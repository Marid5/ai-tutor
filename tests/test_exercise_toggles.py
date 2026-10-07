"""Exercise toggles and the content lifecycle, driven through the engine on a real database."""

from __future__ import annotations

import pytest

from app import curriculum, session
from app.database import Database
from tests.helpers import (
    Learner,
    fake_now,
    first_of,
    lesson_ref,
    make_user,
    right_answer,
    submit,
    wrong_answer,
)

CARD = "capital-of-france"
OTHER = "largest-planet"


# ----------------------------------------------------------- which primary
def test_primary_is_choice_by_default(db, tmp_path):
    learner = Learner(db, tmp_path)
    primary = first_of(learner.start(), CARD, "p1:")
    assert primary["kind"] == "choice"
    assert sorted(primary["options"]) == ["Lyon", "Marseille", "Nice", "Paris"]


def test_choice_disabled_primary_becomes_cloze(db, tmp_path):
    learner = Learner(db, tmp_path, exercises={"choice": False})
    primary = first_of(learner.start(), CARD, "p1:")
    assert primary["kind"] == "cloze"
    assert primary["prefix"] == ""
    assert primary["suffix"] == " is the capital of France"
    assert sorted(primary["options"]) == ["Lyon", "Marseille", "Nice", "Paris"]


def test_only_assemble_enabled_primary_is_assemble(db, tmp_path):
    learner = Learner(db, tmp_path, exercises={"choice": False, "cloze": False, "assemble": True})
    primary = first_of(learner.start(), CARD, "p1:")
    assert primary["kind"] == "assemble"
    assert sorted(primary["tiles"]) == sorted(["Paris", "is", "the", "capital", "of", "France"])
    assert learner.answer(primary).correct is True


def test_chapter_override_changes_primary(db, tmp_path):
    learner = Learner(db, tmp_path, chapter_overrides={"choice": False})
    assert first_of(learner.start(), CARD, "p1:")["kind"] == "cloze"


# ---------------------------------------------------------------- triage
def test_triage_disabled_skips_triage(db, tmp_path):
    learner = Learner(db, tmp_path, exercises={"triage": False})
    queue = learner.start()
    assert queue, "a new card starts with its primary check"
    assert all(step["kind"] != "triage" for step in queue)
    assert queue[0]["id"].startswith("p1:")


def test_triage_event_when_disabled_is_stale(db, tmp_path):
    learner = Learner(db, tmp_path)
    triage = first_of(learner.start(), CARD, "triage:")
    learner.reload(exercises={"triage": False})
    outcome = learner.answer(triage)
    assert outcome.status == "stale"
    assert db.scalar("SELECT count(*) FROM events") == 0


# ------------------------------------------------------------- the ladder
@pytest.mark.parametrize(
    "exercises",
    [
        {},
        {"triage": False},
        {"choice": False},
        {"choice": False, "cloze": False, "assemble": True},
        {"triage": False, "assemble": True},
    ],
)
def test_flash_follows_missed_primary_with_any_toggles(db, tmp_path, exercises):
    learner = Learner(db, tmp_path, exercises=exercises)
    queue = learner.start()
    while not queue[0]["id"].startswith("p1:"):
        assert learner.answer(queue[0]).status == "inserted"
        queue = learner.steps()
    primary = queue[0]
    assert learner.answer(primary, right=False).correct is False

    queue = learner.steps()
    card_steps = [step for step in queue if step["card_id"] == primary["card_id"]]
    assert card_steps[0]["kind"] == "flash", "the missed card is read again first"
    assert any(step["id"].startswith("v2:") for step in card_steps), "and the ladder opens"
    flash_at = queue.index(card_steps[0])
    rung_at = queue.index(next(step for step in card_steps if step["id"].startswith("v2:")))
    assert flash_at < rung_at


