"""The step engine on fake rows: step kinds, step ids, the ladder and server-side verdicts."""

from __future__ import annotations

import pytest

from app import steps as engine
from app.steps import (
    CLOSED_KINDS,
    LADDER_PREFIX,
    PRIMARY_PREFIX,
    STEP_KINDS,
    closed_step_correct,
    current_events,
    derive_card_stage,
    rungs_for,
    step_id_matches,
    triage_enabled,
)
from tests.helpers import FAKE_CARD_ID, FAKE_HASH, fake_card, fake_event

H8 = FAKE_HASH[:8]
DISTRACTORS = ["16 July 1969", "20 July 1970", "21 August 1969"]
FULL_LADDER = '["choice", "cloze", "assemble"]'


def test_only_five_step_kinds_exist():
    assert {"triage", "choice", "flash", "cloze", "assemble"} == STEP_KINDS
    assert {"choice", "cloze", "assemble"} == CLOSED_KINDS


# ------------------------------------------------------------ stored card data
def test_rungs_and_triage_come_from_the_stored_card():
    assert rungs_for(fake_card(rungs_json=FULL_LADDER)) == ["choice", "cloze", "assemble"]
    assert rungs_for(fake_card(rungs_json='["cloze"]')) == ["cloze"]
    assert triage_enabled(fake_card(triage_enabled=1)) is True
    assert triage_enabled(fake_card(triage_enabled=0)) is False


def test_cloze_is_not_offered_when_the_card_does_not_climb_it():
    row = fake_card(rungs_json='["choice"]')
    assert engine.step_is_available(row, "choice") is True
    assert engine.step_is_available(row, "cloze") is False
    assert engine.step_is_available(row, "assemble") is False
    assert engine.step_is_available(row, "flash") is True


def test_triage_is_unavailable_when_switched_off():
    assert engine.step_is_available(fake_card(triage_enabled=0), "triage") is False
    assert engine.step_is_available(fake_card(triage_enabled=1), "triage") is True


# ---------------------------------------------------------------- step ids
def test_step_ids_carry_the_check_hash_prefix():
    row = fake_card(
        rungs_json=FULL_LADDER,
        cloze_key="20 July 1969",
        cloze_options_json='["20 July 1969", "16 July 1969", "20 July 1970"]',
        answer="It happened on 20 July 1969 in the evening",
        assemble_eligible=1,
    )
    assert engine.triage_step(row, 0)["id"] == f"triage:{FAKE_CARD_ID}:{H8}:0"
    assert engine.flash_step(row, 2)["id"] == f"flash:{FAKE_CARD_ID}:{H8}:2"
    assert engine.primary_step(row, 1)["id"] == f"p1:choice:{FAKE_CARD_ID}:{H8}:1"
    assert engine.ladder_step(row, "cloze", "event-7", 1)["id"] == f"v2:cloze:{FAKE_CARD_ID}:{H8}:event-7:1"


def test_step_id_matches_only_the_current_card_and_hash():
    row = fake_card(rungs_json='["choice", "assemble"]', assemble_eligible=1)
    assert step_id_matches(f"p1:choice:{FAKE_CARD_ID}:{H8}:0", "choice", row)
    assert step_id_matches(f"v2:assemble:{FAKE_CARD_ID}:{H8}:event-1:0", "assemble", row)
    assert step_id_matches(f"triage:{FAKE_CARD_ID}:{H8}:0", "triage", row)
    assert step_id_matches(f"flash:{FAKE_CARD_ID}:{H8}:3", "flash", row)
    # An older hash, another card, or the kind disagreeing with the id.
    assert not step_id_matches(f"p1:choice:{FAKE_CARD_ID}:deadbeef:0", "choice", row)
    assert not step_id_matches(f"p1:choice:other-card:{H8}:0", "choice", row)
    assert not step_id_matches(f"p1:choice:{FAKE_CARD_ID}:{H8}:0", "assemble", row)
    assert not step_id_matches(f"flash:{FAKE_CARD_ID}:{H8}:0", "triage", row)
    # Ids without the hash segment are not ids this engine issues.
    assert not step_id_matches(f"p1:choice:{FAKE_CARD_ID}:0", "choice", row)
    assert not step_id_matches(f"triage:{FAKE_CARD_ID}:0", "triage", row)
    assert not step_id_matches(None, "choice", row)
    assert not step_id_matches("", "choice", row)


