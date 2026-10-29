"""Acceptance test: check that our rules reproduce the macro on a real workbook.

Usage (from the repo root):
    python -m scripts.verify_against_macro path/to/real.xlsm

Recalculates every employee-day from the SAP sheet and compares it with the
status already in the TO CONSIDER sheet. Prints match / mismatch counts and every
mismatch (employee ID and date only, no names). Exit code 0 = no mismatches.

The file is only read, never changed. Do not commit real workbooks.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from app.core.grid import build_cycle_data
from app.core.rules import format_seconds
from app.core.workbook_reader import WorkbookError, read_workbook


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workbook", type=Path, help="path to the .xlsm / .xlsx file")
    args = parser.parse_args(argv)

    try:
        data = read_workbook(args.workbook)
    except WorkbookError as error:
        print(f"ERROR: {error}")
        return 2
    cycle = build_cycle_data(data)

    print(f"Cycle: {cycle.dates[0]:%d/%m/%Y} - {cycle.dates[-1]:%d/%m/%Y} ({len(cycle.dates)} days)")
    print(f"Employees in TO CONSIDER: {len(cycle.employees)}   in SAP: {len(data.sap_names)}")
    print(f"SAP rows read: {len(data.sap_days)}")
    if not data.sap_present:
        print("ERROR: the workbook has no SAP sheet, nothing to verify.")
        return 2

    mismatches = len(cycle.mismatches)
    print(f"Empty TO CONSIDER cells filled from SAP: {cycle.filled_days}")
    print(f"Employee-days compared: {cycle.compared_days}")
    if cycle.compared_days == 0:
        print("Nothing to compare: the TO CONSIDER date cells are empty, so the grid comes from SAP.")
        print("To prove the rules match the macro, run this on a file where the macro has filled the days.")
    print(f"Matches:    {cycle.compared_days - mismatches}")
    print(f"Mismatches: {mismatches}")
    for m in cycle.mismatches:
        sap_day = data.sap_days[(m.employee_id, m.date)]
        print(f"  {m.employee_id}  {m.date}  TO CONSIDER={m.to_consider}  SAP={m.calculated}"
              f"  ({sap_day.schedule} · {format_seconds(sap_day.worked_seconds)})")

    other = Counter(i.type for i in cycle.exceptions if i.type != "status_mismatch")
    if other:
        print("Other exceptions:")
        for issue_type, count in sorted(other.items()):
            print(f"  {issue_type}: {count}")
    return 0 if mismatches == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