def test_ladder_runs_all_enabled_rungs_in_order(db, tmp_path):
    learner = Learner(db, tmp_path, exercises={"assemble": True})
    assert session.rungs_for(learner.row(CARD)) == ["choice", "cloze", "assemble"]
    learner.start()
    served = learner.drain(miss={CARD})
    ladder = [step["kind"] for step in served if step["card_id"] == CARD and step["id"].startswith("v2:")]
    assert ladder == ["choice", "cloze", "assemble"]
    assert db.user_lesson_state(learner.user, "first")["status"] == "completed"


def test_second_miss_on_rung_closes_ladder(db, tmp_path):
    learner = Learner(db, tmp_path, exercises={"assemble": True})
    learner.start()
    for _ in range(20):
        step = learner.steps()[0]
        wrong = step["card_id"] == CARD and step["id"].startswith(("p1:", "v2:"))
        learner.answer(step, right=not wrong)
        events = db.events_for_session(learner.user, "first")
        if sum(1 for event in events if (event["step_id"] or "").startswith("v2:")) == 2:
            break
    row = learner.row(CARD)
    events = [event for event in db.events_for_session(learner.user, "first") if event["card_id"] == CARD]
    assert session.derive_card_stage(row, events).stage == "closed"
    assert not any(step["id"].startswith("v2:") for step in learner.steps() if step["card_id"] == CARD)
    rung_kinds = [event["kind"] for event in events if (event["step_id"] or "").startswith("v2:")]
    assert rung_kinds == ["choice", "choice"], "no later rung opens after the ladder closes"


# ------------------------------------------------------- content lifecycle
def test_step_with_old_hash_is_stale(db, tmp_path):
    learner = Learner(db, tmp_path)
    primary = first_of(learner.start(), CARD, "p1:")
    learner.reload(cards={CARD: {"answer": "Paris is the capital city of France"}})
    outcome = learner.answer(primary)
    assert outcome.status == "stale"
    fresh = first_of(learner.steps(), CARD, "p1:")
    assert fresh["id"] != primary["id"]
    assert learner.answer(fresh).status == "inserted"


def test_reset_card_reappears_in_lesson_as_new(db, tmp_path):
    learner = Learner(db, tmp_path)
    learner.start()
    learner.drain()
    assert learner.steps() == []
    learner.reload(cards={CARD: {"distractors": ["Lyon", "Marseille", "Toulouse"]}})
    queue = learner.steps()
    assert {step["card_id"] for step in queue} == {CARD}
    assert [step["kind"] for step in queue] == ["triage", "choice"]
    assert queue[1]["id"].startswith("p1:")


def test_lesson_completes_after_answer_edit_mid_history(db, tmp_path):
    learner = Learner(db, tmp_path)
    queue = learner.start()
    for step in queue[:3]:  # both triages and the first primary check
        learner.answer(step)
    assert db.user_lesson_state(learner.user, "first")["status"] == "in_progress"
    learner.reload(cards={CARD: {"answer": "Paris is the capital city of France"}})
    learner.drain()
    state = db.user_lesson_state(learner.user, "first")
    assert state["status"] == "completed"
    assert state["accuracy"] == 1.0


def test_old_events_do_not_regrade_after_answer_edit(db, tmp_path):
    learner = Learner(db, tmp_path, exercises={"triage": False})
    primary = first_of(learner.start(), CARD, "p1:")
    assert learner.answer(primary, right=False).correct is False  # answered "Lyon" or another lure
    wrong = db.fetch_cards("SELECT answer FROM events WHERE step_id=?", (primary["id"],))[0]["answer"]
    # The lure the learner picked becomes the right answer of the edited card.
    others = [lure for lure in ["Paris", "Lyon", "Marseille", "Nice"] if lure != wrong]
    learner.reload(
        cards={CARD: {"option": wrong, "answer": f"{wrong} is the capital of France", "distractors": others}}
    )
    row = learner.row(CARD)
    events = [event for event in db.events_for_session(learner.user, "first") if event["card_id"] == CARD]
    assert session.card_is_ready(row, events) is False, "the old miss is not regraded as a pass"
    assert session.card_probed(row, events) is False
    assert session.derive_card_stage(row, events).stage == "closed"
    assert curriculum.readiness(db, learner.user)["ready"] == 0


