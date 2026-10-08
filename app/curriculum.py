"""The course as the learner sees it: the home screen, readiness and progress.

There is no gating. Every lesson can be started at any time, in any order;
the home screen only points at the next one worth doing. Progress is one
honest number per card: whether its last primary check was right. Lesson
completion is history and is never rewritten; when an edit of a card's
answer resets it, the lesson is flagged as having open work instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .content import Program, Schedule
from .database import PRACTICE_SESSION_ID_PREFIX, Database
from .daytime import day_key, day_start, require_aware
from .review import (
    PRACTICE_CARDS_PER_SESSION,
    REVIEW_CARDS_PER_SESSION,
    mixed_practice_pool,
    review_day_plan_status,
)
from .session import ELAPSED_MS_MAX
from .steps import (
    CLOSED_KINDS,
    card_is_ready,
    card_resolved,
    current_events,
    event_correct,
    group_by_card,
    is_primary,
)

SHOW_HINT_KEY = "settings:show_hint_by_default"
RETENTION_WINDOW_DAYS = 30


# ------------------------------------------------------------------ settings
def show_hint_by_default(db: Database, user_id: str) -> bool:
    """Default off: a hint waits behind a "Show hint" button, so the learner
    tries to recall the answer first and asks for help only when needed."""
    return db.get_user_meta(user_id, SHOW_HINT_KEY) == "1"


def set_show_hint_by_default(db: Database, user_id: str, value: bool) -> None:
    db.set_user_meta(user_id, SHOW_HINT_KEY, "1" if value else "0")


# ----------------------------------------------------------------- readiness
def _ready_cards(db: Database, user_id: str) -> set[str]:
    """Live cards whose last current primary check was right."""
    grouped = group_by_card(event for event in db.events_for_user(user_id) if event["kind"] in CLOSED_KINDS)
    if not grouped:
        return set()
    rows = db.cards_for_user(user_id, list(grouped))
    return {
        card_id
        for card_id, card_events in grouped.items()
        if card_id in rows and not rows[card_id]["retired"] and card_is_ready(rows[card_id], card_events)
    }


def readiness(db: Database, user_id: str) -> dict[str, int]:
    """How many live cards passed their last primary check, out of all live cards."""
    total = db.scalar("SELECT count(*) FROM cards WHERE retired=0") or 0
    return {"ready": len(_ready_cards(db, user_id)), "total": total}


# ---------------------------------------------------------------- home screen
def _open_work(cards: list[Any], lesson_events: list[Any]) -> bool:
    """Some card of the lesson has no current primary check, or its ladder is still open."""
    grouped = group_by_card(lesson_events)
    return any(not card_resolved(card, grouped.get(card["id"], [])) for card in cards)


def chapters_view(db: Database, user_id: str, program: Program, now: datetime) -> dict[str, Any]:
    """The home screen: every chapter and lesson (all open), the next lesson, review and practice."""
    require_aware(now)
    schedule = program.schedule
    lesson_states = db.user_lesson_states(user_id)
    in_progress = db.in_progress_lesson(user_id)
    ready = _ready_cards(db, user_id)
    events_by_session: dict[str, list[Any]] = {}
    for event in db.events_for_user(user_id):
        events_by_session.setdefault(event["session_id"], []).append(event)

    chapters_out = []
    for chapter in db.chapters():
        lessons_out = []
        for lesson in db.lessons_of_chapter(chapter["id"]):
            state = lesson_states.get(lesson["id"])
            completed = bool(state and state["status"] == "completed")
            cards = db.lesson_cards(lesson["id"])
            lessons_out.append(
                {
                    "id": lesson["id"],
                    "title": lesson["title"],
                    "position": lesson["position"],
                    # Every lesson can always be started; there is no gating.
                    "status": "completed" if completed else "available",
                    "is_in_progress": in_progress == lesson["id"],
                    "has_open_work": _open_work(cards, events_by_session.get(lesson["id"], [])),
                    "cards_total": len(cards),
                    "cards_ready": sum(1 for card in cards if card["id"] in ready),
                }
            )
        chapter_cards = db.chapter_cards(chapter["id"])
        chapters_out.append(
            {
                "id": chapter["id"],
                "title": chapter["title"],
                "position": chapter["position"],
                "lessons_total": len(lessons_out),
                "lessons_completed": sum(1 for lesson in lessons_out if lesson["status"] == "completed"),
                "cards_total": len(chapter_cards),
                "cards_ready": sum(1 for card in chapter_cards if card["id"] in ready),
                "lessons": lessons_out,
            }
        )

    review = review_day_plan_status(db, user_id, now, schedule)
    practice_pool = mixed_practice_pool(db, user_id, PRACTICE_CARDS_PER_SESSION)
    board = readiness(db, user_id)
    return {
        "day": day_key(now, schedule),
        "cards_total": board["total"],
        "cards_ready": board["ready"],
        "next_lesson": _next_lesson(chapters_out, in_progress),
        "review_due": review["due_total"],
        "review_sessions_remaining": review["review_sessions_remaining"],
        "review_session_size": REVIEW_CARDS_PER_SESSION,
        "practice_available": bool(practice_pool),
        "practice_card_count": len(practice_pool),
        "chapters": chapters_out,
        "program_version": db.program_version(),
    }


def _next_lesson(chapters_out: list[dict[str, Any]], in_progress: str | None) -> dict[str, Any] | None:
    """Resume the lesson in progress, else the first one not finished or with open work."""
    flat = [(chapter, lesson) for chapter in chapters_out for lesson in chapter["lessons"]]
    chosen = next(((c, lesson) for c, lesson in flat if in_progress and lesson["id"] == in_progress), None)
    if chosen is None:
        chosen = next(
            ((c, lesson) for c, lesson in flat if lesson["status"] != "completed" or lesson["has_open_work"]),
            None,
        )
    if chosen is None:
        return None
    chapter, lesson = chosen
    started = lesson["is_in_progress"] or lesson["status"] == "completed"
    return {
        "id": lesson["id"],
        "title": lesson["title"],
        "chapter_id": chapter["id"],
        "action": "continue" if started else "start",
    }


# -------------------------------------------------------------- progress view
FORECAST_DAYS = 7


def _forecast(db: Database, user_id: str, now: datetime, schedule: Schedule) -> list[dict[str, Any]]:
    """Cards falling due per learning day, for the next days that have any.

    Days are learning days in the program's timezone (a review due at 02:00
    belongs to the previous day when the day starts at 04:00), and anything
    already overdue is counted as due today.
    """
    rows = db.fetch_all(
        """SELECT s.due FROM card_state s JOIN cards c ON c.id=s.card_id
        JOIN lessons l ON l.id=c.lesson_id
        WHERE s.user_id=? AND s.state != 'new' AND c.retired=0 AND l.retired=0""",
        (user_id,),
    )
    today = day_key(now, schedule)
    amounts: dict[str, int] = {}
    for row in rows:
        day = max(day_key(datetime.fromisoformat(row["due"]), schedule), today)
        amounts[day] = amounts.get(day, 0) + 1
    return [{"day": day, "amount": amounts[day]} for day in sorted(amounts)[:FORECAST_DAYS]]


def learning_metrics(db: Database, user_id: str, now: datetime, schedule: Schedule) -> dict[str, Any]:
    """The progress screen.

    Retention counts primary checks only, and only from lessons and scheduled
    review: practice is voluntary extra work, not the signal this number
    tracks. Like every derivation from history, it reads only answers given
    against the card's current check. Readiness, problem cards and study time
    draw on every mode.
    """
    rows = db.events_for_user(user_id)
    cutoff = day_start(now, schedule) - timedelta(days=RETENTION_WINDOW_DAYS - 1)

    def in_window(row: Any) -> bool:
        return datetime.fromisoformat(row["ts"].replace("Z", "+00:00")) >= cutoff

    primary = [
        row
        for row in rows
        if is_primary(row) and not row["session_id"].startswith(PRACTICE_SESSION_ID_PREFIX) and in_window(row)
    ]
    cards = db.cards_for_user(user_id, [row["card_id"] for row in primary if row["card_id"]])
    graded = [
        row for row in primary if row["card_id"] in cards and current_events(cards[row["card_id"]], [row])
    ]
    correct = sum(1 for row in graded if event_correct(row, cards[row["card_id"]]))
    retention = round(correct * 100 / len(graded)) if graded else 0

    forecast = _forecast(db, user_id, now, schedule)
    difficult = db.fetch_all(
        """SELECT c.id, c.prompt, c.answer, count(*) AS again_count FROM cards c
        JOIN events e ON c.id=e.card_id AND e.check_version=c.check_version
        WHERE e.user_id=? AND e.rating='again' AND c.retired=0 GROUP BY c.id
        ORDER BY again_count DESC, c.position LIMIT 5""",
        (user_id,),
    )
    minutes = round(
        sum(max(0, min(row["elapsed_ms"], ELAPSED_MS_MAX)) for row in rows if in_window(row)) / 60000
    )
    board = readiness(db, user_id)
    return {
        "cards_ready": board["ready"],
        "cards_total": board["total"],
        "retention_30d": retention,
        "checks_30d": len(graded),
        "forecast_7d": forecast,
        "problem_cards": [dict(row) for row in difficult],
        "session_minutes": minutes,
        "lessons_total": db.scalar("SELECT count(*) FROM lessons WHERE retired=0") or 0,
        "lessons_completed": db.scalar(
            """SELECT count(*) FROM user_lesson_state s JOIN lessons l ON l.id=s.lesson_id
            WHERE s.user_id=? AND s.status='completed' AND l.retired=0""",
            (user_id,),
        )
        or 0,
    }
