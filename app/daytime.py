"""The learning-day boundary: a day rolls over at a configured local hour, not at midnight.

Someone studying past midnight should not have the day change under them mid-session.
Everything that decides "what day is it" (the day's plan, its key, the retention window)
reads it from here so the app never holds two different ideas of today at once.

The timezone and rollover hour come from the program's `Schedule`; nothing here is
module-global, so two programs can use different clocks side by side.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.content import Schedule


def local_now(schedule: Schedule, now: datetime | None = None) -> datetime:
    """The current moment expressed in the schedule's timezone (`now` is injectable for tests).

    `now` must be timezone-aware: reading a naive time as local or as UTC would silently
    put the day boundary in the wrong place, so it is rejected.
    """
    if now is not None and now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    moment = now or datetime.now(UTC)
    return moment.astimezone(ZoneInfo(schedule.timezone))


def day_start(now: datetime, schedule: Schedule) -> datetime:
    """Start of the learning day containing `now`, in the schedule's timezone."""
    local = local_now(schedule, now)
    start = local.replace(hour=schedule.day_starts_at_hour, minute=0, second=0, microsecond=0)
    if local < start:
        start -= timedelta(days=1)
    return start


def day_end(now: datetime, schedule: Schedule) -> datetime:
    """Exclusive end of the learning day containing `now` (the next rollover)."""
    start = day_start(now, schedule)
    # Add a calendar day to the wall-clock time rather than 24 hours, so a daylight-saving
    # change does not shift the rollover away from the configured hour.
    next_day = start.replace(tzinfo=None) + timedelta(days=1)
    return next_day.replace(tzinfo=start.tzinfo)


def day_key(now: datetime, schedule: Schedule) -> str:
    """Calendar-date label of the learning day containing `now`."""
    return day_start(now, schedule).date().isoformat()


def to_utc_iso(dt: datetime) -> str:
    """Normalise an aware datetime to the UTC ISO form stored for `due` timestamps.

    Comparing ISO strings that carry different UTC offsets is not reliable, so every
    stored timestamp and every day-boundary comparison goes through UTC first.
    """
    return dt.astimezone(UTC).isoformat()