def test_queue_rebuilds_identically_after_reload(db, tmp_path):
    learner = Learner(db, tmp_path)
    learner.start()
    for step in learner.steps()[:3]:
        learner.answer(step, right=not step["id"].startswith("p1:"))
    before = learner.steps()
    reopened = Database(db.path)
    assert session.lesson_steps(reopened, learner.user, reopened.lesson("first")) == before
    learner.reload()  # an unchanged course loaded again changes nothing
    assert learner.steps() == before


def test_card_is_ready_uses_primary_only(db, tmp_path):
    learner = Learner(db, tmp_path, exercises={"triage": False})
    learner.start()
    learner.drain(miss={CARD})
    row = learner.row(CARD)
    events = [event for event in db.events_for_session(learner.user, "first") if event["card_id"] == CARD]
    assert any(event["step_id"].startswith("v2:") and event["correct"] for event in events)
    assert session.card_is_ready(row, events) is False
    other_events = [
        event for event in db.events_for_session(learner.user, "first") if event["card_id"] == OTHER
    ]
    assert session.card_is_ready(learner.row(OTHER), other_events) is True
    assert curriculum.readiness(db, learner.user) == {"ready": 1, "total": 3}


def test_server_ignores_client_correct_flag(db, tmp_path):
    learner = Learner(db, tmp_path)
    primary = first_of(learner.start(), CARD, "p1:")
    row = learner.row(CARD)
    outcome = submit(
        db, learner.user, learner.program, "first", primary, wrong_answer(row, primary), correct=True
    )
    assert outcome.status == "inserted"
    assert outcome.correct is False
    stored = db.fetch_cards("SELECT rating, correct FROM events WHERE step_id=?", (primary["id"],))[0]
    assert stored["rating"] == "again"
    assert stored["correct"] == 0


# --------------------------------------------------------------- home view
def test_no_lesson_gating_next_lesson_resumes_in_progress(db, tmp_path):
    learner = Learner(db, tmp_path)
    view = learner.view()
    lessons = [lesson for chapter in view["chapters"] for lesson in chapter["lessons"]]
    assert [lesson["status"] for lesson in lessons] == ["available", "available"]
    assert all(lesson["has_open_work"] for lesson in lessons)
    assert view["next_lesson"]["id"] == "first"
    assert view["next_lesson"]["action"] == "start"

    learner.start("second")
    view = learner.view()
    assert view["next_lesson"]["id"] == "second"
    assert view["next_lesson"]["action"] == "continue"
    assert lesson_ref(view, "second")["is_in_progress"] is True

    learner.drain("second")
    view = learner.view()
    assert lesson_ref(view, "second")["status"] == "completed"
    assert lesson_ref(view, "second")["has_open_work"] is False
    assert view["next_lesson"]["id"] == "first"


def test_answer_edit_marks_completed_lesson_open_work(db, tmp_path):
    learner = Learner(db, tmp_path)
    learner.start()
    learner.drain()
    learner.start("second")
    learner.drain("second")
    view = learner.view()
    assert lesson_ref(view, "first")["has_open_work"] is False
    assert view["next_lesson"] is None

    learner.reload(cards={CARD: {"answer": "Paris is the capital city of France"}})
    view = learner.view()
    first = lesson_ref(view, "first")
    assert first["status"] == "completed", "completion is history, not rewritten"
    assert first["has_open_work"] is True
    assert view["next_lesson"]["id"] == "first"
    assert view["cards_ready"] == 2


# ----------------------------------------------------- answer validation
def test_malformed_answers_are_invalid_with_a_reason(db, tmp_path):
    learner = Learner(db, tmp_path)
    triage = first_of(learner.start(), CARD, "triage:")
    assert submit(db, learner.user, learner.program, "nope", triage, "know").status == "invalid"
    bad = submit(db, learner.user, learner.program, "first", triage, "maybe")
    assert bad.status == "invalid" and bad.detail
    event = {
        "id": "e-1",
        "session_id": "first",
        "card_id": CARD,
        "kind": "triage",
        "step_id": triage["id"],
        "answer": "know",
        "elapsed_ms": 10 * 60 * 1000,
    }
    late = session.record_answer(db, learner.user, event, program=learner.program, now=fake_now())
    assert late.status == "invalid" and "elapsed_ms" in late.detail
    assert db.scalar("SELECT count(*) FROM events") == 0


