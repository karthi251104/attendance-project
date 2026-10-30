"""Side-by-side check: does the app produce what the Excel macro produces?

Usage (from the repo root):
    python -m scripts.side_by_side path/to/macro_output.xlsm [--save-dir DIR]

Give it a workbook on which the macro has already run. The script:
  1. makes the "as HR uploads it" version: deletes the TO CONSIDER sheet and clears
     SAP Remarks / Remarks Code,
  2. runs that through the app's pipeline (read -> calculate -> write back),
  3. compares the app's TO CONSIDER with the macro's, column by column (matched by header,
     row by row matched by Employee ID), and the SAP sheet cell by cell, and checks that
     every other part of the file (HC_Teamlease, VBA, ...) is byte-identical.

The macro's TO CONSIDER holds VLOOKUP formulas; their cached values are compared with the
values the app writes. Columns the app does not build (such as the macro's column D) are
listed but not counted as differences. Where VLOOKUP shows 0 for an empty HC_Teamlease
cell, the app leaves the cell empty; that is not counted as a difference either.

Nothing is written next to the input file. With --save-dir, the upload copy and the app's
output are saved there so you can open them in Excel. Never commit them. It prints employee
IDs and dates only, never names. Exit code 0 = no differences.
"""

import argparse
import sys
import time
import zipfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from app.config import get_settings
from app.core.grid import build_cycle_data
from app.core.processing import layout_to_doc
from app.core.rules import Thresholds
from app.core.workbook_reader import SAP_SHEET, TO_CONSIDER_SHEET, normalize_id, read_workbook
from app.core.workbook_writer import build_updated_workbook
from app.core.xlsx_patch import XlsxPatcher

# Parts of the zip the app is allowed to change (besides the TO CONSIDER and SAP sheets).
EXPECTED_CHANGED = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels", "[Content_Types].xml", "xl/calcChain.xml",
                    "xl/styles.xml"}


def make_uploaded_copy(original: bytes) -> bytes:
    """Delete TO CONSIDER and clear SAP Remarks / Remarks Code, keeping every other cell and style."""
    patcher = XlsxPatcher(original)
    patcher.remove_sheet(TO_CONSIDER_SHEET)
    without_tc = patcher.save()

    layout = read_workbook(without_tc).layout
    patcher = XlsxPatcher(without_tc)
    sap = patcher.sheet(SAP_SHEET)
    for field_name in ("remarks", "remarks_code"):
        column = layout.sap_columns.get(field_name)
        for row in range(2, layout.sap_last_row + 1):
            if column:
                sap.clear(f"{column}{row}")
    patcher.drop_calc_chain()  # its entries point at the formulas we just removed
    return patcher.save()


def run_app(uploaded: bytes):
    settings = get_settings()
    cycle = build_cycle_data(read_workbook(uploaded), settings)
    employees = [asdict(emp) for emp in cycle.employees]
    out, report = build_updated_workbook(uploaded, layout_to_doc(cycle), employees, Thresholds.from_settings(settings))
    return out, report, cycle


def _sheet(wb, name: str):
    return next(wb[n] for n in wb.sheetnames if n.strip().upper() == name)


def read_table(data: bytes, data_only: bool) -> tuple[list, dict[str, dict]]:
    """TO CONSIDER as (column keys in order, {employee id: {column key: value}}).

    A column key is the header, numbered when it repeats ("Maternity Leave", "Maternity Leave #2").
    Date headers become ISO dates.
    """
    wb = load_workbook(BytesIO(data), read_only=True, data_only=data_only)
    try:
        rows = list(_sheet(wb, TO_CONSIDER_SHEET).iter_rows(values_only=True))
    finally:
        wb.close()
    seen: Counter = Counter()
    keys = []
    for value in rows[0]:
        if isinstance(value, datetime):
            key = value.date().isoformat()
        else:
            key = " ".join(str(value).split()) if value is not None else None
        if key is not None:
            seen[key] += 1
            key = key if seen[key] == 1 else f"{key} #{seen[key]}"
        keys.append(key)
    table = {}
    for row in rows[1:]:
        emp_id = normalize_id(row[0]) if row else None
        if emp_id:
            table[emp_id] = {key: value for key, value in zip(keys, row) if key is not None}
    return [k for k in keys if k is not None], table


def _same(macro: object, app: object) -> bool:
    if macro in (0, None, "") and app in (None, ""):
        return True  # VLOOKUP of an empty cell shows 0; the app leaves it empty
    if isinstance(macro, float) and macro.is_integer():
        macro = int(macro)
    if isinstance(app, float) and app.is_integer():
        app = int(app)
    return macro == app


