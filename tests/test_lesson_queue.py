"""Queue rules that are easy to break silently: spacing of the ladder and the chapter filler."""

from __future__ import annotations

import pytest

from app import session
from app.database import Database
from tests.helpers import Learner, first_of

FIVE_CARDS = {
    "big": ["capital-of-france", "largest-planet", "boiling-point", "tallest-mountain", "longest-river"]
}
MISSED = "capital-of-france"


def test_first_rung_lands_exactly_retry_gap_other_steps_after_the_miss(db, tmp_path):
    learner = Learner(db, tmp_path, exercises={"triage": False}, lessons=FIVE_CARDS)
    queue = learner.start("big")
    primary = queue[0]
    assert primary["card_id"] == MISSED and primary["id"].startswith("p1:")
    assert learner.answer(primary, right=False, session_id="big").correct is False

    served: list[dict] = []
    while True:
        queue = learner.steps("big")
        # Rebuilt by a fresh connection, the continuation is the same.
        reopened = Database(db.path)
        assert session.lesson_steps(reopened, learner.user, reopened.lesson("big")) == queue
        step = queue[0]
        if step["id"].startswith("v2:"):
            break
        served.append(step)
        assert learner.answer(step, session_id="big").status == "inserted"
    others = [step for step in served if step["card_id"] != MISSED]
    assert session.RETRY_GAP == 3, "the spacing the course promises: three other exercises"
    assert len(others) == session.RETRY_GAP
    assert step["card_id"] == MISSED
    # The spacing was a real constraint: other work was still waiting.
    assert any(item["card_id"] != MISSED for item in queue[1:])


def test_one_card_lesson_borrows_scheduled_cards_once(db, tmp_path):
    layout = {"solo": ["capital-of-france"], "other": ["largest-planet"]}
    learner = Learner(db, tmp_path, lessons=layout)
    learner.start("other")
    learner.drain("other")
    assert learner.row("largest-planet")["cs_state"] != "new"

    learner.start("solo")
    primary = first_of(learner.steps("solo"), MISSED, "p1:")
    for step in learner.steps("solo"):
        if step["id"] == primary["id"]:
            break
        assert learner.answer(step, session_id="solo").status == "inserted"
    assert learner.answer(primary, right=False, session_id="solo").correct is False

    queue = learner.steps("solo")
    filler = [step for step in queue if step["card_id"] == "largest-planet"]
    assert [step["kind"] for step in filler] == ["flash"], "a scheduled card of the chapter fills the gap"
    for step in queue:
        assert learner.answer(step, session_id="solo").status == "inserted"
        if step is filler[0]:
            break
    assert all(step["card_id"] != "largest-planet" for step in learner.steps("solo")), "served only once"
    learner.drain("solo")
    assert db.user_lesson_state(learner.user, "solo")["status"] == "completed"


def test_card_without_rungs_fails_loudly(db, tmp_path):
    learner = Learner(db, tmp_path)
    with db._transaction() as conn:
        conn.execute("UPDATE cards SET rungs_json='[]' WHERE id=?", (MISSED,))
    with pytest.raises(ValueError, match="no enabled closed exercise"):
        learner.start()


def test_in_progress_lesson_is_chosen_deterministically(db, tmp_path):
    learner = Learner(db, tmp_path)
    learner.db.start_lesson(learner.user, "second")
    learner.db.start_lesson(learner.user, "first")
    with db._transaction() as conn:
        conn.execute("UPDATE user_lesson_state SET started_at='2026-10-07T10:00:00+00:00'")
    assert db.in_progress_lesson(learner.user) == "first"


def test_review_sitting_drops_a_card_reset_after_it_was_cut(db, tmp_path):
    learner = Learner(db, tmp_path)
    learner.start()
    learner.drain()
    study = db.start_practice(learner.user, "mixed_practice", [MISSED, "largest-planet"], "2026-10-07")
    assert {step["card_id"] for step in session.study_session_steps(db, learner.user, study)} == {
        MISSED,
        "largest-planet",
    }
    learner.reload(cards={MISSED: {"answer": "Paris is the capital city of France"}})
    queue = session.study_session_steps(db, learner.user, study)
    assert {step["card_id"] for step in queue} == {"largest-planet"}
    assert session.session_progress(db, learner.user, study) == (1, 0)