def test_retired_card_and_retired_lesson_answers_are_stale(db, tmp_path):
    learner = Learner(db, tmp_path)
    triage = first_of(learner.start(), CARD, "triage:")
    with db._transaction() as conn:
        conn.execute("UPDATE cards SET retired=1 WHERE id=?", (CARD,))
    assert learner.answer(triage).status == "stale"
    other = first_of(learner.steps(), OTHER, "triage:")
    with db._transaction() as conn:
        conn.execute("UPDATE lessons SET retired=1 WHERE id='first'")
    assert learner.answer(other).status == "stale"


def test_replayed_step_is_a_duplicate_and_counts_once(db, tmp_path):
    learner = Learner(db, tmp_path)
    triage = first_of(learner.start(), CARD, "triage:")
    assert learner.answer(triage, event_id="a").status == "inserted"
    assert learner.answer(triage, event_id="a").status == "duplicate_event"
    assert learner.answer(triage, event_id="b").status == "duplicate_step"
    assert db.scalar("SELECT count(*) FROM events") == 1


def test_reverted_answer_edit_starts_over(db, tmp_path):
    learner = Learner(db, tmp_path)
    learner.start()
    learner.drain()
    learner.reload(cards={CARD: {"answer": "Paris is the capital city of France"}})
    learner.reload()  # the edit is undone: the same answer text as at first
    queue = learner.steps()
    assert [step["kind"] for step in queue if step["card_id"] == CARD] == ["triage", "choice"]
    row = learner.row(CARD)
    events = [event for event in db.events_for_session(learner.user, "first") if event["card_id"] == CARD]
    assert session.card_is_ready(row, events) is False, "answers to the first version do not count again"
    view = learner.view()
    assert lesson_ref(view, "first")["has_open_work"] is True
    assert view["cards_ready"] == 1
    learner.drain()
    events = [event for event in db.events_for_session(learner.user, "first") if event["card_id"] == CARD]
    assert session.card_is_ready(learner.row(CARD), events) is True
    assert learner.steps() == []


@pytest.mark.parametrize(
    "bad_id",
    ["x" * 300, {"nested": "id"}, "", "has space", "colon:inside", 7],
    ids=["300-chars", "dict", "empty", "space", "colon", "int"],
)
def test_bad_event_id_is_invalid_and_the_lesson_keeps_working(db, tmp_path, bad_id):
    learner = Learner(db, tmp_path, exercises={"triage": False})
    primary = learner.start()[0]
    row = learner.row(primary["card_id"])
    event = {
        "id": bad_id,
        "session_id": "first",
        "card_id": primary["card_id"],
        "kind": primary["kind"],
        "step_id": primary["id"],
        "answer": wrong_answer(row, primary),
        "elapsed_ms": 1,
    }
    outcome = session.record_answer(db, learner.user, event, program=learner.program, now=fake_now())
    assert outcome.status == "invalid" and "id" in outcome.detail
    assert db.scalar("SELECT count(*) FROM events") == 0
    learner.drain(miss={primary["card_id"]})
    assert db.user_lesson_state(learner.user, "first")["status"] == "completed"


def test_client_fields_are_type_and_size_checked(db, tmp_path):
    learner = Learner(db, tmp_path, exercises={"triage": False})
    primary = learner.start()[0]
    base = {
        "session_id": "first",
        "card_id": primary["card_id"],
        "kind": primary["kind"],
        "step_id": primary["id"],
        "answer": "Paris",
        "elapsed_ms": 1,
    }
    for broken in (
        {"answer": "x" * 501},
        {"answer": ["Paris"]},
        {"ts": "yesterday"},
        {"ts": "2026-10-07T10:00:00"},
        {"elapsed_ms": True},
        {"timing_version": "2"},
        {"timing_version": -1},
        {"timing_version": 1001},
        {"timing_version": 2**70},
        {"ts": "0001-01-01T00:00:00+05:00"},  # leaves the datetime range when moved to UTC
        {"answer": "\ud800"},
        {"session_id": "first\udfff"},
    ):
        outcome = session.record_answer(
            db, learner.user, {**base, **broken}, program=learner.program, now=fake_now()
        )
        assert outcome.status == "invalid", broken
    assert db.scalar("SELECT count(*) FROM events") == 0


