"""Build the updated workbook for download, doing what the Excel macro does.

Into the original uploaded file it writes:
  1. TO CONSIDER day cells where the portal value differs from what the sheet held at
     upload (`days` != `sheet_days`). These are the days filled from SAP and the days
     edited in the portal. Days that already had a value and were not edited are never
     touched, even if SAP calculates something else.
  2. The macro's COUNTIF / SUM formulas into total cells that are empty.
  3. The macro's Remarks / Remarks Code formulas into SAP cells that are empty.
Cells that already hold a formula are never overwritten. Nothing else changes.
"""

import logging
from dataclasses import dataclass, field
from app.core.rules import UNKNOWN_STATUS, Thresholds
from app.core.workbook_reader import SAP_SHEET, TO_CONSIDER_SHEET, normalize_id
from app.core.xlsx_patch import XlsxPatcher, column_index

log = logging.getLogger(__name__)


class WriterError(Exception):
    pass


@dataclass
class WriteReport:
    day_cells: int = 0
    total_cells: int = 0
    sap_cells: int = 0
    skipped: list[str] = field(default_factory=list)


def _time_text(seconds: int) -> str:
    """The macro compares with '> TIME(4,59,59)', i.e. one second below the threshold."""
    seconds -= 1
    return f"TIME({seconds // 3600},{seconds % 3600 // 60},{seconds % 60})"


def remarks_formula(row: int, schedule_col: str, worked_col: str, thresholds: Thresholds, numeric: bool) -> str:
    """The macro's SAP formula: Remarks (1=P 2=A 3=OFF 4=PH) or Remarks Code (P/A/OFF/PH)."""
    p, a, off, ph = ("1", "2", "3", "4") if numeric else ('"P"', '"A"', '"OFF"', '"PH"')
    g, r = f"{schedule_col}{row}", f"{worked_col}{row}"
    work, offday, holiday = (_time_text(t) for t in (thresholds.workday_seconds, thresholds.offday_seconds, thresholds.ph_seconds))
    return (f'IF({g}="PH",IF({r}>{holiday},{p},{ph}),'
            f'IF(OR({g}="5DAG",{g}="6DAG"),IF({r}>{work},{p},{a}),'
            f'IF({g}="OFF",IF({r}>{offday},{p},{off}))))')


def _employee_id_at(patcher: XlsxPatcher, column: str, row: int) -> str | None:
    """Normalised Employee ID in TO CONSIDER at (column, row), read straight from the XML."""
    text = patcher.cell_text(TO_CONSIDER_SHEET, f"{column}{row}")
    try:
        return normalize_id(float(text)) if text and text.replace(".", "", 1).isdigit() else normalize_id(text)
    except ValueError:
        return None


def build_updated_workbook(original: bytes, layout: dict, employees: list[dict],
                           thresholds: Thresholds) -> tuple[bytes, WriteReport]:
    report = WriteReport()
    patcher = XlsxPatcher(original)
    tc = patcher.sheet(TO_CONSIDER_SHEET)
    date_columns: dict[str, str] = layout["date_columns"]
    employee_rows: dict[str, int] = layout["employee_rows"]
    id_column = layout.get("employee_id_column", "A")

    # 1. Day cells.
    for emp in employees:
        row = employee_rows.get(emp["employee_id"])
        if row is None or _employee_id_at(patcher, id_column, row) != emp["employee_id"]:
            raise WriterError(f"TO CONSIDER row for employee {emp['employee_id']} does not match the saved layout.")
        sheet_days = emp.get("sheet_days", {})
        for iso, value in emp["days"].items():
            if not value or value == UNKNOWN_STATUS or value == sheet_days.get(iso) or iso not in date_columns:
                continue
            ref = f"{date_columns[iso]}{row}"
            if tc.state(ref).kind == "formula":
                report.skipped.append(f"TO CONSIDER!{ref} holds a formula and was not overwritten")
                continue
            tc.set_text(ref, value)
            report.day_cells += 1

    # 2. Total formulas (only into empty cells).
    letters = sorted(date_columns.values(), key=column_index)
    first, last = letters[0], letters[-1]
    totals = layout.get("total_columns", [])
    counted = [col["letter"] for col in totals if col["code"]]
    for row in sorted(employee_rows.values()):
        for col in totals:
            ref = f"{col['letter']}{row}"
            if tc.state(ref).kind != "empty":
                continue
            if col["code"]:
                tc.set_formula(ref, f'COUNTIF({first}{row}:{last}{row},"{col["code"]}")')
            elif counted:
                tc.set_formula(ref, f"SUM({counted[0]}{row}:{counted[-1]}{row})")
            else:
                continue
            report.total_cells += 1

    # 3. SAP Remarks / Remarks Code formulas (only into empty cells).
    sap_columns: dict[str, str] = layout.get("sap_columns", {})
    if sap_columns.get("work_schedule") and sap_columns.get("worked_hours"):
        sap = patcher.sheet(SAP_SHEET)
        for field_name, numeric in (("remarks", True), ("remarks_code", False)):
            column = sap_columns.get(field_name)
            if not column:
                continue
            for row in range(layout["header_row"] + 1, layout.get("sap_last_row", 0) + 1):
                ref = f"{column}{row}"
                if sap.state(ref).kind == "empty":
                    sap.set_formula(ref, remarks_formula(row, sap_columns["work_schedule"], sap_columns["worked_hours"], thresholds, numeric))
                    report.sap_cells += 1

    patcher.set_full_calc_on_load()
    log.info("Workbook built: day_cells=%d total_cells=%d sap_cells=%d skipped=%d",
             report.day_cells, report.total_cells, report.sap_cells, len(report.skipped))
    return patcher.save(), report