def test_the_primary_check_must_be_the_first_rung():
    row = fake_card(rungs_json='["cloze", "assemble"]', assemble_eligible=1)
    assert step_id_matches(f"p1:cloze:{FAKE_CARD_ID}:{H8}:0", "cloze", row)
    assert not step_id_matches(f"p1:assemble:{FAKE_CARD_ID}:{H8}:0", "assemble", row)
    assert not step_id_matches(f"p1:choice:{FAKE_CARD_ID}:{H8}:0", "choice", row)


def test_a_ladder_step_must_be_one_of_the_rungs():
    row = fake_card(rungs_json='["choice"]')
    assert step_id_matches(f"v2:choice:{FAKE_CARD_ID}:{H8}:event-1:0", "choice", row)
    assert not step_id_matches(f"v2:cloze:{FAKE_CARD_ID}:{H8}:event-1:0", "cloze", row)


def test_triage_step_is_refused_while_triage_is_off():
    row = fake_card(triage_enabled=0)
    assert not step_id_matches(f"triage:{FAKE_CARD_ID}:{H8}:0", "triage", row)


# ---------------------------------------------------------------- choice step
def test_choice_offers_the_option_and_its_three_distractors():
    row = fake_card()
    step = engine.primary_step(row, attempt=0)
    assert step["kind"] == "choice"
    assert step["id"].startswith(PRIMARY_PREFIX)
    assert sorted(step["options"]) == sorted([row["option"], *DISTRACTORS])


def test_choice_order_is_shuffled_deterministically_not_alphabetically():
    row = fake_card()
    first = engine.primary_step(row, attempt=0)["options"]
    again = engine.primary_step(row, attempt=0)["options"]
    other = engine.primary_step(row, attempt=1)["options"]
    assert first == again, "the same step id must rebuild the same order"
    assert first != sorted(first), "alphabetical order leaks the answer on numbers"
    assert sorted(other) == sorted(first)


def test_choice_verdict_comes_from_the_server():
    row = fake_card()
    assert closed_step_correct(row, "choice", row["option"]) is True
    assert closed_step_correct(row, "choice", "16 July 1969") is False
    # Case, accents and spacing are not what is being tested.
    accented = fake_card(option="Crème brûlée")
    assert closed_step_correct(accented, "choice", "  creme   BRULEE ") is True


def test_the_primary_check_is_the_first_rung_of_the_card():
    row = fake_card(
        rungs_json='["cloze", "assemble"]',
        answer="It happened on 20 July 1969 in the evening",
        cloze_key="20 July 1969",
        cloze_options_json='["20 July 1969", "16 July 1969", "20 July 1970"]',
        assemble_eligible=1,
    )
    step = engine.primary_step(row, attempt=0)
    assert step["kind"] == "cloze"
    assert step["id"] == f"p1:cloze:{FAKE_CARD_ID}:{H8}:0"
    assert "prefix" in step and "options" in step


# ----------------------------------------------------------------- cloze step
def test_cloze_cuts_the_key_and_offers_the_stored_options():
    row = fake_card(
        answer="Neil Armstrong was the first person on the Moon.",
        option="Neil Armstrong",
        distractors_json='["Buzz Aldrin", "Michael Collins", "Yuri Gagarin"]',
        rungs_json='["choice", "cloze"]',
        cloze_key="Neil Armstrong",
        cloze_options_json='["Neil Armstrong", "Buzz Aldrin", "Michael Collins", "Yuri Gagarin"]',
    )
    step = engine.ladder_step(row, "cloze", "trigger-1", attempt=0)
    assert step["prefix"] == ""
    assert step["suffix"] == " was the first person on the Moon."
    assert set(step["options"]) == {"Neil Armstrong", "Buzz Aldrin", "Michael Collins", "Yuri Gagarin"}
    assert closed_step_correct(row, "cloze", "neil armstrong") is True
    assert closed_step_correct(row, "cloze", "Buzz Aldrin") is False


