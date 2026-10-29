"""Side-by-side check: does the app produce exactly what the Excel macro produces?

Usage (from the repo root):
    python -m scripts.side_by_side path/to/macro_output.xlsm [--save-dir DIR]

Give it a workbook on which the macro has already run. The script:
  1. makes the "as HR uploads it" version by clearing what the macro fills in
     (TO CONSIDER day cells and totals, SAP Remarks / Remarks Code),
  2. runs that through the app's pipeline (read -> calculate -> write back),
  3. compares the app's output with the macro's file, cell by cell, in both sheets,
     and checks that every other part of the file (other sheets, VBA, styles) is byte-identical.

Nothing is written next to the input file. With --save-dir, the cleared upload and the
app's output are saved there so you can open them in Excel. Never commit them.
Exit code 0 = identical.
"""

import argparse
import sys
import time
import zipfile
from dataclasses import asdict
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from app.config import get_settings
from app.core.grid import build_cycle_data
from app.core.processing import layout_to_doc
from app.core.rules import Thresholds
from app.core.workbook_reader import SAP_SHEET, TO_CONSIDER_SHEET, read_workbook
from app.core.workbook_writer import build_updated_workbook
from app.core.xlsx_patch import XlsxPatcher

# Parts of the zip the app is allowed to change.
EXPECTED_CHANGED = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels", "[Content_Types].xml", "xl/calcChain.xml"}


def make_uploaded_copy(original: bytes) -> bytes:
    """Clear the cells the macro fills, keeping every cell's style."""
    data = read_workbook(original)
    layout = data.layout
    patcher = XlsxPatcher(original)
    tc = patcher.sheet(TO_CONSIDER_SHEET)
    clear_columns = list(layout.date_columns.values()) + [col.letter for col in layout.total_columns]
    for row in layout.employee_rows.values():
        for column in clear_columns:
            tc.clear(f"{column}{row}")
    sap = patcher.sheet(SAP_SHEET)
    for field_name in ("remarks", "remarks_code"):
        column = layout.sap_columns.get(field_name)
        for row in range(2, layout.sap_last_row + 1):
            if column:
                sap.clear(f"{column}{row}")
    patcher.drop_calc_chain()  # its entries point at the formulas we just removed
    return patcher.save()


def run_app(uploaded: bytes) -> tuple[bytes, object]:
    settings = get_settings()
    cycle = build_cycle_data(read_workbook(uploaded), settings)
    employees = [asdict(emp) for emp in cycle.employees]
    return build_updated_workbook(uploaded, layout_to_doc(cycle), employees, Thresholds.from_settings(settings))


def sheet_cells(data: bytes, sheet_name: str) -> dict[str, object]:
    wb = load_workbook(BytesIO(data), read_only=True, data_only=False)
    try:
        sheet = next(wb[n] for n in wb.sheetnames if n.strip().upper() == sheet_name)
        cells = {}
        for row in sheet.iter_rows():
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


def compare_parts(macro: bytes, app: bytes, changed_sheets: set[str]) -> list[str]:
    a, b = zipfile.ZipFile(BytesIO(macro)), zipfile.ZipFile(BytesIO(app))
    allowed = EXPECTED_CHANGED | changed_sheets
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
    print(f"  Other zip parts: {len(a.namelist()) - len(allowed & set(a.namelist()))} checked, "
          f"{len(problems)} different")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--save-dir", type=Path)
    args = parser.parse_args(argv)
    macro = args.workbook.read_bytes()

    started = time.perf_counter()
    uploaded = make_uploaded_copy(macro)
    print(f"1. Made the 'as uploaded' copy ({time.perf_counter() - started:.1f}s)")
    check = build_cycle_data(read_workbook(uploaded))
    print(f"   Day cells now empty: {check.filled_days} (all filled from SAP by the app)")

    started = time.perf_counter()
    app, report = run_app(uploaded)
    print(f"2. App built the workbook in {time.perf_counter() - started:.1f}s: "
          f"{report.day_cells} day cells, {report.total_cells} total formulas, {report.sap_cells} SAP formulas")

    print("3. Comparing the app's output with the macro's file:")
    patcher = XlsxPatcher(macro)
    sheet_parts = {patcher._sheet_paths[TO_CONSIDER_SHEET], patcher._sheet_paths[SAP_SHEET]}
    problems = compare_sheet(macro, app, TO_CONSIDER_SHEET) + compare_sheet(macro, app, SAP_SHEET)
    problems += compare_parts(macro, app, sheet_parts)
    problems += [f"writer skipped: {s}" for s in report.skipped]

    if args.save_dir:
        args.save_dir.mkdir(parents=True, exist_ok=True)
        suffix = args.workbook.suffix
        (args.save_dir / f"as_uploaded{suffix}").write_bytes(uploaded)
        (args.save_dir / f"app_output{suffix}").write_bytes(app)
        print(f"Saved as_uploaded{suffix} and app_output{suffix} in {args.save_dir}")

    for line in problems[:50]:
        print("  DIFF", line)
    print("RESULT:", "IDENTICAL to the macro output" if not problems else f"{len(problems)} differences")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