def compare_to_consider(macro: bytes, app: bytes) -> list[str]:
    macro_keys, macro_rows = read_table(macro, data_only=True)
    app_keys, app_values = read_table(app, data_only=False)
    problems = []

    not_built = [k for k in macro_keys if k not in app_keys]
    extra = [k for k in app_keys if k not in macro_keys]
    if not_built:
        print(f"  Columns in the macro's sheet that the app does not build: {not_built}")
    problems += [f"TO CONSIDER: column {k!r} is only in the app's sheet" for k in extra]

    if list(macro_rows) != list(app_values):
        problems.append("TO CONSIDER: the employees or their order differ "
                        f"(macro {len(macro_rows)} rows, app {len(app_values)} rows)")

    totals = [k for k in app_keys if any(isinstance(r.get(k), str) and r[k].startswith("=") for r in app_values.values())]
    days = [k for k in app_keys if k[:4].isdigit()]

    def count(row: dict, formula: str) -> int:
        """What a total formula shows: COUNTIF of its code, or (Total) the number of counted days."""
        code = formula.split('"')[1] if '"' in formula else None
        return sum(1 for k in days if row.get(k) and (row[k] == code if code else row[k] != "?"))

    compared = Counter()
    for emp_id in macro_rows.keys() & app_values.keys():
        left, right = macro_rows[emp_id], app_values[emp_id]
        for key in app_keys:
            if key not in macro_keys:
                continue
            expected, value = left.get(key), right.get(key)
            if key in totals:
                value = count(right, value or "")
                if expected is None:  # no cached value (file not saved by Excel): count the macro's own days
                    expected = count(left, right.get(key) or "")
            compared[key] += 1
            if not _same(expected, value):
                problems.append(f"TO CONSIDER {emp_id} [{key}]: macro={expected!r} app={value!r}")
    print(f"  TO CONSIDER: {len(app_values)} employees, {sum(compared.values())} cells compared, "
          f"{len(problems)} different")
    return problems


def sheet_cells(data: bytes, sheet_name: str) -> dict[str, object]:
    wb = load_workbook(BytesIO(data), read_only=True, data_only=False)
    try:
        cells = {}
        for row in _sheet(wb, sheet_name).iter_rows():
            for cell in row:
                value = getattr(cell, "value", None)
                if value not in (None, ""):
                    cells[cell.coordinate] = value
        return cells
    finally:
        wb.close()


def compare_sheet(macro: bytes, app: bytes, sheet_name: str) -> list[str]:
    left, right = sheet_cells(macro, sheet_name), sheet_cells(app, sheet_name)
    problems = []
    for ref in sorted(set(left) | set(right), key=lambda r: (len(r), r)):
        if left.get(ref) != right.get(ref):
            problems.append(f"{sheet_name}!{ref}: macro={left.get(ref)!r} app={right.get(ref)!r}")
    print(f"  {sheet_name}: {len(left)} cells in macro file, {len(right)} in app file, {len(problems)} different")
    return problems


def compare_parts(macro: bytes, app: bytes) -> list[str]:
    a, b = zipfile.ZipFile(BytesIO(macro)), zipfile.ZipFile(BytesIO(app))
    allowed = EXPECTED_CHANGED | {XlsxPatcher(macro)._sheet_paths[n] for n in (TO_CONSIDER_SHEET, SAP_SHEET)}
    allowed |= {XlsxPatcher(app)._sheet_paths[n] for n in (TO_CONSIDER_SHEET, SAP_SHEET)}
    problems = []
    for name in sorted(set(a.namelist()) | set(b.namelist())):
        if name in allowed:
            continue
        if name not in a.namelist() or name not in b.namelist():
            problems.append(f"zip part {name} exists in only one file")
        elif a.read(name) != b.read(name):
            problems.append(f"zip part {name} differs")
    vba = "xl/vbaProject.bin"
    if vba in a.namelist():
        print(f"  VBA project: {'identical' if vba in b.namelist() and a.read(vba) == b.read(vba) else 'DIFFERENT'}")
    print(f"  Other zip parts: {len(set(a.namelist()) - allowed)} checked, {len(problems)} different")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--save-dir", type=Path)
    args = parser.parse_args(argv)
    macro = args.workbook.read_bytes()

    started = time.perf_counter()
    uploaded = make_uploaded_copy(macro)
    print(f"1. Made the 'as uploaded' copy: no TO CONSIDER, SAP Remarks cleared ({time.perf_counter() - started:.1f}s)")

    started = time.perf_counter()
    app, report, cycle = run_app(uploaded)
    print(f"2. App built the workbook in {time.perf_counter() - started:.1f}s: {report.employee_rows} employees, "
          f"{report.day_cells} day cells, {report.total_cells} total formulas, {report.sap_cells} SAP formulas")
    issues = Counter(issue.type for issue in cycle.exceptions)
    if issues:
        print(f"   Exceptions: {dict(sorted(issues.items()))}")

    print("3. Comparing the app's output with the macro's file:")
    problems = compare_to_consider(macro, app) + compare_sheet(macro, app, SAP_SHEET) + compare_parts(macro, app)

    if args.save_dir:
        args.save_dir.mkdir(parents=True, exist_ok=True)
        suffix = args.workbook.suffix
        (args.save_dir / f"as_uploaded{suffix}").write_bytes(uploaded)
        (args.save_dir / f"app_output{suffix}").write_bytes(app)
        print(f"Saved as_uploaded{suffix} and app_output{suffix} in {args.save_dir}")

    for line in problems[:50]:
        print("  DIFF", line)
    print("RESULT:", "SAME as the macro output" if not problems else f"{len(problems)} differences")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