def test_cloze_options_are_only_the_stored_ones():
    """Lures of a different length than the gap are not offered (the stored list excludes them)."""
    row = fake_card(
        answer="The crew landed in the Sea of Tranquility",
        rungs_json='["choice", "cloze"]',
        cloze_key="Tranquility",
        cloze_options_json='["Tranquility", "Serenity", "Storms"]',
    )
    step = engine.ladder_step(row, "cloze", "trigger-1", attempt=0)
    assert sorted(step["options"]) == ["Serenity", "Storms", "Tranquility"]
    assert step["prefix"] == "The crew landed in the Sea of "
    assert step["suffix"] == ""


# -------------------------------------------------------------- assemble step
def test_assemble_shuffles_every_word_and_adds_no_extras():
    row = fake_card(
        answer="The naïve café owner smiled warmly", assemble_eligible=1, rungs_json='["choice", "assemble"]'
    )
    step = engine.ladder_step(row, "assemble", "trigger-1", attempt=0)
    assert sorted(step["tiles"]) == sorted(row["answer"].split())
    assert step["tiles"] != row["answer"].split()
    assert closed_step_correct(row, "assemble", "The naïve café owner smiled warmly") is True
    assert closed_step_correct(row, "assemble", "The café naïve owner smiled warmly") is False


def test_assemble_accepts_the_answer_without_accents():
    row = fake_card(answer="The naïve café owner smiled warmly", assemble_eligible=1)
    assert closed_step_correct(row, "assemble", "the naive cafe owner smiled warmly") is True


def test_triage_and_flash_have_no_closed_verdict():
    with pytest.raises(ValueError):
        closed_step_correct(fake_card(), "triage", "know")


# --------------------------------------------------------------- the ladder
def miss(order: int = 1, event_id: str = "e1"):
    return fake_event(
        id=event_id,
        kind="choice",
        rating="again",
        answer="16 July 1969",
        step_id=f"p1:choice:{FAKE_CARD_ID}:{H8}:0",
        accepted_order=order,
        correct=0,
    )


def rung(kind: str, answer: str, order: int, attempt: int = 0, **overrides):
    return fake_event(
        id=f"r{order}",
        kind=kind,
        answer=answer,
        accepted_order=order,
        step_id=f"{LADDER_PREFIX}{kind}:{FAKE_CARD_ID}:{H8}:e1:{attempt}",
        **overrides,
    )


def ladder_card(**overrides):
    fields = {
        "rungs_json": FULL_LADDER,
        "answer": "It happened on 20 July 1969 in summer",
        "cloze_key": "20 July 1969",
        "cloze_options_json": '["20 July 1969", "16 July 1969", "20 July 1970"]',
        "assemble_eligible": 1,
    }
    return fake_card(**{**fields, **overrides})


def test_a_correct_primary_check_leaves_the_ladder_closed():
    row = fake_card()
    events = [
        fake_event(
            kind="choice",
            rating="good",
            answer=row["option"],
            accepted_order=1,
            step_id=f"p1:choice:{FAKE_CARD_ID}:{H8}:0",
        )
    ]
    assert derive_card_stage(row, events).stage == "closed"


def test_a_missed_check_opens_the_ladder_at_the_first_rung():
    row = fake_card()
    state = derive_card_stage(row, [miss()])
    assert state.stage == "needs_choice"
    assert state.trigger_event_id == "e1"


def test_the_ladder_climbs_every_rung_in_order():
    row = ladder_card()
    events = [miss()]
    assert derive_card_stage(row, events).stage == "needs_choice"
    events.append(rung("choice", row["option"], 2))
    assert derive_card_stage(row, events).stage == "needs_cloze"
    events.append(rung("cloze", "20 July 1969", 3))
    assert derive_card_stage(row, events).stage == "needs_assemble"
    events.append(rung("assemble", row["answer"], 4))
    assert derive_card_stage(row, events).stage == "closed"


def test_the_ladder_skips_a_kind_the_card_does_not_climb():
    row = ladder_card(rungs_json='["choice", "assemble"]')
    events = [miss(), rung("choice", row["option"], 2)]
    assert derive_card_stage(row, events).stage == "needs_assemble"


