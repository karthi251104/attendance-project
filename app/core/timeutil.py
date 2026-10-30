"""Date and time helpers.

Rule of thumb: store UTC datetimes in the database, show local time (TIMEZONE,
default Asia/Kolkata) in the UI, and show dates as dd/mm/yyyy.
"""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """MongoDB returns naive datetimes that are really UTC; make them timezone-aware."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def to_local(value: datetime, tz_name: str) -> datetime:
    return as_utc(value).astimezone(ZoneInfo(tz_name))


def end_of_day_utc(day: date, tz_name: str) -> datetime:
    """23:59:59 local time on `day`, as UTC. Used for the correction deadline."""
    return datetime.combine(day, time(23, 59, 59), tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)


def fmt_datetime(value: datetime | None, tz_name: str) -> str:
    if value is None:
        return ""
    return to_local(value, tz_name).strftime("%d/%m/%Y %H:%M")


def date_to_datetime(value: date | None) -> datetime | None:
    """BSON has no date type, so dates are stored as midnight UTC datetimes."""
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc) if value else None
