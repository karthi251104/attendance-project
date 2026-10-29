"""Row totals shown in the grid (read-only). Same counts as the COUNTIF columns in Excel."""

from collections import Counter

TOTAL_CODES = ["P", "A", "OFF", "PH", "CO", "ML", "PL"]


def compute_totals(days: dict[str, str | None]) -> dict[str, int]:
    counts = Counter(value for value in days.values() if value)
    totals = {code: counts.get(code, 0) for code in TOTAL_CODES}
    totals["Total"] = sum(totals.values())
    return totals