def test_naive_now_is_refused_before_any_write(db, tmp_path):
    learner = Learner(db, tmp_path)
    triage = first_of(learner.start(), CARD, "triage:")
    naive = fake_now().replace(tzinfo=None)
    with pytest.raises(ValueError):
        submit(db, learner.user, learner.program, "first", triage, "know", now=naive)
    with pytest.raises(ValueError):
        curriculum.chapters_view(db, learner.user, learner.program, naive)
    with pytest.raises(ValueError):
        session.open_review_session(db, learner.user, naive, learner.program.schedule)
    assert db.scalar("SELECT count(*) FROM events") == 0
    assert db.scalar("SELECT count(*) FROM user_meta") == 0


def test_primary_with_an_unissued_attempt_is_stale(db, tmp_path):
    learner = Learner(db, tmp_path, exercises={"triage": False})
    primary = first_of(learner.start(), CARD, "p1:")
    assert primary["id"].endswith(":0")
    minted = {**primary, "id": primary["id"][:-1] + "1"}
    assert learner.answer(minted).status == "stale"
    for forged in ("00", "\u0660", "+0"):  # other spellings of zero are not the issued id
        assert learner.answer({**primary, "id": primary["id"][:-1] + forged}).status == "stale"
    assert learner.answer(primary, event_id="first-answer").status == "inserted"
    # Replays of the accepted answer stay duplicates, not stale.
    assert learner.answer(primary, event_id="first-answer").status == "duplicate_event"
    assert learner.answer(primary, event_id="second-try").status == "duplicate_step"
    # Once checked, no further primary check of the card is accepted in this lesson.
    assert learner.answer(minted).status == "stale"


def test_a_future_timestamp_is_taken_as_now(db, tmp_path):
    learner = Learner(db, tmp_path, exercises={"triage": False})
    primary = learner.start()[0]
    event = {
        "session_id": "first",
        "card_id": primary["card_id"],
        "kind": primary["kind"],
        "step_id": primary["id"],
        "answer": "Paris",
        "elapsed_ms": 1,
        "ts": "2099-01-01T00:00:00+00:00",
    }
    outcome = session.record_answer(db, learner.user, event, program=learner.program, now=fake_now())
    assert outcome.status == "inserted"
    assert db.scalar("SELECT ts FROM events") == fake_now().isoformat()


def test_unknown_kind_is_quoted_and_bounded_in_the_detail(db, tmp_path):
    learner = Learner(db, tmp_path)
    triage = first_of(learner.start(), CARD, "triage:")
    kind = "evil\nkind" + "x" * 200
    outcome = learner.answer({**triage, "kind": kind})
    assert outcome.status == "invalid"
    assert "\n" not in outcome.detail
    assert "'evil\\nkind" in outcome.detail
    assert len(outcome.detail) < 100


def test_event_ids_are_scoped_to_their_learner(db, tmp_path):
    learner = Learner(db, tmp_path, exercises={"triage": False})
    other = make_user(db, "other-learner")
    primary = learner.start()[0]
    db.start_lesson(other, "first")
    assert learner.answer(primary, event_id="shared-id").status == "inserted"
    row = learner.row(primary["card_id"])
    outcome = submit(
        db, other, learner.program, "first", primary, right_answer(row, primary), event_id="shared-id"
    )
    assert outcome.status == "inserted", "another learner's event id is not a duplicate"
    assert learner.answer(primary, event_id="shared-id").status == "duplicate_event"
    assert db.scalar("SELECT count(*) FROM events WHERE id='shared-id'") == 2
