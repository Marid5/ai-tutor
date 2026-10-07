"""Learning: the home screen, lessons, review, practice, answers and progress.

A session payload is the same for every kind of session:

    {"session_id", "lesson_id", "title", "mode", "steps", "total_cards", "resolved_cards"}

`mode` is `lesson`, `scheduled_review`, `lesson_practice` or `mixed_practice`;
`steps` is the queue the engine rebuilds from accepted answers, so it is the
same after a reload or on another device. An empty `steps` list means the
session is finished.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app import curriculum, session
from app.database import Database
from app.daytime import day_key
from app.session import AnswerOutcome

from .deps import DB, CourseProgram, CurrentUser, utc_now

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# One request drains at most this many buffered answers; a longer offline
# buffer is sent in several requests.
ANSWER_BATCH_MAX = 100
REVIEW_TITLE = "Review"
PRACTICE_TITLE = "Practice"


# ---------------------------------------------------------------- payloads
def _lesson_payload(db: Database, user_id: str, lesson: dict[str, Any]) -> dict[str, Any]:
    steps = session.lesson_steps(db, user_id, lesson)
    if not steps:
        # Nothing left to ask, for instance because the remaining cards were
        # removed from the course: finish the lesson so the home screen moves on.
        session.maybe_complete_lesson(db, user_id, lesson)
    total, resolved = session.lesson_progress(db, lesson, steps)
    return {
        "session_id": lesson["id"],
        "lesson_id": lesson["id"],
        "title": lesson["title"],
        "mode": "lesson",
        "steps": steps,
        "total_cards": total,
        "resolved_cards": resolved,
    }


def _study_payload(db: Database, user_id: str, study: dict[str, Any]) -> dict[str, Any]:
    lesson_id = study.get("lesson_id")
    if study["mode"] == "scheduled_review":
        title = REVIEW_TITLE
    else:
        lesson = db.lesson(lesson_id) if lesson_id else None
        title = f"{PRACTICE_TITLE}: {lesson['title']}" if lesson else PRACTICE_TITLE
    total, resolved = session.session_progress(db, user_id, study)
    return {
        "session_id": study["id"],
        "lesson_id": lesson_id,
        "title": title,
        "mode": study["mode"],
        "steps": session.study_session_steps(db, user_id, study),
        "total_cards": total,
        "resolved_cards": resolved,
    }


def _session_payload(db: Database, user_id: str, session_id: str) -> dict[str, Any] | None:
    """Any session of this learner by id; None if there is no such (live) session."""
    lesson = db.lesson(session_id)
    if lesson is not None:
        return None if lesson["retired"] else _lesson_payload(db, user_id, lesson)
    study = db.study_session(user_id, session_id)
    return _study_payload(db, user_id, study) if study else None


def _live_lesson(db: Database, lesson_id: str) -> dict[str, Any]:
    lesson = db.lesson(lesson_id)
    if lesson is None or lesson["retired"]:
        raise HTTPException(status_code=404, detail="lesson not found")
    return lesson


# ------------------------------------------------------------- home screen
@router.get("/chapters")
def chapters(user: CurrentUser, db: DB, program: CourseProgram) -> dict:
    return curriculum.chapters_view(db, user.user_id, program, utc_now())


@router.get("/progress")
def progress(user: CurrentUser, db: DB, program: CourseProgram) -> dict:
    return curriculum.learning_metrics(db, user.user_id, utc_now(), program.schedule)


# ----------------------------------------------------------------- lessons
@router.post("/lessons/{lesson_id}/start")
def start_lesson(lesson_id: str, user: CurrentUser, db: DB) -> dict:
    # Any lesson, any time: there is no prerequisite chain and no rule that
    # an unfinished lesson must be finished first.
    lesson = _live_lesson(db, lesson_id)
    db.start_lesson(user.user_id, lesson_id)
    return _lesson_payload(db, user.user_id, lesson)


@router.post("/lessons/{lesson_id}/practice")
def practice_lesson(lesson_id: str, user: CurrentUser, db: DB, program: CourseProgram) -> dict:
    lesson = _live_lesson(db, lesson_id)
    state = db.user_lesson_state(user.user_id, lesson_id)
    if not state or state["status"] != "completed":
        raise HTTPException(status_code=409, detail="lesson not completed")
    # Cards added since the lesson was finished, or reset by an edit, are new
    # material rather than a replay: the lesson itself serves them.
    if session.lesson_steps(db, user.user_id, lesson):
        return _lesson_payload(db, user.user_id, lesson)
    card_ids = [row["id"] for row in db.lesson_cards(lesson_id)]
    if not card_ids:
        raise HTTPException(status_code=404, detail="nothing to practice")
    study = db.start_practice(
        user.user_id, "lesson_practice", card_ids, day_key(utc_now(), program.schedule), lesson_id=lesson_id
    )
    return _study_payload(db, user.user_id, study)


# ------------------------------------------------------- review and practice
@router.post("/review/start")
def start_review(user: CurrentUser, db: DB, program: CourseProgram) -> dict:
    study = session.open_review_session(db, user.user_id, utc_now(), program.schedule)
    if study is None:
        raise HTTPException(status_code=404, detail="no reviews due")
    return _study_payload(db, user.user_id, study)


@router.post("/practice/start")
def start_practice(user: CurrentUser, db: DB, program: CourseProgram) -> dict:
    pool = session.mixed_practice_pool(db, user.user_id, session.PRACTICE_CARDS_PER_SESSION)
    if not pool:
        raise HTTPException(status_code=404, detail="nothing to practice")
    study = db.start_practice(user.user_id, "mixed_practice", pool, day_key(utc_now(), program.schedule))
    return _study_payload(db, user.user_id, study)


@router.get("/session")
def current_session(
    user: CurrentUser,
    db: DB,
    program: CourseProgram,
    session_id: str | None = Query(default=None, min_length=1, max_length=200),
) -> dict:
    """The given session, or else what to continue: a lesson in progress, then review.

    Despite being a GET, this may write, idempotently: it completes a lesson
    found with nothing left to ask, and with no id it opens today's next
    review sitting when one is due (as the review start would). Repeating the
    request changes nothing further.
    """
    if session_id is not None:
        payload = _session_payload(db, user.user_id, session_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="unknown session")
        return payload
    lesson_id = db.in_progress_lesson(user.user_id)
    if lesson_id:
        return _lesson_payload(db, user.user_id, _live_lesson(db, lesson_id))
    now = utc_now()
    study = session.open_review_session(db, user.user_id, now, program.schedule)
    if study:
        return _study_payload(db, user.user_id, study)
    return {
        "session_id": session.review_slice_session_id(day_key(now, program.schedule), 0),
        "lesson_id": None,
        "title": REVIEW_TITLE,
        "mode": "scheduled_review",
        "steps": [],
        "total_cards": 0,
        "resolved_cards": 0,
    }


# ------------------------------------------------------------------ answers
class AnswerBatch(BaseModel):
    # Items are checked one by one by the engine, so one malformed answer is
    # reported in its own result instead of failing the whole batch.
    events: list[Any] = Field(min_length=1, max_length=ANSWER_BATCH_MAX)


# Client-supplied text goes into logs only as a bounded repr, never raw.
LOG_QUOTE_MAX = 100


def _bounded(value: Any) -> Any:
    return value[:LOG_QUOTE_MAX] if isinstance(value, str) else value


_NOT_AN_OBJECT = AnswerOutcome("invalid", None, "each event must be a JSON object")


def _result(event: Any, outcome: AnswerOutcome) -> dict[str, Any]:
    step_id = event.get("step_id") if isinstance(event, dict) else None
    rejected = outcome.status if outcome.status in ("stale", "invalid") else None
    return {
        "step_id": step_id if isinstance(step_id, str) else None,
        "accepted": rejected is None,
        "duplicate": outcome.status in ("duplicate_event", "duplicate_step"),
        "correct": outcome.correct,
        "rejected": rejected,
        "detail": outcome.detail,
    }


@router.post("/answers")
def answers(batch: AnswerBatch, user: CurrentUser, db: DB, program: CourseProgram) -> dict:
    """Apply buffered answers in order, each on its own; reply with where to continue.

    Idempotent: a replayed answer (same event id, or a step already answered)
    is reported as a duplicate and changes nothing. A step the engine would
    no longer serve (content or exercise settings changed since it was
    issued) is rejected as `stale` while the rest of the batch still applies,
    so a client's buffer always drains. `session` is the continuation of the
    last event's session (null if there is no such session).
    """
    results = []
    for event in batch.events:
        if isinstance(event, dict):
            outcome = session.record_answer(db, user.user_id, event, program=program, now=utc_now())
        else:
            outcome = _NOT_AN_OBJECT
        if outcome.status == "invalid":
            logger.warning("rejected answer from user_id=%s: %r", user.user_id, _bounded(outcome.detail))
        results.append(_result(event, outcome))

    last = batch.events[-1] if isinstance(batch.events[-1], dict) else {}
    session_id = last.get("session_id")
    payload = (
        _session_payload(db, user.user_id, session_id) if isinstance(session_id, str) and session_id else None
    )
    # The continuation should never open with the very step just answered; if
    # it does, the client would loop on it, so say so (and log it).
    first = payload["steps"][0] if payload and payload["steps"] else None
    conflict = bool(first and first["id"] == last.get("step_id"))
    if conflict:
        logger.error(
            "continuation repeats the answered step user_id=%s session_id=%r step_id=%r status=%s",
            user.user_id,
            _bounded(session_id),
            _bounded(last.get("step_id")),
            results[-1]["rejected"] or ("duplicate" if results[-1]["duplicate"] else "accepted"),
        )
    return {"results": results, "session": payload, "state_conflict": conflict}
