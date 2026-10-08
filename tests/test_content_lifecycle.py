"""Loading a program into the database: edits, removals and returns of content."""

import copy
import sqlite3
from datetime import UTC, datetime

import pytest

from app.content import Program
from app.database import Database, StoredCard

CARD = "capital-of-france"
OTHER_CARD = "powerhouse-organelle"
ZERO = {"added": 0, "updated": 0, "reset": 0, "retired": 0, "restored": 0}


def clone(program: Program) -> Program:
    return copy.deepcopy(program)


def card_of(program: Program, card_id: str):
    for chapter in program.chapters:
        for lesson in chapter.lessons:
            for card in lesson.cards:
                if card.id == card_id:
                    return card
    raise KeyError(card_id)


def drop_card(program: Program, card_id: str) -> Program:
    edited = clone(program)
    for lesson in edited.chapters[0].lessons:
        lesson.cards = [card for card in lesson.cards if card.id != card_id]
    return edited


def scheduled(db: Database, user_id: str, card_id: str, reps: int = 3) -> None:
    now = datetime.now(UTC).isoformat()
    db.update_state(
        user_id,
        card_id,
        StoredCard(
            due=now, stability=4.0, difficulty=5.0, reps=reps, lapses=0, state="review", last_review=now
        ),
    )


def answered(db: Database, user_id: str, card_id: str, event_id: str) -> None:
    db.record_event(
        user_id,
        {
            "id": event_id,
            "ts": datetime.now(UTC).isoformat(),
            "session_id": "first",
            "card_id": card_id,
            "kind": "choice",
            "answer": "Paris",
            "rating": "good",
            "elapsed_ms": 900,
            "step_id": event_id,
            "correct": 1,
            "check_version": db.card(card_id)["check_version"],
        },
    )


def state_rows(db: Database, card_id: str) -> int:
    return db.scalar("SELECT count(*) FROM card_state WHERE card_id=?", (card_id,))


def test_first_upsert_adds_everything(db: Database, program_minimal: Program):
    result = db.upsert_program(program_minimal)

    assert result == {**ZERO, "added": 4}
    assert [chapter["id"] for chapter in db.chapters()] == ["basics"]
    assert [lesson["id"] for lesson in db.lessons_of_chapter("basics")] == ["first", "second"]
    assert [card["id"] for card in db.chapter_cards("basics")] == [
        "capital-of-france",
        "powerhouse-organelle",
        "light-versus-sound",
        "primary-colors",
    ]


def test_upsert_stores_derived_card_data(seeded_db: Database, program_minimal: Program):
    capital = seeded_db.card(CARD)
    source = card_of(program_minimal, CARD)
    assert capital["check_hash"] == source.check_hash()
    assert capital["option"] == "Paris"
    assert capital["triage_enabled"] == 1
    assert capital["assemble_eligible"] == 1
    assert capital["cloze_key"] == "Paris"
    assert capital["cloze_options_json"] == '["Paris", "Lyon", "Marseille", "Nice"]'
    assert capital["rungs_json"] == '["choice", "cloze", "assemble"]'
    # A card with no material of its own still gets its derived defaults stored.
    flash_only = seeded_db.card("light-versus-sound")
    assert flash_only["option"] == "Light travels faster than sound"
    assert flash_only["rungs_json"] == '["assemble"]'
    assert flash_only["distractors_json"] == "[]"


def test_upsert_is_idempotent(seeded_db: Database, program_minimal: Program):
    before = [dict(row) for row in seeded_db.fetch_all("SELECT * FROM cards ORDER BY id")]

    assert seeded_db.upsert_program(program_minimal) == ZERO
    assert seeded_db.upsert_program(program_minimal) == ZERO

    assert [dict(row) for row in seeded_db.fetch_all("SELECT * FROM cards ORDER BY id")] == before
    assert seeded_db.scalar("SELECT count(*) FROM chapters") == 1
    assert seeded_db.scalar("SELECT count(*) FROM lessons") == 2


