"""Read the uploaded attendance workbook.

Only the `TO CONSIDER` and `SAP` sheets are read. `HC_Teamlease` is never
touched: openpyxl in read-only mode parses a sheet only when it is iterated, so
opening the file does not load it.

This module only *reads*; turning the data into the grid and the exceptions
report happens in `app/core/grid.py`.
"""

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from app.core.rules import to_seconds

TO_CONSIDER_SHEET = "TO CONSIDER"
SAP_SHEET = "SAP"
HEADER_ROW = 1

# Cached values Excel writes for failed formulas; treated as "missing".
ERROR_VALUES = {"#N/A", "#REF!", "#VALUE!", "#NAME?", "#DIV/0!", "#NUM!", "#NULL!", "#SPILL!", "#CALC!"}

# TO CONSIDER identity columns, found by header name.
TC_FIELDS = {
    "employee id": "employee_id",
    "employee name": "employee_name",
    "doj": "doj",
    "emp status": "emp_status",
    "lwd": "lwd",
    "department": "department",
    "parent org": "parent_org",
    "emp email id": "emp_email",
    "manager id": "manager_id",
    "manager name": "manager_name",
    "manager email": "manager_email",
    "manager status": "manager_status",
}
TC_REQUIRED = ["employee id", "employee name", "manager name", "manager email"]

SAP_REQUIRED = {
    "employee id": "employee_id",
    "employee name": "employee_name",
    "date": "date",
    "work schedule": "work_schedule",
    "worked hours": "worked_hours",
}

# Status counted by each total column, by header, for uploads where the total formulas
# are still empty. The two "Maternity Leave" columns count ML and then PL.
TOTAL_HEADER_CODES = {
    "present": ["P"], "absent": ["A"], "weekly off": ["OFF"], "comp off": ["CO"],
    "maternity leave": ["ML", "PL"], "paternity leave": ["PL"], "ph": ["PH"], "total": [None],
}

# SAP columns the macro fills with formulas (empty in the file HR uploads).
SAP_OUTPUT_COLUMNS = {"remarks": "remarks", "remarks code": "remarks_code"}

# The first date column is N (14) in the current file; dates are never searched before it.
FIRST_DATE_COLUMN = 14

_COUNTIF_CODE = re.compile(r'COUNTIF\s*\([^,]+,\s*"([^"]*)"\s*\)', re.IGNORECASE)
_TEXT_DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d")

MISSING_CACHE_MESSAGE = "Please open the file in Excel, save it, and upload again."


class WorkbookError(Exception):
    """A problem with the uploaded file that HR must fix before it can be processed."""


@dataclass
class Issue:
    """One line in the exceptions report shown to HR."""

    type: str
    severity: str  # "error" | "warning" | "info"
    message: str
    employee_id: str | None = None
    employee_name: str | None = None
    date: str | None = None  # ISO date


@dataclass
class TotalColumn:
    letter: str
    header: str
    code: str | None  # status counted by COUNTIF, or None for Total (SUM)
    has_formula: bool  # False when the cell is still empty and the app must write the formula


@dataclass
class SheetLayout:
    header_row: int
    date_columns: dict[str, str]  # ISO date -> column letter
    employee_rows: dict[str, int]  # employee id -> row number
    total_columns: list[TotalColumn]
    # SAP column letters by field: employee_id, date, work_schedule, worked_hours, remarks, remarks_code
    sap_columns: dict[str, str] = field(default_factory=dict)
    sap_last_row: int = 0
    employee_id_column: str = "A"

    @property
    def counted_statuses(self) -> set[str]:
        return {col.code for col in self.total_columns if col.code}


@dataclass
class EmployeeRow:
    """One TO CONSIDER row."""

    employee_id: str
    employee_name: str
    row_number: int
    doj: date | None
    emp_status: str | None
    lwd: date | None
    department: str | None
    parent_org: str | None
    emp_email: str | None
    manager_id: str | None
    manager_name: str | None
    manager_email: str | None
    manager_status: str | None
    days: dict[str, str | None]  # ISO date -> status in the sheet (None = empty cell)


@dataclass
class SapDay:
    schedule: str
    worked_seconds: int