def test_the_ladder_starts_at_whatever_rung_comes_first():
    row = ladder_card(rungs_json='["cloze", "assemble"]')
    state = derive_card_stage(row, [miss()])
    assert state.stage == "needs_cloze"


def test_the_ladder_closes_after_choice_when_nothing_else_is_climbed():
    row = fake_card(rungs_json='["choice"]')
    events = [miss(), rung("choice", row["option"], 2)]
    assert derive_card_stage(row, events).stage == "closed"


def test_a_rung_gets_one_retry_then_the_ladder_gives_up():
    row = fake_card()
    events = [miss(), rung("choice", "16 July 1969", 2)]
    assert derive_card_stage(row, events).stage == "needs_choice_retry"
    assert derive_card_stage(row, events).attempt == 1
    events.append(rung("choice", "16 July 1969", 3, attempt=1))
    assert derive_card_stage(row, events).stage == "closed"


def test_events_under_an_older_hash_are_ignored_by_the_ladder():
    row = fake_card()
    old_miss = fake_event(**{**miss(), "check_hash": "f" * 64})
    assert derive_card_stage(row, [old_miss]).stage == "closed"
    assert current_events(row, [old_miss, miss(order=2)]) == [miss(order=2)]


# ------------------------------------------------- the client is never trusted
def test_a_forged_correct_flag_does_not_close_the_ladder():
    """A client claiming `correct: true` on a wrong answer must not advance."""
    row = fake_card()
    forged = rung("choice", "16 July 1969", 2, correct=1)
    state = derive_card_stage(row, [miss(), forged])
    assert state.stage == "needs_choice_retry", "the answer text is what counts, not the flag"


def test_server_recomputes_the_verdict_from_the_answer_text():
    row = fake_card()
    assert closed_step_correct(row, "choice", "21 August 1969") is False


# -------------------------------------------------------------- readiness
def test_readiness_counts_only_the_last_primary_check():
    """A correct ladder rung is scaffolding, not evidence the card is known.

    A missed primary followed by a correct rung does *not* make the card ready;
    only a fresh, correct primary check does.
    """
    row = fake_card()
    primary = f"p1:choice:{FAKE_CARD_ID}:{H8}"
    missed_then_scaffolded = [
        fake_event(kind="choice", correct=0, accepted_order=1, step_id=f"{primary}:0", answer="16 July 1969"),
        rung("choice", row["option"], 2),
    ]
    assert engine.card_is_ready(row, missed_then_scaffolded) is False

    missed_then_reprobed = [
        *missed_then_scaffolded,
        fake_event(kind="choice", correct=1, accepted_order=3, step_id=f"{primary}:1", answer=row["option"]),
    ]
    assert engine.card_is_ready(row, missed_then_reprobed) is True

    correct_then_wrong_rung = [
        fake_event(kind="choice", correct=1, accepted_order=1, step_id=f"{primary}:0", answer=row["option"]),
        rung("choice", "16 July 1969", 2),
    ]
    # A rung only ever follows a *missed* primary, so this shape is not
    # reachable in practice, but the rule must ignore rungs either way.
    assert engine.card_is_ready(row, correct_then_wrong_rung) is True

    never_probed = [rung("choice", row["option"], 1)]
    assert engine.card_is_ready(row, never_probed) is False


def test_triage_and_flash_do_not_make_a_card_ready():
    row = fake_card()
    events = [
        fake_event(
            kind="triage",
            rating="good",
            answer="know",
            accepted_order=1,
            step_id=f"triage:{FAKE_CARD_ID}:{H8}:0",
        ),
        fake_event(
            kind="flash",
            rating="good",
            answer="remembered",
            accepted_order=2,
            step_id=f"flash:{FAKE_CARD_ID}:{H8}:0",
        ),
    ]
    assert engine.card_is_ready(row, events) is False


def test_a_primary_check_under_an_older_hash_does_not_count():
    row = fake_card()
    old = fake_event(
        kind="choice",
        accepted_order=1,
        answer=row["option"],
        check_hash="f" * 64,
        step_id=f"p1:choice:{FAKE_CARD_ID}:ffffffff:0",
    )
    assert engine.card_is_ready(row, [old]) is False
    assert engine.card_probed(row, [old]) is False
