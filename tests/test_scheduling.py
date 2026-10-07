"""FSRS scheduling and the learning-day boundary."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.content import Schedule
from app.daytime import day_end, day_key, local_now, to_utc_iso
from app.fsrs import Rating, StoredCard, rating_for, schedule

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def new_card() -> StoredCard:
    return StoredCard(
        due=NOW.isoformat(),
        stability=None,
        difficulty=None,
        reps=0,
        lapses=0,
        state="new",
        last_review=None,
    )


def interval_days(stored: StoredCard, now: datetime = NOW) -> float:
    return (datetime.fromisoformat(stored.due) - now).total_seconds() / 86400


def mature_card(plan: Schedule) -> StoredCard:
    """A card reviewed well a few times, so its next interval is long enough to compare."""
    stored, now = new_card(), NOW
    for _ in range(4):
        stored = schedule(stored, Rating.Good, now, plan)
        now = datetime.fromisoformat(stored.due)
    return stored


def test_day_key_respects_timezone_and_start_hour():
    plan = Schedule(timezone="Asia/Tokyo", day_starts_at_hour=4)
    # 20:00 UTC is 05:00 the next morning in Tokyo, after the 04:00 rollover.
    assert day_key(datetime(2026, 10, 7, 20, 0, tzinfo=UTC), plan) == "2026-10-08"
    # 18:00 UTC is 03:00 in Tokyo, still the previous learning day.
    assert day_key(datetime(2026, 10, 7, 18, 0, tzinfo=UTC), plan) == "2026-10-07"


def test_day_rolls_over_at_start_hour():
    plan = Schedule(timezone="Europe/Paris", day_starts_at_hour=6)
    tz = ZoneInfo("Europe/Paris")
    before = datetime(2026, 10, 8, 5, 59, tzinfo=tz)
    after = datetime(2026, 10, 8, 6, 0, tzinfo=tz)

    assert day_key(before, plan) == "2026-10-07"
    assert day_key(after, plan) == "2026-10-08"
    assert day_end(before, plan) == datetime(2026, 10, 8, 6, 0, tzinfo=tz)
    assert day_end(after, plan) == datetime(2026, 10, 9, 6, 0, tzinfo=tz)
    assert to_utc_iso(after) == "2026-10-08T04:00:00+00:00"


def test_day_end_keeps_start_hour_across_spring_forward():
    plan = Schedule(timezone="Europe/Paris", day_starts_at_hour=6)
    tz = ZoneInfo("Europe/Paris")

    end = day_end(datetime(2026, 3, 28, 12, tzinfo=tz), plan)

    # Clocks jump forward on 29 March, so the learning day is 23 hours long.
    assert end == datetime(2026, 3, 29, 6, 0, tzinfo=tz)
    assert to_utc_iso(end) == "2026-03-29T04:00:00+00:00"


def test_day_end_keeps_start_hour_across_fall_back():
    plan = Schedule(timezone="Europe/Paris", day_starts_at_hour=6)
    tz = ZoneInfo("Europe/Paris")

    end = day_end(datetime(2026, 10, 24, 12, tzinfo=tz), plan)

    # Clocks go back at 03:00 on 25 October, inside this learning day (24 Oct 06:00 to
    # 25 Oct 06:00), so it is 25 hours long. Adding a flat 24 hours would end it at 05:00.
    assert end == datetime(2026, 10, 25, 6, 0, tzinfo=tz)
    assert to_utc_iso(end) == "2026-10-25T05:00:00+00:00"


def test_naive_datetime_is_rejected():
    plan = Schedule()
    naive = datetime(2026, 10, 7, 12, 0)

    with pytest.raises(ValueError, match="timezone-aware"):
        local_now(plan, naive)
    with pytest.raises(ValueError):
        day_key(naive, plan)


def test_local_now_uses_schedule_timezone():
    plan = Schedule(timezone="America/New_York")

    local = local_now(plan, NOW)

    assert local == NOW
    assert local.utcoffset() == timedelta(hours=-4)
    assert str(local.tzinfo) == "America/New_York"
    assert local_now(plan).tzinfo is not None


def test_good_rating_extends_interval():
    plan = Schedule()
    first = schedule(new_card(), Rating.Good, NOW, plan)
    second = schedule(first, Rating.Good, datetime.fromisoformat(first.due), plan)

    assert first.state != "new"
    assert first.reps == 1
    assert interval_days(first) > 0
    second_interval = interval_days(second, datetime.fromisoformat(first.due))
    assert second_interval > interval_days(first)


def test_again_rating_shortens_interval():
    plan = Schedule()
    mature = mature_card(plan)
    review_at = datetime.fromisoformat(mature.due)

    lapsed = schedule(mature, Rating.Again, review_at, plan)

    assert lapsed.lapses == mature.lapses + 1
    previous_interval = interval_days(mature, datetime.fromisoformat(mature.last_review))
    assert interval_days(lapsed, review_at) < previous_interval


def test_interval_capped_by_max_interval_days():
    plan = Schedule(max_interval_days=5)
    stored, now = new_card(), NOW
    for _ in range(8):
        stored = schedule(stored, Rating.Good, now, plan)
        assert datetime.fromisoformat(stored.due) - now <= timedelta(days=5)
        now = datetime.fromisoformat(stored.due)


def test_retention_parameter_applied():
    strict = Schedule(desired_retention=0.99)
    relaxed = Schedule(desired_retention=0.8)

    strict_card, relaxed_card = mature_card(strict), mature_card(relaxed)

    assert datetime.fromisoformat(strict_card.due) < datetime.fromisoformat(relaxed_card.due)


@pytest.mark.parametrize(
    ("kind", "correct", "answer", "expected"),
    [
        ("triage", None, "know", Rating.Good),
        ("triage", None, "dont_know", Rating.Again),
        ("flash", None, "remembered", Rating.Good),
        ("flash", None, "forgot", Rating.Again),
        ("choice", True, "Paris", Rating.Good),
        ("cloze", False, "Rome", Rating.Again),
        ("assemble", True, "a b c", Rating.Good),
    ],
)
def test_rating_for_maps_triage_flash_and_closed_kinds(kind, correct, answer, expected):
    assert rating_for(kind, correct, answer) == expected


def test_rating_for_rejects_unscored_closed_answer_and_unknown_kind():
    # A closed step needs the server's own verdict; the client's claim never counts.
    with pytest.raises(ValueError):
        rating_for("choice", None, "Paris")
    with pytest.raises(ValueError):
        rating_for("essay", True, "x")
