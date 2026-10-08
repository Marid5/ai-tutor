"""Scheduled review and mixed practice: which cards a sitting is made of.

Scheduled review works from a *day plan*: on the first check of a learning
day that finds anything due, the set of due cards is frozen, so the plan does
not drift under the learner as cards mature during the day. The plan is
served in sittings of `REVIEW_CARDS_PER_SESSION` cards (`review-<day>-<n>`).
A card leaves the plan once it is resolved in a sitting of that day, or when
it is retired or reset by an edit of its answer.

Mixed practice picks up to `PRACTICE_CARDS_PER_SESSION` cards from completed
lessons, weakest first. The queue of steps inside a sitting is built by
`app.session`, the same way for both.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .content import Schedule
from .database import Database
from .daytime import day_end, day_key, require_aware, to_utc_iso
from .steps import (
    card_category,
    card_resolved,
    card_state_of,
    current_events,
    event_correct,
    group_by_card,
    is_primary,
)

REVIEW_QUEUE_SAFETY_LIMIT = 500
REVIEW_CARDS_PER_SESSION = 10
PRACTICE_CARDS_PER_SESSION = 10

# Card columns plus the learner's schedule, as the queue builders expect them.
CARD_COLUMNS = "c.*, s.state AS cs_state, s.due AS cs_due, s.stability AS cs_stability"


# ------------------------------------------------------------- scheduled review
def _interleave_by_category(rows: list[Any]) -> list[Any]:
    """Spread new, learning and mature cards through the queue instead of front-loading one."""
    groups: dict[str, list[Any]] = {}
    for row in rows:
        groups.setdefault(card_category(row), []).append(row)
    slots: list[tuple[float, str, Any]] = []
    for category, group in groups.items():
        for index, row in enumerate(group):
            slots.append(((index + 0.5) / len(group), category, row))
    slots.sort(key=lambda item: (item[0], item[1]))
    return [row for _fraction, _category, row in slots]


def due_review_cards(
    db: Database, user_id: str, now: datetime, schedule: Schedule, limit: int = REVIEW_QUEUE_SAFETY_LIMIT
) -> list[Any]:
    """Every live card due before the current learning day ends, interleaved by category."""
    rows = db.fetch_all(
        f"""SELECT {CARD_COLUMNS}
        FROM cards c
        JOIN lessons l ON l.id=c.lesson_id
        JOIN card_state s ON s.card_id=c.id AND s.user_id=?
        WHERE c.retired=0 AND l.retired=0 AND s.state != 'new'
        AND julianday(s.due) < julianday(?)
        ORDER BY julianday(s.due) ASC, c.position""",
        (user_id, to_utc_iso(day_end(now, schedule))),
    )
    return _interleave_by_category(rows)[:limit]


def review_slice_session_id(day: str, slice_index: int) -> str:
    return f"review-{day}-{slice_index}"


def _review_day_plan_key(now: datetime, schedule: Schedule) -> str:
    return f"review_day_plan_{day_key(now, schedule)}"


def freeze_review_day_plan(
    db: Database, user_id: str, now: datetime, schedule: Schedule
) -> list[dict[str, str]]:
    """Fix today's review membership on the first non-empty check of the day.

    Without freezing, the plan would drift under the learner as cards mature
    during the day. An empty result is deliberately not stored: a card that
    becomes due later today can still form the day's first plan.
    """
    key = _review_day_plan_key(now, schedule)
    stored = db.get_user_meta(user_id, key)
    if stored is not None:
        return json.loads(stored)
    plan = [
        {"id": row["id"], "category": card_category(row)}
        for row in due_review_cards(db, user_id, now, schedule)
    ]
    if plan:
        db.set_user_meta(user_id, key, json.dumps(plan))
    return plan


def fold_practice_miss_into_today_plan(
    db: Database, user_id: str, now: datetime, schedule: Schedule, card_id: str, category: str
) -> None:
    """A practice miss can make a card due today; keep an already-frozen plan in step.

    An unfrozen day picks the card up at its first freeze. A card already in
    the plan is left alone, and one already resolved today stays excluded
    downstream by `unresolved_review_cards`.
    """
    key = _review_day_plan_key(now, schedule)
    stored = db.get_user_meta(user_id, key)
    if stored is None:
        return
    plan = json.loads(stored)
    if any(entry["id"] == card_id for entry in plan):
        return
    plan.append({"id": card_id, "category": category})
    db.set_user_meta(user_id, key, json.dumps(plan))


def _review_day_resolved_cards(db: Database, user_id: str, now: datetime, schedule: Schedule) -> set[str]:
    events: list[Any] = []
    for study in db.review_sessions_of_day(user_id, day_key(now, schedule)):
        events.extend(db.events_for_session(user_id, study["id"]))
    grouped = group_by_card(events)
    if not grouped:
        return set()
    rows = db.cards_for_user(user_id, list(grouped))
    return {
        card_id
        for card_id, card_events in grouped.items()
        if card_id in rows and card_resolved(rows[card_id], card_events)
    }


def unresolved_review_cards(db: Database, user_id: str, now: datetime, schedule: Schedule) -> list[str]:
    """Card ids of today's plan still to review.

    Cards retired since the plan was frozen, or reset by an edit of their
    answer (they go back to their lesson as new), are dropped.
    """
    plan = freeze_review_day_plan(db, user_id, now, schedule)
    if not plan:
        return []
    resolved = _review_day_resolved_cards(db, user_id, now, schedule)
    rows = db.cards_for_user(user_id, [entry["id"] for entry in plan])
    return [
        entry["id"]
        for entry in plan
        if entry["id"] not in resolved
        and entry["id"] in rows
        and not rows[entry["id"]]["retired"]
        and card_state_of(rows[entry["id"]]) != "new"
    ]


def review_day_plan_status(db: Database, user_id: str, now: datetime, schedule: Schedule) -> dict[str, int]:
    """How much of today's frozen plan is left, and in how many sittings."""
    remaining = unresolved_review_cards(db, user_id, now, schedule)
    sittings = -(-len(remaining) // REVIEW_CARDS_PER_SESSION) if remaining else 0
    return {"due_total": len(remaining), "review_sessions_remaining": sittings}


def open_review_session(
    db: Database, user_id: str, now: datetime, schedule: Schedule
) -> dict[str, Any] | None:
    """Resume the open review sitting, or cut the next one from today's plan; None if nothing is due."""
    require_aware(now)
    active = db.in_progress_study_session(user_id, "scheduled_review")
    if active:
        return active
    unresolved = unresolved_review_cards(db, user_id, now, schedule)
    if not unresolved:
        return None
    day = day_key(now, schedule)
    slice_index = max((row["slice_index"] for row in db.review_sessions_of_day(user_id, day)), default=0) + 1
    session_id = review_slice_session_id(day, slice_index)
    db.create_review_session(session_id, user_id, day, unresolved[:REVIEW_CARDS_PER_SESSION], slice_index)
    return db.study_session(user_id, session_id)


# ------------------------------------------------------------------ practice
def mixed_practice_pool(db: Database, user_id: str, limit: int = PRACTICE_CARDS_PER_SESSION) -> list[str]:
    """Deterministic candidate order for mixed practice over completed lessons.

    A card whose last primary check was wrong comes first, then by how long
    it has gone unchecked (never checked first), then by course position. A
    correct answer in practice moves a card out of the first group and
    refreshes the second, so the next run offers a different set.
    """
    candidates = db.mixed_practice_candidate_cards(user_id)
    if not candidates:
        return []
    grouped = group_by_card(event for event in db.events_for_user(user_id) if is_primary(event))

    def sort_key(row: Any) -> tuple[int, int, int, int]:
        primaries = current_events(row, grouped.get(row["id"], []))
        last = max(primaries, key=lambda event: event["accepted_order"]) if primaries else None
        wrong_first = 0 if last is None or not event_correct(last, row) else 1
        last_order = last["accepted_order"] if last is not None else -1
        return (wrong_first, last_order, row["lesson_position"], row["position"])

    return [row["id"] for row in sorted(candidates, key=sort_key)[:limit]]
