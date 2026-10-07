"""Spaced-repetition scheduling: which answer counts as which rating, and when a card is due next."""

from datetime import UTC, datetime, timedelta

from fsrs import FSRS, Card, Rating, State

from app.content import Schedule
from app.database import StoredCard

__all__ = ["Rating", "StoredCard", "rating_for", "schedule"]

# Kinds whose correctness the server computes from the card itself.
CLOSED_KINDS = {"choice", "cloze", "assemble"}


def rating_for(kind: str, correct: bool | None, answer: str) -> Rating:
    """Map a step answer to a rating.

    Never Easy: on a brand-new card it would push the next review weeks out, so marking
    a batch "known" could hide cards for a long time. Good is the ceiling.
    """
    if kind == "triage":
        return Rating.Good if answer == "know" else Rating.Again
    if kind == "flash":
        return Rating.Good if answer == "remembered" else Rating.Again
    if kind in CLOSED_KINDS:
        # Always the server's own verdict. These answers move the schedule, so a
        # client-supplied "correct" must never be trusted here.
        if correct is None:
            raise ValueError("a closed step requires a server-computed correctness")
        return Rating.Good if correct else Rating.Again
    raise ValueError(f"unsupported step kind: {kind}")


def _state(value: str) -> State:
    name = value.capitalize()
    return State[name] if name in State.__members__ else State.New


def _to_card(stored: StoredCard) -> Card:
    card = Card()
    card.due = datetime.fromisoformat(stored.due)
    card.stability = stored.stability
    card.difficulty = stored.difficulty
    card.reps = stored.reps
    card.lapses = stored.lapses
    card.state = _state(stored.state)
    if stored.last_review:
        card.last_review = datetime.fromisoformat(stored.last_review)
    return card


def schedule(stored: StoredCard, rating: Rating, now: datetime, schedule: Schedule) -> StoredCard:
    """Apply one review and return the card's new schedule.

    `schedule` is the program's `Schedule` (not this function): it supplies the target
    retention and the longest allowed gap between reviews. `now` must be timezone-aware,
    and every timestamp in `stored` (`due`, `last_review`) is an aware UTC ISO string,
    as `to_utc_iso` produces.
    """
    # The scheduler works in UTC; so does everything stored.
    utc_now = now.astimezone(UTC)
    scheduler = FSRS(
        request_retention=schedule.desired_retention,
        maximum_interval=schedule.max_interval_days,
    )
    card, _ = scheduler.review_card(_to_card(stored), rating, now=utc_now)
    # The library rounds its cap up (a 21-day maximum comes back as 22), so the limit is
    # enforced here rather than trusted to it.
    due = min(card.due, utc_now + timedelta(days=schedule.max_interval_days))
    return StoredCard(
        due=due.isoformat(),
        stability=card.stability,
        difficulty=card.difficulty,
        reps=card.reps,
        lapses=card.lapses,
        state=card.state.name.lower(),
        last_review=card.last_review.isoformat() if card.last_review else None,
    )