def test_prompt_edit_keeps_schedule(seeded_db: Database, program_minimal: Program):
    user = seeded_db.create_user("learner", "hash")
    scheduled(seeded_db, user, CARD)
    answered(seeded_db, user, CARD, "e1")

    edited = clone(program_minimal)
    changed = card_of(edited, CARD)
    changed.prompt = "Which city is the capital of France?"
    changed.prompt_variants = ["Name the capital of France."]
    changed.hint = "Think of the Eiffel Tower."
    changed.note = "Paris has been the capital for centuries."
    changed.tags = ["geography", "europe"]
    changed.source = "An atlas"
    result = seeded_db.upsert_program(edited)

    assert result == {**ZERO, "updated": 1}
    row = seeded_db.card(CARD)
    assert row["prompt"] == "Which city is the capital of France?"
    assert row["hint"] == "Think of the Eiffel Tower."
    assert row["tags_json"] == '["geography", "europe"]'
    assert state_rows(seeded_db, CARD) == 1
    assert seeded_db.get_state(user, CARD).reps == 3
    assert seeded_db.scalar("SELECT count(*) FROM events WHERE user_id=?", (user,)) == 1


def test_title_and_order_edits_keep_progress(seeded_db: Database, program_minimal: Program):
    user = seeded_db.create_user("learner", "hash")
    scheduled(seeded_db, user, CARD)
    seeded_db.complete_lesson(user, "first", 1.0)

    edited = clone(program_minimal)
    edited.chapters[0].title = "Fundamentals"
    edited.chapters[0].lessons.reverse()
    seeded_db.upsert_program(edited)

    assert seeded_db.chapters()[0]["title"] == "Fundamentals"
    assert [lesson["id"] for lesson in seeded_db.lessons_of_chapter("basics")] == ["second", "first"]
    assert state_rows(seeded_db, CARD) == 1
    assert seeded_db.user_lesson_state(user, "first")["status"] == "completed"


def test_answer_edit_resets_schedule_for_all_users_keeps_events(
    seeded_db: Database, program_minimal: Program
):
    first = seeded_db.create_user("first", "hash")
    second = seeded_db.create_user("second", "hash")
    old_hash = seeded_db.card(CARD)["check_hash"]
    old_version = seeded_db.card(CARD)["check_version"]
    for user in (first, second):
        scheduled(seeded_db, user, CARD)
        scheduled(seeded_db, user, OTHER_CARD)
        answered(seeded_db, user, CARD, f"event-{user}")
    seeded_db.complete_lesson(first, "first", 1.0)

    edited = clone(program_minimal)
    card_of(edited, CARD).answer = "The capital city of France is Paris, on the Seine."
    result = seeded_db.upsert_program(edited)

    assert result["reset"] == 1
    assert state_rows(seeded_db, CARD) == 0
    assert state_rows(seeded_db, OTHER_CARD) == 2
    assert seeded_db.card(CARD)["check_hash"] != old_hash
    assert seeded_db.card(CARD)["check_version"] != old_version
    # History stays, still tagged with the version it was answered against.
    stored = seeded_db.fetch_all("SELECT check_version FROM events WHERE card_id=?", (CARD,))
    assert len(stored) == 2
    assert {row["check_version"] for row in stored} == {old_version}
    # Completion is left alone; "open work" is derived later from the events.
    assert seeded_db.user_lesson_state(first, "first")["status"] == "completed"


def test_distractor_edit_resets_schedule(seeded_db: Database, program_minimal: Program):
    user = seeded_db.create_user("learner", "hash")
    scheduled(seeded_db, user, CARD)

    edited = clone(program_minimal)
    card_of(edited, CARD).distractors = ["Lyon", "Marseille", "Toulouse"]
    result = seeded_db.upsert_program(edited)

    assert result["reset"] == 1
    assert state_rows(seeded_db, CARD) == 0


def test_reordering_distractors_does_not_reset(seeded_db: Database, program_minimal: Program):
    user = seeded_db.create_user("learner", "hash")
    scheduled(seeded_db, user, CARD)

    edited = clone(program_minimal)
    card_of(edited, CARD).distractors = ["Nice", "Lyon", "Marseille"]
    result = seeded_db.upsert_program(edited)

    assert result["reset"] == 0
    assert state_rows(seeded_db, CARD) == 1