@dataclass
class WorkbookData:
    dates: list[date]
    layout: SheetLayout
    employees: list[EmployeeRow]
    sap_present: bool
    sap_days: dict[tuple[str, str], SapDay]  # (employee id, ISO date) -> day
    sap_names: dict[str, str]  # employee id -> name, for employees found in SAP
    issues: list[Issue] = field(default_factory=list)


# ---------- small value helpers ----------

def normalize_header(value: object) -> str:
    """Lower-case and collapse whitespace, so "Remarks\\nCode" matches "remarks code"."""
    return " ".join(str(value).split()).lower() if value is not None else ""


def is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip()
        return text == "" or text.upper() in ERROR_VALUES
    return False


def clean_text(value: object) -> str | None:
    """Return trimmed text, or None for empty / error values."""
    if is_missing(value):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def normalize_id(value: object) -> str | None:
    """Return an ID as a string of digits ("10027539"), or None if missing/invalid.

    Handles 10027539, 10027539.0, "10027539" and "10027539.0". 0 counts as missing.
    """
    if is_missing(value) or isinstance(value, bool):
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if not text.isdigit() or int(text) == 0:
        return None
    return str(int(text))


def normalize_email(value: object) -> str | None:
    """Lower-cased, trimmed email, or None for empty / error / 0."""
    text = clean_text(value)
    if text is None or text == "0":
        return None
    return text.lower()


