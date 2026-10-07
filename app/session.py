"""Sessions: what to ask next, and what an answer changes.

A session is a lesson (its id is the lesson id), a scheduled review sitting
(`review-<day>-<n>`) or a practice session (`practice-<uuid>`). For each one
the queue of upcoming steps is rebuilt from the answers the server has
accepted, never from client state, so a reload or a second device continues
with the identical sequence.

A lesson queue is built in four layers:

1. triage for every card the learner has never met (if the chapter enables it);
2. the primary check of every card not yet checked in this session;
3. a flash step for every card the learner just did not know;
4. ladder rungs for every missed primary check, placed `RETRY_GAP` other
   exercises later, topped up from the same chapter when the lesson is too
   small to provide that spacing.

`record_answer` is the only way an answer enters the system. It refuses steps
that are no longer valid (`stale`) or malformed (`invalid`), grades closed
steps on the server, stores the event with the card's check hash, moves the
spaced-repetition schedule where the session mode says it should, and
completes the lesson or session once its queue is empty.

Which cards a review or practice sitting contains is decided in `app.review`;
single steps, verdicts and the ladder live in `app.steps`. The names other
modules need from both are re-exported here, so the HTTP layer can import
the engine from one place.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from . import fsrs
from .content import Program
from .database import Database
from .daytime import require_aware, to_utc_iso
from .review import (
    CARD_COLUMNS,
    PRACTICE_CARDS_PER_SESSION,
    REVIEW_CARDS_PER_SESSION,
    due_review_cards,
    fold_practice_miss_into_today_plan,
    freeze_review_day_plan,
    mixed_practice_pool,
    open_review_session,
    review_day_plan_status,
    review_slice_session_id,
    unresolved_review_cards,
)
from .steps import (
    CLOSED_KINDS,
    FLASH_ANSWERS,
    LADDER_PREFIX,
    LEARNING_KINDS,
    PRIMARY_PREFIX,
    STEP_ID_MAX,
    STEP_KINDS,
    TRIAGE_ANSWERS,
    attempts_of,
    card_is_ready,
    card_probed,
    card_resolved,
    card_state_of,
    category_for,
    closed_step_correct,
    current_events,
    derive_card_stage,
    event_correct,
    flash_step,
    group_by_card,
    hash_prefix,
    is_closed,
    ladder_step,
    primary_attempt,
    primary_step,
    rungs_for,
    step_id_matches,
    step_is_available,
    triage_attempted,
    triage_enabled,
    triage_step,
)

__all__ = [
    "CLOSED_KINDS",
    "PRACTICE_CARDS_PER_SESSION",
    "REVIEW_CARDS_PER_SESSION",
    "LADDER_PREFIX",
    "PRIMARY_PREFIX",
    "STEP_KINDS",
    "AnswerOutcome",
    "accuracy_for_lesson",
    "card_is_ready",
    "card_probed",
    "card_resolved",
    "derive_card_stage",
    "due_review_cards",
    "finish_session_if_done",
    "freeze_review_day_plan",
    "lesson_progress",
    "lesson_steps",
    "maybe_complete_lesson",
    "mixed_practice_pool",
    "open_review_session",
    "record_answer",
    "review_day_plan_status",
    "review_slice_session_id",
    "rungs_for",
    "session_mode",
    "session_progress",
    "session_queue",
    "study_session_steps",
    "unresolved_review_cards",
]

# A missed card comes back after this many other exercises. In a one-card
# lesson there are none, so the continuation is topped up from the chapter.
RETRY_GAP = 3
# A card the learner did not know is read again at most this many times.
FLASH_MAX_ATTEMPTS = 2

LESSON_TOTAL_STEPS_MAX = 40
REVIEW_TOTAL_STEPS_MAX = 30

# Client-chosen event ids (the idempotency key of an answer). They also end up
# inside ladder step ids, so their length is bounded.
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# A real answer is a button label or the card's own answer; anything longer is not one.
ANSWER_MAX_LENGTH = 500

# Longest time a single answer may claim; longer means the tab was left open.
ELAPSED_MS_MAX = 5 * 60 * 1000

# study_sessions.mode values that are voluntary practice, not scheduled review.
PRACTICE_MODES = {"lesson_practice", "mixed_practice"}


# --------------------------------------------------------------- learning tail
def _needs_learning_step(row: Any, card_events: list[Any]) -> bool:
    """A card the learner did not know is read again before the session ends."""
    events = current_events(row, card_events)
    missed_triage = any(event["kind"] == "triage" and event["answer"] == "dont_know" for event in events)
    missed_check = any(is_closed(event) and not event_correct(event, row) for event in events)
    if not (missed_triage or missed_check):
        return False
    flashes = [event for event in events if event["kind"] == "flash"]
    if len(flashes) >= FLASH_MAX_ATTEMPTS:
        return False
    return not any(event["rating"] in {"good", "easy"} for event in flashes)


# ------------------------------------------------------------- ladder placement
def _pending_ladder_steps(row_by_id: dict[str, Any], events: list[Any]) -> list[tuple[int, dict[str, Any]]]:
    """Open ladder rungs, each with how many more exercises must pass before it.

    The gap is counted from accepted events, so "a miss comes back after three
    other exercises" survives a reload.
    """
    pending: list[tuple[int, dict[str, Any]]] = []
    for card_id, card_events in group_by_card(events).items():
        row = row_by_id.get(card_id)
        if row is None or row["retired"]:
            continue
        state = derive_card_stage(row, card_events)
        if state.stage == "closed" or not state.kind or not state.trigger_event_id:
            continue
        trigger_order = next(
            (event["accepted_order"] for event in card_events if event["id"] == state.trigger_event_id),
            0,
        )
        elsewhere = {
            (event["session_id"], event["step_id"] or f"event:{event['accepted_order']}")
            for event in events
            if event["accepted_order"] > trigger_order and event["card_id"] != card_id
        }
        pending.append(
            (
                max(0, RETRY_GAP - len(elsewhere)),
                ladder_step(row, state.kind, state.trigger_event_id, state.attempt),
            )
        )
    return pending


def _insert_ladder_steps(
    queue: list[dict[str, Any]], pending: list[tuple[int, dict[str, Any]]]
) -> list[tuple[int, dict[str, Any]]]:
    """Place every rung whose gap the queue can already absorb; return the rest."""
    deferred: list[tuple[int, dict[str, Any]]] = []
    for gap, step in sorted(pending, key=lambda item: (item[0], item[1]["card_id"])):
        if gap <= len(queue):
            queue.insert(gap, step)
        else:
            deferred.append((gap, step))
    return deferred


def _place_ladder_steps(
    db: Database,
    user_id: str,
    lesson: dict[str, Any] | None,
    queue: list[dict[str, Any]],
    pending: list[tuple[int, dict[str, Any]]],
    exclude: set[str],
) -> None:
    """Space every open rung as far as this session can, but never drop one.

    The gap is a target, not a precondition. A small lesson on a fresh account
    has nothing to space with, and dropping the rung would let the lesson end
    with a missed card never checked again. Whatever cannot be spaced goes to
    the end of the queue, which is the most spacing the session has to offer.
    """
    deferred = _insert_ladder_steps(queue, pending)
    if deferred and lesson is not None:
        needed = max(gap for gap, _ in deferred) - len(queue)
        queue.extend(_chapter_filler(db, user_id, lesson, needed, exclude))
        deferred = _insert_ladder_steps(queue, deferred)
    for _gap, step in deferred:
        queue.append(step)


def _chapter_filler(
    db: Database, user_id: str, lesson: dict[str, Any], needed: int, exclude: set[str]
) -> list[dict[str, Any]]:
    """Flash steps from the same chapter so a spaced rung has room to land.

    Only cards the learner already has a schedule for: a filler step must not
    introduce new material in the middle of another lesson.
    """
    if needed <= 0:
        return []
    rows = db.fetch_cards(
        f"""SELECT {CARD_COLUMNS}
        FROM cards c
        JOIN lessons l ON l.id=c.lesson_id
        LEFT JOIN card_state s ON s.card_id=c.id AND s.user_id=?
        WHERE l.chapter_id=? AND c.lesson_id != ? AND c.retired=0 AND l.retired=0
        ORDER BY l.position, c.position""",
        (user_id, lesson["chapter_id"], lesson["id"]),
    )
    filler: list[dict[str, Any]] = []
    for row in rows:
        if row["id"] in exclude or card_state_of(row) == "new":
            continue
        filler.append(flash_step(row, 0))
        if len(filler) >= needed:
            break
    return filler


# ----------------------------------------------------------------- lessons
def _lesson_rows(db: Database, user_id: str, lesson_id: str) -> list[Any]:
    return db.fetch_cards(
        f"""SELECT {CARD_COLUMNS}
        FROM cards c LEFT JOIN card_state s ON s.card_id=c.id AND s.user_id=?
        WHERE c.lesson_id=? AND c.retired=0 ORDER BY c.position""",
        (user_id, lesson_id),
    )


def lesson_steps(db: Database, user_id: str, lesson: dict[str, Any]) -> list[dict[str, Any]]:
    """The queue for one lesson. Empty means the lesson is finished (or retired)."""
    if lesson.get("retired"):
        return []
    lesson_id = lesson["id"]
    rows = _lesson_rows(db, user_id, lesson_id)
    events = db.events_for_session(user_id, lesson_id)
    grouped = group_by_card(events)
    row_by_id = {row["id"]: row for row in rows}
    card_events = {row["id"]: current_events(row, grouped.get(row["id"], [])) for row in rows}
    queue: list[dict[str, Any]] = []

    # 1. Triage every card the learner has not met yet. A card whose answer was
    #    edited has no schedule and no current events, so it is met again.
    for row in rows:
        if (
            triage_enabled(row)
            and card_state_of(row) == "new"
            and not triage_attempted(row, card_events[row["id"]])
        ):
            queue.append(triage_step(row, 0))

    # 2. The primary check, in the same session as the triage, so an honest
    #    first lesson already shows progress.
    for row in rows:
        if not card_probed(row, card_events[row["id"]]):
            queue.append(primary_step(row, primary_attempt(row, card_events[row["id"]])))

    # 3. Learning tail: a card the learner did not know is read again.
    for row in rows:
        if _needs_learning_step(row, card_events[row["id"]]):
            queue.append(flash_step(row, attempts_of(row, card_events[row["id"]], "flash")))

    # 4. Ladder rungs, spaced by their own gap. Cards answered in this session
    #    are excluded from the filler: a filler step is re-derived on every
    #    rebuild, and serving a step id the learner has already spent would be
    #    refused as a duplicate and leave the session stuck.
    _place_ladder_steps(
        db, user_id, lesson, queue, _pending_ladder_steps(row_by_id, events), set(row_by_id) | set(grouped)
    )
    return queue[:LESSON_TOTAL_STEPS_MAX]


def lesson_progress(db: Database, lesson: dict[str, Any], queue: list[dict[str, Any]]) -> tuple[int, int]:
    """(cards in the lesson, cards with nothing left in the queue)."""
    rows = db.lesson_cards(lesson["id"])
    pending = {step.get("card_id") for step in queue}
    return len(rows), sum(1 for row in rows if row["id"] not in pending)


def accuracy_for_lesson(db: Database, user_id: str, lesson_id: str) -> float:
    """Share of first attempts per (kind, card) that were right; later retries are practice."""
    events = db.events_for_session(user_id, lesson_id)
    rows = db.cards_for_user(user_id, [event["card_id"] for event in events if event["card_id"]])
    first: dict[tuple[str, str], Any] = {}
    for event in events:
        row = rows.get(event["card_id"])
        if row is None or event["kind"] not in STEP_KINDS or event["check_version"] != row["check_version"]:
            continue
        first.setdefault((event["kind"], event["card_id"]), event)
    if not first:
        return 1.0
    correct = sum(1 for event in first.values() if _graded_correct(event, rows[event["card_id"]]))
    return round(correct / len(first), 4)


def _graded_correct(event: Any, row: Any) -> bool:
    if event["kind"] == "triage":
        return event["answer"] == "know"
    if event["kind"] == "flash":
        return event["answer"] == "remembered"
    return event_correct(event, row)


def maybe_complete_lesson(db: Database, user_id: str, lesson: dict[str, Any]) -> bool:
    """Mark a started lesson completed once its queue is empty; True if it changed."""
    if lesson.get("retired"):
        return False
    state = db.user_lesson_state(user_id, lesson["id"])
    if not state or state["status"] == "completed":
        return False
    if lesson_steps(db, user_id, lesson):
        return False
    db.complete_lesson(user_id, lesson["id"], accuracy_for_lesson(db, user_id, lesson["id"]))
    return True


# ---------------------------------------------------- review and practice queues
def session_queue(
    db: Database, user_id: str, session_id: str, rows: list[Any], total_max: int = REVIEW_TOTAL_STEPS_MAX
) -> list[dict[str, Any]]:
    """The queue over a fixed set of cards (a review sitting or practice): no triage."""
    events = db.events_for_session(user_id, session_id)
    grouped = group_by_card(events)
    row_by_id = {row["id"]: row for row in rows}
    queue: list[dict[str, Any]] = []
    for row in rows:
        card_events = current_events(row, grouped.get(row["id"], []))
        if not card_probed(row, card_events):
            queue.append(primary_step(row, primary_attempt(row, card_events)))
    for row in rows:
        card_events = current_events(row, grouped.get(row["id"], []))
        if _needs_learning_step(row, card_events):
            queue.append(flash_step(row, attempts_of(row, card_events, "flash")))
    _place_ladder_steps(db, user_id, None, queue, _pending_ladder_steps(row_by_id, events), set(row_by_id))
    return queue[:total_max]


def _sitting_rows(db: Database, user_id: str, study_session: dict[str, Any]) -> list[Any]:
    """The sitting's cards that still belong in it, in the order it was cut.

    A card retired since then is gone, and one reset by an edit of its answer
    (no schedule any more) goes back to its lesson as new, so both are dropped,
    as they are from today's review plan.
    """
    card_ids = json.loads(study_session["card_ids_json"])
    rows_by_id = db.cards_for_user(user_id, card_ids)
    return [
        rows_by_id[card_id]
        for card_id in card_ids
        if card_id in rows_by_id
        and not rows_by_id[card_id]["retired"]
        and card_state_of(rows_by_id[card_id]) != "new"
    ]


def study_session_steps(db: Database, user_id: str, study_session: dict[str, Any]) -> list[dict[str, Any]]:
    return session_queue(db, user_id, study_session["id"], _sitting_rows(db, user_id, study_session))


def session_progress(db: Database, user_id: str, study_session: dict[str, Any]) -> tuple[int, int]:
    """(cards in the session, cards worked through), from the fixed card set alone.

    Derived from the queue length instead, the finish line would recede as the
    learner advances, because the ladder adds steps.
    """
    rows = _sitting_rows(db, user_id, study_session)
    grouped = group_by_card(db.events_for_session(user_id, study_session["id"]))
    resolved = sum(1 for row in rows if card_resolved(row, grouped.get(row["id"], [])))
    return len(rows), resolved


# ------------------------------------------------------------------- answers
@dataclass(frozen=True)
class AnswerOutcome:
    """What happened to one submitted answer.

    `inserted`: stored and applied. `duplicate_event` / `duplicate_step`: a
    replay, absorbed without effect. `stale`: the step is no longer one the
    engine would serve (the card or lesson was removed, the exercise switched
    off, or the card's answer edited since the step was issued). `invalid`:
    the event itself is malformed; `detail` says how. `correct` is the
    server's verdict for closed steps, else None.
    """

    status: Literal["inserted", "duplicate_event", "duplicate_step", "stale", "invalid"]
    correct: bool | None = None
    detail: str | None = None


def _invalid(detail: str) -> AnswerOutcome:
    return AnswerOutcome("invalid", None, detail)


STALE = AnswerOutcome("stale", None, "this step is no longer current")


def session_mode(db: Database, user_id: str, session_id: str) -> str | None:
    """The session's mode from server records (the client never states it); None if unknown.

    An answer may arrive late into a completed or abandoned session (an
    offline buffer draining); that is allowed here, and completion logic
    simply leaves such a session alone.
    """
    if db.lesson(session_id):
        return "lesson"
    study = db.study_session(user_id, session_id)
    return study["mode"] if study else None


def _fsrs_applies(mode: str, kind: str, step_id: str, correct: bool | None, row: Any) -> bool:
    """Which answers move the schedule.

    In a lesson or scheduled review, triage and flash always do: they are the
    learner's own report and there is no other signal for a card never
    checked. A closed step does too, but only as a primary check: a ladder
    rung is follow-up work on a card already rated by the miss that opened
    it, and rating it again would count one lapse several times.

    Practice is asymmetric. A correct answer never moves the schedule (the
    point is to hold the interval, not stretch it) and neither does flash;
    only a missed primary check applies `Again`. The exception is a card with
    no schedule yet, or one just reset by an edit of its answer: even practice
    has to seed a schedule there, so any outcome applies.
    """
    is_primary_check = kind in CLOSED_KINDS and step_id.startswith(PRIMARY_PREFIX)
    if mode not in PRACTICE_MODES:
        return kind in LEARNING_KINDS or is_primary_check
    if not is_primary_check:
        return False
    if card_state_of(row) == "new":
        return True
    return correct is False


def _event_time(event: dict[str, Any], now: datetime) -> str | None:
    """The event's own timestamp (an offline answer keeps its time), or now."""
    raw = event.get("ts")
    if raw is None:
        return to_utc_iso(now)
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return to_utc_iso(moment) if moment.utcoffset() is not None else None


def record_answer(
    db: Database, user_id: str, event: dict[str, Any], *, program: Program, now: datetime
) -> AnswerOutcome:
    """Validate, grade, store and apply one answer.

    `now` is the aware moment the server applies the answer; the schedule's
    timezone and day boundary come from `program.schedule`. The client's
    `correct` field is ignored: closed steps are graded here.
    """
    require_aware(now)
    event_id = event.get("id")
    if event_id is not None and (not isinstance(event_id, str) or not EVENT_ID_RE.fullmatch(event_id)):
        return _invalid("id must be 1-64 letters, digits, '-' or '_'")
    kind = event.get("kind")
    if kind not in STEP_KINDS:
        return _invalid(f"unknown step kind: {kind}")
    elapsed = event.get("elapsed_ms", 0)
    if isinstance(elapsed, bool) or not isinstance(elapsed, int) or not 0 <= elapsed <= ELAPSED_MS_MAX:
        return _invalid(f"elapsed_ms must be between 0 and {ELAPSED_MS_MAX}")
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return _invalid("session_id is required")
    mode = session_mode(db, user_id, session_id)
    if mode is None:
        return _invalid("unknown session")
    card_id = event.get("card_id")
    if not isinstance(card_id, str) or not card_id:
        return _invalid("card_id is required")
    step_id = event.get("step_id")
    if not isinstance(step_id, str) or not step_id or len(step_id) > STEP_ID_MAX:
        return _invalid(f"step_id is required and at most {STEP_ID_MAX} characters")
    answer = event.get("answer", "")
    if not isinstance(answer, str) or len(answer) > ANSWER_MAX_LENGTH:
        return _invalid(f"answer must be a string of at most {ANSWER_MAX_LENGTH} characters")
    timing_version = event.get("timing_version")
    if timing_version is not None and (type(timing_version) is not int):
        return _invalid("timing_version must be an integer")
    ts = _event_time(event, now)
    if ts is None:
        return _invalid("ts must be an ISO-8601 timestamp with a time zone")
    row = db.card_for_user(user_id, card_id)
    if row is None:
        return _invalid("unknown card")

    lesson = db.lesson(session_id) if mode == "lesson" else None
    if row["retired"] or (lesson is not None and lesson["retired"]):
        return STALE
    if not step_is_available(row, kind) or not step_id_matches(step_id, kind, row):
        return STALE
    if kind == "triage" and answer not in TRIAGE_ANSWERS:
        return _invalid(f"triage answer must be one of {sorted(TRIAGE_ANSWERS)}")
    if kind == "flash" and answer not in FLASH_ANSWERS:
        return _invalid(f"flash answer must be one of {sorted(FLASH_ANSWERS)}")

    correct = closed_step_correct(row, kind, answer) if kind in CLOSED_KINDS else None
    if step_id.startswith(PRIMARY_PREFIX):
        # A primary check is accepted only while the server would serve one
        # (the card has none in this session yet) and under the exact id it
        # would issue: a client cannot mint a second primary check of a card.
        session_events = db.events_for_session(user_id, session_id)
        card_events = [past for past in session_events if past["card_id"] == card_id]
        issued = f"{PRIMARY_PREFIX}{kind}:{card_id}:{hash_prefix(row)}:{primary_attempt(row, card_events)}"
        if card_probed(row, card_events) or step_id != issued:
            # A replay of an answer already accepted is still reported as a
            # duplicate, so a client retrying its buffer sees the same result.
            if event_id is not None and db.scalar("SELECT 1 FROM events WHERE id=?", (event_id,)):
                return AnswerOutcome("duplicate_event", correct, None)
            if any(past["step_id"] == step_id for past in card_events):
                return AnswerOutcome("duplicate_step", correct, None)
            return STALE
    rating = fsrs.rating_for(kind, correct, answer)
    stored = {
        "id": event_id or str(uuid.uuid4()),
        "ts": ts,
        "session_id": session_id,
        "card_id": card_id,
        "kind": kind,
        "answer": answer,
        "rating": rating.name.lower(),
        "correct": correct,
        "elapsed_ms": elapsed,
        "timing_version": timing_version,
        "step_id": step_id,
        # Pins the answer to the version of the check the learner actually saw.
        "check_version": row["check_version"],
    }
    result = db.record_event(user_id, stored)
    if result != "inserted":
        return AnswerOutcome(result, correct, None)

    if _fsrs_applies(mode, kind, step_id, correct, row):
        state = fsrs.schedule(db.get_state(user_id, card_id), rating, now, program.schedule)
        db.update_state(user_id, card_id, state, row["check_hash"])
        if mode in PRACTICE_MODES and correct is False:
            fold_practice_miss_into_today_plan(
                db, user_id, now, program.schedule, card_id, category_for(state.state, state.stability)
            )
    finish_session_if_done(db, user_id, session_id)
    return AnswerOutcome("inserted", correct, None)


def finish_session_if_done(db: Database, user_id: str, session_id: str) -> None:
    """Complete a lesson or study session whose queue has run out.

    Only an in-progress session completes; a completed or abandoned practice
    session is never silently reopened.
    """
    lesson = db.lesson(session_id)
    if lesson:
        maybe_complete_lesson(db, user_id, lesson)
        return
    study = db.study_session(user_id, session_id)
    if study and study["status"] == "in_progress" and not study_session_steps(db, user_id, study):
        db.complete_study_session(user_id, session_id)