def test_exercise_change_updates_rungs_and_triage_without_reset(
    seeded_db: Database, program_minimal: Program
):
    user = seeded_db.create_user("learner", "hash")
    scheduled(seeded_db, user, CARD)

    edited = clone(program_minimal)
    edited.chapters[0].exercises.triage = False
    edited.chapters[0].exercises.cloze = False
    result = seeded_db.upsert_program(edited)

    assert result["reset"] == 0
    row = seeded_db.card(CARD)
    assert row["rungs_json"] == '["choice", "assemble"]'
    assert row["triage_enabled"] == 0
    assert state_rows(seeded_db, CARD) == 1
    assert '"triage": false' in seeded_db.chapters()[0]["exercises_json"]


def test_removed_card_is_retired_with_history(seeded_db: Database, program_minimal: Program):
    user = seeded_db.create_user("learner", "hash")
    scheduled(seeded_db, user, "light-versus-sound")
    answered(seeded_db, user, "light-versus-sound", "e1")

    result = seeded_db.upsert_program(drop_card(program_minimal, "light-versus-sound"))

    assert result == {**ZERO, "retired": 1}
    assert seeded_db.card("light-versus-sound")["retired"] == 1
    assert state_rows(seeded_db, "light-versus-sound") == 1
    assert seeded_db.scalar("SELECT count(*) FROM events WHERE card_id='light-versus-sound'") == 1
    # Retiring is reported once, not on every later start.
    assert seeded_db.upsert_program(drop_card(program_minimal, "light-versus-sound")) == ZERO


def test_returning_card_is_restored_with_progress(seeded_db: Database, program_minimal: Program):
    user = seeded_db.create_user("learner", "hash")
    scheduled(seeded_db, user, "light-versus-sound")
    seeded_db.upsert_program(drop_card(program_minimal, "light-versus-sound"))

    result = seeded_db.upsert_program(program_minimal)

    assert result == {**ZERO, "restored": 1}
    assert seeded_db.card("light-versus-sound")["retired"] == 0
    assert seeded_db.get_state(user, "light-versus-sound").reps == 3
    assert "light-versus-sound" in [card["id"] for card in seeded_db.lesson_cards("second")]


def test_returning_card_with_a_changed_answer_is_reset(seeded_db: Database, program_minimal: Program):
    user = seeded_db.create_user("learner", "hash")
    scheduled(seeded_db, user, "light-versus-sound")
    seeded_db.upsert_program(drop_card(program_minimal, "light-versus-sound"))

    edited = clone(program_minimal)
    card_of(edited, "light-versus-sound").answer = "Light is faster than sound"
    result = seeded_db.upsert_program(edited)

    assert result["restored"] == 1
    assert result["reset"] == 1
    assert state_rows(seeded_db, "light-versus-sound") == 0


def test_removed_lesson_and_chapter_are_retired(seeded_db: Database, program_minimal: Program):
    # A second chapter, so a whole chapter can disappear.
    extra = copy.deepcopy(program_minimal.chapters[0])
    extra.id = "extra"
    for lesson in extra.lessons:
        lesson.id = f"x-{lesson.id}"
        for card in lesson.cards:
            card.id = f"x-{card.id}"
    both = clone(program_minimal)
    both.chapters.append(extra)
    seeded_db.upsert_program(both)
    assert [chapter["id"] for chapter in seeded_db.chapters()] == ["basics", "extra"]

    only_first_lesson = clone(program_minimal)
    only_first_lesson.chapters[0].lessons.pop()
    result = seeded_db.upsert_program(only_first_lesson)

    # Lesson "second" (2 cards) and chapter "extra" (4 cards) are gone.
    assert result["retired"] == 6
    assert [chapter["id"] for chapter in seeded_db.chapters()] == ["basics"]
    assert [lesson["id"] for lesson in seeded_db.lessons_of_chapter("basics")] == ["first"]
    assert seeded_db.lessons_of_chapter("extra") == []
    assert seeded_db.fetch_all("SELECT id FROM chapters WHERE retired=1")[0]["id"] == "extra"
    assert seeded_db.lesson("second")["retired"] == 1
    assert seeded_db.scalar("SELECT count(*) FROM cards WHERE retired=0") == 2

    seeded_db.upsert_program(both)
    assert [chapter["id"] for chapter in seeded_db.chapters()] == ["basics", "extra"]
    assert seeded_db.scalar("SELECT count(*) FROM cards WHERE retired=1") == 0


