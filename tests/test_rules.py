from datetime import datetime, time, timedelta

import pytest

from app.core.rules import Thresholds, calculate_status, format_seconds, to_seconds

DEFAULT = Thresholds()


def hms(h: int, m: int = 0, s: int = 0) -> int:
    return h * 3600 + m * 60 + s


# ---------- to_seconds ----------

@pytest.mark.parametrize(
    "value, expected",
    [
        (None, 0),
        ("", 0),
        ("   ", 0),
        (time(9, 8, 8), hms(9, 8, 8)),
        (time(0, 0, 0), 0),
        (time(4, 59, 59, 999_999), hms(5)),  # float noise rounds to the nearest second
        (timedelta(hours=9, minutes=8, seconds=8), hms(9, 8, 8)),
        (timedelta(hours=26), hms(26)),
        (datetime(1899, 12, 31, 2, 0, 0), hms(26)),  # Excel duration > 24h
        (datetime(2026, 9, 21, 9, 8, 8), hms(9, 8, 8)),  # a real date: use the time part
        (0.375, hms(9)),
        (5 / 24, hms(5)),
        (4 / 24 + 59 / 1440 + 59 / 86400, hms(4, 59, 59)),
        (0, 0),
        ("09:08:08", hms(9, 8, 8)),
        ("9:08", hms(9, 8)),
        (" 04:59:59 ", hms(4, 59, 59)),
        ("0.375", hms(9)),
    ],
)
def test_to_seconds(value: object, expected: int) -> None:
    assert to_seconds(value) == expected


@pytest.mark.parametrize("value", ["abc", "9h", True, object()])
def test_to_seconds_rejects_garbage(value: object) -> None:
    with pytest.raises(ValueError):
        to_seconds(value)


def test_format_seconds() -> None:
    assert format_seconds(hms(9, 8, 8)) == "09:08:08"
    assert format_seconds(0) == "00:00:00"


# ---------- calculate_status ----------

@pytest.mark.parametrize(
    "schedule, worked, expected",
    [
        ("5DAG", hms(4, 59, 59), "A"),
        ("5DAG", hms(5), "P"),
        ("6DAG", hms(4, 59, 59), "A"),
        ("6DAG", hms(5), "P"),
        ("6DAG", 0, "A"),
        ("OFF", 0, "OFF"),
        ("OFF", hms(5), "OFF"),
        ("OFF", hms(7, 59, 59), "OFF"),
        ("OFF", hms(8), "P"),
        ("PH", 0, "PH"),
        ("PH", hms(4, 59, 59), "PH"),
        ("PH", hms(5), "P"),
        (" 6dag ", hms(9), "P"),  # whitespace and case are normalised
        ("NIGHT", hms(9), "?"),
        ("", hms(9), "?"),
    ],
)
def test_calculate_status(schedule: str, worked: int, expected: str) -> None:
    assert calculate_status(schedule, worked, DEFAULT) == expected


def test_calculate_status_uses_custom_thresholds() -> None:
    strict = Thresholds(workday_seconds=hms(6), offday_seconds=hms(9), ph_seconds=hms(6))
    assert calculate_status("6DAG", hms(5, 30), strict) == "A"
    assert calculate_status("OFF", hms(8), strict) == "OFF"
    assert calculate_status("PH", hms(6), strict) == "P"


def test_calculate_status_defaults_come_from_settings() -> None:
    assert calculate_status("6DAG", hms(5)) == "P"
    assert calculate_status("OFF", hms(7, 59, 59)) == "OFF"
