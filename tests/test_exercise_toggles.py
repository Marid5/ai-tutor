"""Exercise toggles and the content lifecycle, driven through the engine on a real database."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import curriculum, session
from app.content import Program
from app.database import Database
from tests.helpers import fake_now, make_user, right_answer, seed, submit, write_program, wrong_answer

CARD = "capital-of-france"
OTHER = "largest-planet"


class Learner:
    """One learner on a course written to disk, with helpers to walk a lesson."""

    def __init__(self, db: Database, tmp_path: Path, **course):
        self.db = db
        self.tmp_path = tmp_path
        self.program = seed(db, write_program(tmp_path, **course))
        self.user = make_user(db)

    def reload(self, **course) -> Program:
        """Edit the course on disk and load it again, as a restart would."""
        self.program = seed(self.db, write_program(self.tmp_path, **course))
        return self.program

    def row(self, card_id: str):
        return self.db.card_for_user(self.user, card_id)

    def lesson(self, lesson_id: str = "first"):
        return self.db.lesson(lesson_id)

    def start(self, lesson_id: str = "first") -> list[dict]:
        self.db.start_lesson(self.user, lesson_id)
        return self.steps(lesson_id)

    def steps(self, lesson_id: str = "first") -> list[dict]:
        return session.lesson_steps(self.db, self.user, self.lesson(lesson_id))

    def answer(self, step: dict, *, right: bool = True, session_id: str = "first", **kwargs):
        row = self.row(step["card_id"])
        text = right_answer(row, step) if right else wrong_answer(row, step)
        return submit(self.db, self.user, self.program, session_id, step, text, **kwargs)

    def drain(self, lesson_id: str = "first", miss: set[str] | None = None, limit: int = 60) -> list[dict]:
        """Answer every step (right, except primaries of cards in `miss`) until the lesson ends."""
        served: list[dict] = []
        for _ in range(limit):
            queue = self.steps(lesson_id)
            if not queue:
                return served
            step = queue[0]
            served.append(step)
            wrong = step["id"].startswith("p1:") and step["card_id"] in (miss or set())
            outcome = self.answer(step, right=not wrong, session_id=lesson_id)
            assert outcome.status == "inserted", outcome
        raise AssertionError("lesson did not terminate")

    def view(self) -> dict:
        return curriculum.chapters_view(self.db, self.user, self.program, fake_now())


def lesson_ref(view: dict, lesson_id: str) -> dict:
    return next(
        lesson for chapter in view["chapters"] for lesson in chapter["lessons"] if lesson["id"] == lesson_id
    )


def first_of(queue: list[dict], card_id: str, prefix: str) -> dict:
    return next(step for step in queue if step["card_id"] == card_id and step["id"].startswith(prefix))


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
