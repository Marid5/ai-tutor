"""Per-learner state: schedules, events, lessons and study sessions."""

import sqlite3
from datetime import UTC, datetime

import pytest

from app.database import Database, StoredCard

TS = datetime(2026, 10, 7, 12, 0, tzinfo=UTC).isoformat()


def event(event_id: str, step_id: str | None, session_id: str = "first", **extra) -> dict:
    return {
        "id": event_id,
        "ts": TS,
        "session_id": session_id,
        "card_id": "capital-of-france",
        "kind": "choice",
        "answer": "Paris",
        "elapsed_ms": 500,
        "step_id": step_id,
        "check_hash": "h",
        **extra,
    }


def test_get_state_creates_a_new_card_once(seeded_db: Database):
    user = seeded_db.create_user("learner", "hash")

    state = seeded_db.get_state(user, "capital-of-france")

    assert state.state == "new"
    assert (state.reps, state.lapses, state.stability, state.last_review) == (0, 0, None, None)
    assert seeded_db.scalar("SELECT count(*) FROM card_state") == 1


def test_update_state_round_trips_and_stores_check_hash(seeded_db: Database):
    user = seeded_db.create_user("learner", "hash")
    stored = StoredCard(
        due=TS, stability=3.5, difficulty=4.2, reps=2, lapses=1, state="review", last_review=TS
    )

    seeded_db.update_state(user, "capital-of-france", stored, check_hash="abc")

    assert seeded_db.get_state(user, "capital-of-france") == stored
    assert seeded_db.scalar("SELECT check_hash FROM card_state WHERE user_id=?", (user,)) == "abc"


def test_reset_card_state_drops_one_schedule(seeded_db: Database):
    user = seeded_db.create_user("learner", "hash")
    seeded_db.get_state(user, "capital-of-france")
    seeded_db.get_state(user, "powerhouse-organelle")

    seeded_db.reset_card_state(user, "capital-of-france")

    assert seeded_db.scalar("SELECT card_id FROM card_state") == "powerhouse-organelle"


def test_record_event_is_idempotent(seeded_db: Database):
    user = seeded_db.create_user("learner", "hash")

    assert seeded_db.record_event(user, event("e1", "step-1")) == "inserted"
    assert seeded_db.record_event(user, event("e1", "step-1")) == "duplicate_event"
    assert seeded_db.record_event(user, event("e2", "step-1")) == "duplicate_step"
    # The same step id in another session is a different step.
    assert seeded_db.record_event(user, event("e3", "step-1", session_id="second")) == "inserted"
    # Events without a step id never collide on it.
    assert seeded_db.record_event(user, event("e4", None)) == "inserted"
    assert seeded_db.record_event(user, event("e5", None)) == "inserted"

    assert [row["id"] for row in seeded_db.events_for_user(user)] == ["e1", "e3", "e4", "e5"]
    assert [row["id"] for row in seeded_db.events_for_session(user, "first")] == ["e1", "e4", "e5"]
    assert len(seeded_db.events_between(user, "2026-10-07T00:00:00+00:00", "2026-10-08T00:00:00+00:00")) == 4
    assert seeded_db.events_between(user, "2026-10-08T00:00:00+00:00", "2026-10-09T00:00:00+00:00") == []


def test_remediation_step_ids_are_unique_across_sessions(seeded_db: Database):
    user = seeded_db.create_user("learner", "hash")

    assert seeded_db.record_event(user, event("e1", "v2:capital-of-france:2:h8")) == "inserted"
    assert (
        seeded_db.record_event(user, event("e2", "v2:capital-of-france:2:h8", session_id="second"))
        == "duplicate_step"
    )


def test_lesson_progress(seeded_db: Database):
    user = seeded_db.create_user("learner", "hash")

    assert seeded_db.user_lesson_state(user, "first") is None
    seeded_db.start_lesson(user, "first")
    seeded_db.start_lesson(user, "first")
    assert seeded_db.in_progress_lesson(user) == "first"
    assert seeded_db.user_lesson_state(user, "first")["status"] == "in_progress"

    seeded_db.complete_lesson(user, "first", 0.75)

    state = seeded_db.user_lesson_state(user, "first")
    assert (state["status"], state["accuracy"]) == ("completed", 0.75)
    assert seeded_db.in_progress_lesson(user) is None
    assert set(seeded_db.user_lesson_states(user)) == {"first"}


def test_user_meta(seeded_db: Database):
    user = seeded_db.create_user("learner", "hash")
    assert seeded_db.get_user_meta(user, "show_hint_by_default") is None
    seeded_db.set_user_meta(user, "show_hint_by_default", "1")
    seeded_db.set_user_meta(user, "show_hint_by_default", "0")
    assert seeded_db.get_user_meta(user, "show_hint_by_default") == "0"


def test_cards_for_user_joins_schedule(seeded_db: Database):
    user = seeded_db.create_user("learner", "hash")
    seeded_db.get_state(user, "capital-of-france")

    rows = seeded_db.cards_for_user(user, ["capital-of-france", "powerhouse-organelle"])

    assert rows["capital-of-france"]["cs_state"] == "new"
    assert rows["powerhouse-organelle"]["cs_state"] is None
    assert seeded_db.cards_for_user(user, []) == {}
    assert seeded_db.card_for_user(user, "capital-of-france")["id"] == "capital-of-france"
    assert seeded_db.card_for_user(user, "missing") is None


def test_review_sessions_are_unique_per_slice(seeded_db: Database):
    user = seeded_db.create_user("learner", "hash")
    seeded_db.create_review_session("review-d-0", user, "d", ["capital-of-france"], slice_index=0)
    seeded_db.create_review_session("review-d-1", user, "d", ["powerhouse-organelle"], slice_index=1)
    with pytest.raises(sqlite3.IntegrityError):
        seeded_db.create_review_session("review-d-0b", user, "d", [], slice_index=0)

    assert [row["id"] for row in seeded_db.review_sessions_of_day(user, "d")] == ["review-d-0", "review-d-1"]
    assert seeded_db.in_progress_study_session(user, "scheduled_review")["id"] in {"review-d-0", "review-d-1"}
    assert seeded_db.study_session(user, "review-d-0")["card_ids_json"] == '["capital-of-france"]'
    assert seeded_db.study_session("someone-else", "review-d-0") is None

    assert seeded_db.complete_study_session(user, "review-d-0") is True
    assert seeded_db.complete_study_session(user, "review-d-0") is False
    assert seeded_db.complete_study_session(user, "missing") is False


def test_practice_resumes_or_replaces_the_open_session(seeded_db: Database):
    user = seeded_db.create_user("learner", "hash")

    first = seeded_db.start_practice(user, "lesson_practice", ["capital-of-france"], "d", lesson_id="first")
    again = seeded_db.start_practice(user, "lesson_practice", ["capital-of-france"], "d", lesson_id="first")
    assert again["id"] == first["id"]
    assert first["id"].startswith("practice-")
    assert seeded_db.in_progress_practice(user)["id"] == first["id"]

    other = seeded_db.start_practice(user, "mixed_practice", ["powerhouse-organelle"], "d")
    assert other["id"] != first["id"]
    assert seeded_db.study_session(user, first["id"])["status"] == "abandoned"
    assert seeded_db.in_progress_practice(user)["id"] == other["id"]
    # An abandoned session is never reopened by completing it.
    assert seeded_db.complete_study_session(user, first["id"]) is False