def parse_date(value: object) -> date | None:
    """Return a date for a datetime/date cell or day-first text like 20-09-2026."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        for fmt in _TEXT_DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def normalize_status(value: object) -> str | None:
    text = clean_text(value)
    return text.upper() if text else None


# ---------- workbook loading ----------

def _open(source: Path | str | bytes | BinaryIO, data_only: bool):
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    elif hasattr(source, "seek"):
        source.seek(0)
    try:
        return load_workbook(source, read_only=True, data_only=data_only, keep_links=False)
    except Exception as exc:  # openpyxl raises several types for bad files
        raise WorkbookError("This file could not be opened as an Excel workbook (.xlsm or .xlsx).") from exc


def _find_sheet(wb, wanted: str):
    for name in wb.sheetnames:
        if name.strip().upper() == wanted:
            return wb[name]
    return None


def read_workbook(source: Path | str | bytes | BinaryIO) -> WorkbookData:
    """Read SAP and TO CONSIDER from the workbook. Raises WorkbookError for fatal problems."""
    values_wb = _open(source, data_only=True)
    try:
        tc_sheet = _find_sheet(values_wb, TO_CONSIDER_SHEET)
        if tc_sheet is None:
            raise WorkbookError(
                "The workbook has no 'TO CONSIDER' sheet. Please check that you uploaded the attendance "
                "workbook downloaded from SAP."
            )
        tc_rows = list(tc_sheet.iter_rows(min_row=HEADER_ROW, values_only=True))
        sap_sheet = _find_sheet(values_wb, SAP_SHEET)
        sap_rows = list(sap_sheet.iter_rows(min_row=HEADER_ROW, values_only=True)) if sap_sheet else None
    finally:
        values_wb.close()

    issues: list[Issue] = []
    header = tc_rows[0] if tc_rows else ()
    field_columns = _find_tc_columns(header)
    dates, date_indexes = _find_date_columns(header, start=max(field_columns.values()) + 1)
    total_columns = _read_total_columns(source, header, after=date_indexes[-1] + 1)
    employees = _read_tc_employees(tc_rows[1:], field_columns, dates, date_indexes, issues)

    layout = SheetLayout(
        header_row=HEADER_ROW,
        date_columns={d.isoformat(): get_column_letter(i + 1) for d, i in zip(dates, date_indexes)},
        employee_rows={e.employee_id: e.row_number for e in employees},
        total_columns=total_columns,
        employee_id_column=get_column_letter(field_columns["employee_id"] + 1),
    )

    sap_days: dict[tuple[str, str], SapDay] = {}
    sap_names: dict[str, str] = {}
    if sap_rows is not None:
        sap_days, sap_names, layout.sap_columns, layout.sap_last_row = _read_sap(sap_rows, issues)

    return WorkbookData(
        dates=dates,
        layout=layout,
        employees=employees,
        sap_present=sap_rows is not None,
        sap_days=sap_days,
        sap_names=sap_names,
        issues=issues,
    )


# ---------- TO CONSIDER ----------

def _find_tc_columns(header: tuple) -> dict[str, int]:
    """Map our field names to 0-based column indexes, by header name."""
    by_name: dict[str, int] = {}
    for index, cell in enumerate(header[: FIRST_DATE_COLUMN - 1]):
        key = normalize_header(cell)
        if key in TC_FIELDS and key not in by_name:
            by_name[key] = index
    missing = [name for name in TC_REQUIRED if name not in by_name]
    if missing:
        pretty = ", ".join(f"'{name.title()}'" for name in missing)
        raise WorkbookError(f"The 'TO CONSIDER' sheet is missing these column headers in row 1: {pretty}.")
    return {TC_FIELDS[key]: index for key, index in by_name.items()}


def _find_date_columns(header: tuple, start: int) -> tuple[list[date], list[int]]:
    """Find the run of date headers starting at column N (or after the last identity column)."""
    index = max(start, FIRST_DATE_COLUMN - 1)
    # Skip blank cells between the identity columns and the first date.
    while index < len(header) and header[index] is None:
        index += 1
    dates: list[date] = []
    indexes: list[int] = []
    while index < len(header):
        day = parse_date(header[index])
        if day is None:
            break
        dates.append(day)
        indexes.append(index)
        index += 1
    if not dates:
        raise WorkbookError("No date columns were found in row 1 of the 'TO CONSIDER' sheet (expected from column N).")
    if len(set(dates)) != len(dates):
        raise WorkbookError("The 'TO CONSIDER' sheet has the same date in more than one column.")
    return dates, indexes


def _read_total_columns(source, header: tuple, after: int) -> list[TotalColumn]:
    """Read the totals columns and the status code each COUNTIF counts (from the first data row's formula)."""
    formulas_wb = _open(source, data_only=False)
    try:
        sheet = _find_sheet(formulas_wb, TO_CONSIDER_SHEET)
        first_row = next(sheet.iter_rows(min_row=HEADER_ROW + 1, max_row=HEADER_ROW + 1, values_only=True), ())
    finally:
        formulas_wb.close()

    columns: list[TotalColumn] = []
    header_uses: dict[str, int] = {}  # how many times each header has been seen (for the two Maternity Leave columns)
    for index in range(after, len(header)):
        title = normalize_header(header[index])
        if not title:
            continue
        formula = first_row[index] if index < len(first_row) else None
        letter = get_column_letter(index + 1)
        pretty = " ".join(str(header[index]).split())

        if isinstance(formula, str) and formula.startswith("="):
            # Prefer the formula: it says exactly which code is counted.
            match = _COUNTIF_CODE.search(formula)
            columns.append(TotalColumn(letter, pretty, match.group(1).strip().upper() if match else None, True))
        elif title in TOTAL_HEADER_CODES:
            # Empty cell (the usual upload): take the code from the header.
            codes = TOTAL_HEADER_CODES[title]
            use = header_uses.get(title, 0)
            header_uses[title] = use + 1
            if use < len(codes):
                columns.append(TotalColumn(letter, pretty, codes[use], False))
    return columns


def _cell(row: tuple, index: int | None) -> object:
    if index is None or index >= len(row):
        return None
    return row[index]


def _read_tc_employees(
    rows: list[tuple],
    columns: dict[str, int],
    dates: list[date],
    date_indexes: list[int],
    issues: list[Issue],
) -> list[EmployeeRow]:
    employees: list[EmployeeRow] = []
    seen: set[str] = set()
    manager_email_cached = False

    for row_number, row in enumerate(rows, start=HEADER_ROW + 1):
        if all(value is None for value in row):
            continue
        emp_id = normalize_id(_cell(row, columns["employee_id"]))
        if emp_id is None:
            issues.append(Issue(
                type="invalid_employee_id", severity="warning",
                message=f"Row {row_number} of TO CONSIDER has no valid Employee ID and was skipped.",
            ))
            continue
        if emp_id in seen:
            issues.append(Issue(
                type="duplicate_employee", severity="error", employee_id=emp_id,
                message=f"Employee ID appears more than once in TO CONSIDER (row {row_number} was skipped).",
            ))
            continue
        seen.add(emp_id)

        raw_manager_email = _cell(row, columns["manager_email"])
        if raw_manager_email is not None:
            manager_email_cached = True

        get = lambda name: _cell(row, columns.get(name))  # noqa: E731 - short local helper
        lwd = get("lwd")
        employees.append(EmployeeRow(
            employee_id=emp_id,
            employee_name=clean_text(get("employee_name")) or "",
            row_number=row_number,
            doj=parse_date(get("doj")),
            emp_status=clean_text(get("emp_status")),
            lwd=parse_date(lwd),
            department=clean_text(get("department")),
            parent_org=clean_text(get("parent_org")),
            emp_email=normalize_email(get("emp_email")),
            manager_id=normalize_id(get("manager_id")),
            manager_name=clean_text(get("manager_name")),
            manager_email=normalize_email(raw_manager_email),
            manager_status=clean_text(get("manager_status")),
            days={day.isoformat(): normalize_status(_cell(row, i)) for day, i in zip(dates, date_indexes)},
        ))

    if employees and not manager_email_cached:
        # Formula columns without cached values: the file was produced by a tool, not saved by Excel.
        raise WorkbookError(MISSING_CACHE_MESSAGE)
    if not employees:
        raise WorkbookError("The 'TO CONSIDER' sheet has no employee rows.")
    return employees


# ---------- SAP ----------

def _read_sap(rows: list[tuple], issues: list[Issue]):
    header = rows[0] if rows else ()
    columns: dict[str, int] = {}
    for index, cell in enumerate(header):
        key = normalize_header(cell)
        if key in SAP_REQUIRED and SAP_REQUIRED[key] not in columns:
            columns[SAP_REQUIRED[key]] = index
    missing = [name for name, field_name in SAP_REQUIRED.items() if field_name not in columns]
    if missing:
        pretty = ", ".join(f"'{name.title()}'" for name in missing)
        raise WorkbookError(f"The 'SAP' sheet is missing these column headers in row 1: {pretty}.")

    letters = {name: get_column_letter(index + 1) for name, index in columns.items()}
    for index, cell in enumerate(header):
        key = normalize_header(cell)
        if key in SAP_OUTPUT_COLUMNS and SAP_OUTPUT_COLUMNS[key] not in letters:
            letters[SAP_OUTPUT_COLUMNS[key]] = get_column_letter(index + 1)

    sap_days: dict[tuple[str, str], SapDay] = {}
    sap_names: dict[str, str] = {}
    last_row = HEADER_ROW
    for row_number, row in enumerate(rows[1:], start=HEADER_ROW + 1):
        if all(value is None for value in row):
            continue
        emp_id = normalize_id(_cell(row, columns["employee_id"]))
        day = parse_date(_cell(row, columns["date"]))
        if emp_id is None or day is None:
            issues.append(Issue(
                type="invalid_sap_row", severity="warning",
                message=f"Row {row_number} of SAP has no valid Employee ID or Date and was skipped.",
            ))
            continue
        last_row = row_number
        sap_names.setdefault(emp_id, clean_text(_cell(row, columns["employee_name"])) or "")

        try:
            worked = to_seconds(_cell(row, columns["worked_hours"]))
        except ValueError:
            issues.append(Issue(
                type="invalid_worked_hours", severity="warning", employee_id=emp_id, date=day.isoformat(),
                message=f"Row {row_number} of SAP has unreadable Worked Hours; treated as 0.",
            ))
            worked = 0

        key = (emp_id, day.isoformat())
        if key in sap_days:
            issues.append(Issue(
                type="duplicate_sap_row", severity="warning", employee_id=emp_id, date=day.isoformat(),
                message=f"SAP has more than one row for this employee and date; row {row_number} was ignored.",
            ))
            continue
        schedule = (clean_text(_cell(row, columns["work_schedule"])) or "").upper()
        sap_days[key] = SapDay(schedule=schedule, worked_seconds=worked)
    return sap_days, sap_names, letters, last_row