def test_retired_cards_excluded_from_lesson_and_review_queries(seeded_db: Database, program_minimal: Program):
    user = seeded_db.create_user("learner", "hash")
    for card_id in ("light-versus-sound", "primary-colors"):
        scheduled(seeded_db, user, card_id)
    seeded_db.complete_lesson(user, "second", 1.0)
    assert len(seeded_db.mixed_practice_candidate_cards(user)) == 2

    seeded_db.upsert_program(drop_card(program_minimal, "light-versus-sound"))

    assert [card["id"] for card in seeded_db.lesson_cards("second")] == ["primary-colors"]
    assert "light-versus-sound" not in [card["id"] for card in seeded_db.chapter_cards("basics")]
    assert [card["id"] for card in seeded_db.mixed_practice_candidate_cards(user)] == ["primary-colors"]
    # Looked up by id, a retired card is still readable: its history needs it.
    assert seeded_db.card("light-versus-sound")["retired"] == 1
    assert "light-versus-sound" in seeded_db.cards_for_user(user, ["light-versus-sound"])


def test_retired_lesson_is_not_the_one_in_progress(seeded_db: Database, program_minimal: Program):
    user = seeded_db.create_user("learner", "hash")
    seeded_db.start_lesson(user, "second")
    assert seeded_db.in_progress_lesson(user) == "second"

    only_first = clone(program_minimal)
    only_first.chapters[0].lessons.pop()
    seeded_db.upsert_program(only_first)

    assert seeded_db.in_progress_lesson(user) is None


def test_program_version_stored(db: Database, program_minimal: Program):
    assert db.program_version() == "unseeded"

    db.upsert_program(program_minimal)

    assert db.program_version() == program_minimal.program_version
    assert db.scalar("SELECT DISTINCT program_version FROM cards") == program_minimal.program_version

    edited = clone(program_minimal)
    card_of(edited, CARD).prompt = "A new wording"
    db.upsert_program(edited)
    assert db.program_version() == edited.program_version
    assert db.program_version() != program_minimal.program_version


def test_events_store_check_version(seeded_db: Database):
    user = seeded_db.create_user("learner", "hash")
    answered(seeded_db, user, CARD, "e1")

    row = seeded_db.events_for_user(user)[0]
    assert row["check_version"] == seeded_db.card(CARD)["check_version"]

    with pytest.raises(KeyError):
        seeded_db.record_event(
            user,
            {
                "id": "e2",
                "ts": "2026-10-07T10:00:00+00:00",
                "session_id": "first",
                "card_id": CARD,
                "kind": "choice",
                "answer": "",
                "elapsed_ms": 0,
            },
        )


def test_upsert_is_one_transaction(seeded_db: Database, program_minimal: Program):
    user = seeded_db.create_user("learner", "hash")
    scheduled(seeded_db, user, CARD)
    broken = clone(program_minimal)
    card_of(broken, CARD).answer = "Changed answer"
    # A missing prompt cannot be stored, so the write fails after the reset was staged.
    card_of(broken, OTHER_CARD).prompt = None

    with pytest.raises(sqlite3.IntegrityError):
        seeded_db.upsert_program(broken)

    assert state_rows(seeded_db, CARD) == 1
    assert seeded_db.card(CARD)["answer"] == "The capital city of France is Paris."


def test_reverted_answer_edit_gets_a_new_check_version(seeded_db: Database, program_minimal: Program):
    first = seeded_db.card(CARD)
    assert first["check_epoch"] == 0
    edited = clone(program_minimal)
    card_of(edited, CARD).answer = "The capital city of France is Paris, on the Seine."
    seeded_db.upsert_program(edited)
    seeded_db.upsert_program(program_minimal)
    reverted = seeded_db.card(CARD)
    assert reverted["check_hash"] == first["check_hash"]
    assert reverted["check_epoch"] == 2
    assert reverted["check_version"] != first["check_version"]
    # Loading the same program again, or retiring and restoring the card, keeps the version.
    seeded_db.upsert_program(drop_card(program_minimal, CARD))
    seeded_db.upsert_program(program_minimal)
    assert seeded_db.card(CARD)["check_version"] == reverted["check_version"]
