"""Attendance calculation rules.

These functions are a direct translation of the existing Excel macro formula and
must stay in sync with it. They are pure (no I/O) so they are easy to test.

Macro rule (compared at second precision; the macro's "> TIME(4,59,59)" is the
same as ">= 5:00:00"):

    5DAG / 6DAG : P if worked >= 5h else A
    OFF         : P if worked >= 8h else OFF
    PH          : P if worked >= 5h else PH
    anything else -> "?" (reported as an exception, never guessed)
"""

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.config import Settings, get_settings

UNKNOWN_STATUS = "?"
WORKING_SCHEDULES = ("5DAG", "6DAG")

SECONDS_PER_DAY = 24 * 60 * 60
# Excel stores times as a fraction of a day counted from this "day zero".
EXCEL_EPOCH = datetime(1899, 12, 30)

_TIME_TEXT = re.compile(r"^(\d+):(\d{1,2})(?::(\d{1,2}))?$")


@dataclass(frozen=True)
class Thresholds:
    """Minimum worked seconds needed to count a day as Present."""

    workday_seconds: int = 5 * 3600
    offday_seconds: int = 8 * 3600
    ph_seconds: int = 5 * 3600

    @classmethod
    def from_settings(cls, settings: Settings) -> "Thresholds":
        return cls(
            workday_seconds=round(settings.workday_min_hours * 3600),
            offday_seconds=round(settings.offday_min_hours * 3600),
            ph_seconds=round(settings.ph_min_hours * 3600),
        )


def to_seconds(value: object) -> int:
    """Convert a 'Worked Hours' cell value into whole seconds.

    Accepts datetime.time, datetime.timedelta, datetime.datetime (Excel durations
    over 24h are read as dates near 1900), an Excel day fraction (int/float), or
    text such as "09:08:08" / "9:08". Empty or None counts as 0.
    Raises ValueError for anything that cannot be understood.
    """
    if value is None:
        return 0
    if isinstance(value, bool):  # bool is a subclass of int; never valid here
        raise ValueError(f"Cannot read worked hours from {value!r}")
    if isinstance(value, timedelta):
        return round(value.total_seconds())
    if isinstance(value, datetime):
        if value.year <= 1900:
            return round((value - EXCEL_EPOCH).total_seconds())
        return _time_to_seconds(value.time())
    if isinstance(value, time):
        return _time_to_seconds(value)
    if isinstance(value, (int, float)):
        return round(value * SECONDS_PER_DAY)
    if isinstance(value, str):
        return _text_to_seconds(value)
    raise ValueError(f"Cannot read worked hours from {value!r}")


def _time_to_seconds(value: time) -> int:
    return round(value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1_000_000)


def _text_to_seconds(text: str) -> int:
    text = text.strip()
    if not text:
        return 0
    match = _TIME_TEXT.match(text)
    if match:
        hours, minutes, seconds = (int(part or 0) for part in match.groups())
        return hours * 3600 + minutes * 60 + seconds
    try:
        return round(float(text) * SECONDS_PER_DAY)  # a day fraction stored as text
    except ValueError:
        raise ValueError(f"Cannot read worked hours from {text!r}") from None


def format_seconds(seconds: int) -> str:
    """Format seconds as HH:MM:SS (used for tooltips and reports)."""
    hours, rest = divmod(max(seconds, 0), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def calculate_status(schedule: str, worked_seconds: int, thresholds: Thresholds | None = None) -> str:
    """Return P / A / OFF / PH for one employee-day, or "?" for an unknown schedule."""
    if thresholds is None:
        thresholds = Thresholds.from_settings(get_settings())
    schedule = (schedule or "").strip().upper()

    if schedule in WORKING_SCHEDULES:
        return "P" if worked_seconds >= thresholds.workday_seconds else "A"
    if schedule == "OFF":
        return "P" if worked_seconds >= thresholds.offday_seconds else "OFF"
    if schedule == "PH":
        return "P" if worked_seconds >= thresholds.ph_seconds else "PH"
    return UNKNOWN_STATUS
