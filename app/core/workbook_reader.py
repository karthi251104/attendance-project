"""Read the uploaded attendance workbook and work out the TO CONSIDER sheet.

HR uploads the file straight from SAP, before the macro has run. It has two sheets:
  SAP           one row per employee per day (schedule and worked hours)
  HC_Teamlease  the headcount report (joining date, status, department, manager, ...)

The macro builds TO CONSIDER from them, and so does the app:
  - one row per employee in SAP, sorted by Employee ID (the macro's pivot table),
  - Employee ID and Name from SAP,
  - C-L looked up in HC_Teamlease by Employee ID, and the manager's email and status
    looked up by Manager ID (the macro's VLOOKUPs),
  - one column per SAP date, from column M.
The macro's column D (a lookup into another workbook) is not built.

This module only *reads*; turning the data into the grid and the exceptions report
happens in `app/core/grid.py`, and writing the sheet in `app/core/workbook_writer.py`.
"""

import io
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import BinaryIO

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from app.core.rules import to_seconds

TO_CONSIDER_SHEET = "TO CONSIDER"
SAP_SHEET = "SAP"
HC_SHEET = "HC_TEAMLEASE"
HEADER_ROW = 1

# What Excel's VLOOKUP shows when the Employee ID or Manager ID is not in HC_Teamlease.
NOT_FOUND = "#N/A"

# Cached values Excel writes for failed formulas; treated as "missing".
ERROR_VALUES = {"#N/A", "#REF!", "#VALUE!", "#NAME?", "#DIV/0!", "#NUM!", "#NULL!", "#SPILL!", "#CALC!"}

# TO CONSIDER columns A-L, in order: (header, where the value comes from).
# "sap" = the SAP sheet, "hc" = HC_Teamlease row of the employee, "manager" = HC_Teamlease row of the manager.
TC_COLUMNS = [
    ("Employee ID", "sap", "employee id"),
    ("Employee Name", "sap", "employee name"),
    ("DOJ", "hc", "joining date"),
    ("Emp Status", "hc", "employment status"),
    ("LWD", "hc", "relieving date"),
    ("Department", "hc", "department"),
    ("Parent Org", "hc", "parent org unit"),
    ("Emp Email ID", "hc", "email id"),
    ("Manager ID", "hc", "rep. mgr emp no."),
    ("Manager Name", "hc", "rep. manager name"),
    ("Manager Email", "manager", "email id"),
    ("Manager Status", "manager", "employment status"),
]
DATE_COLUMNS = {"DOJ", "LWD"}  # shown with a date format
FIRST_DATE_COLUMN = len(TC_COLUMNS) + 1  # M

# Total columns after the dates: (header, status counted). None = SUM of the counts.
TOTAL_COLUMNS = [
    ("Present", "P"), ("Absent", "A"), ("Weekly Off", "OFF"), ("Comp Off", "CO"),
    ("Maternity Leave", "ML"), ("Maternity Leave", "PL"), ("PH", "PH"), ("Total", None),
]
REMARKS_HEADER = "Remarks"  # an empty column after the totals, for HR

SAP_REQUIRED = {
    "employee id": "employee_id",
    "employee name": "employee_name",
    "date": "date",
    "work schedule": "work_schedule",
    "worked hours": "worked_hours",
}

# SAP columns the macro fills with formulas (empty in the file HR uploads).
SAP_OUTPUT_COLUMNS = {"remarks": "remarks", "remarks code": "remarks_code"}

HC_KEY = "employee id"
HC_REQUIRED = sorted({source for _, kind, source in TC_COLUMNS if kind != "sap"} | {HC_KEY})

_TEXT_DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d")


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


@dataclass
class SheetLayout:
    header_row: int
    date_columns: dict[str, str]  # ISO date -> column letter
    employee_rows: dict[str, int]  # employee id -> row number
    total_columns: list[TotalColumn]
    # SAP column letters by field: employee_id, date, work_schedule, worked_hours, remarks, remarks_code
    sap_columns: dict[str, str] = field(default_factory=dict)
    sap_last_row: int = 0

    @property
    def counted_statuses(self) -> set[str]:
        return {col.code for col in self.total_columns if col.code}


@dataclass
class EmployeeRow:
    """One TO CONSIDER row, as the app will write it."""

    employee_id: str
    employee_name: str
    row_number: int
    in_hc: bool
    # The values of columns A-L exactly as they go into the sheet (NOT_FOUND where VLOOKUP fails).
    sheet_cells: list[object]
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


@dataclass
class SapDay:
    schedule: str
    worked_seconds: int


@dataclass
class WorkbookData:
    dates: list[date]
    layout: SheetLayout
    employees: list[EmployeeRow]
    sap_days: dict[tuple[str, str], SapDay]  # (employee id, ISO date) -> day
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


def sheet_value(value: object) -> object:
    """A cell value as it will be written into TO CONSIDER (and stored in MongoDB).

    Numbers stay numbers, dates become datetimes (BSON has no date type), text is trimmed
    and empty cells become None.
    """
    if value is None or isinstance(value, (bool, int, float, datetime)):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    text = str(value).strip()
    if text.isdigit():
        return int(text)  # IDs exported as text, like the macro's Text to Columns
    return text or None


# ---------- workbook loading ----------

def _open(source: Path | str | bytes | BinaryIO):
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    elif hasattr(source, "seek"):
        source.seek(0)
    try:
        return load_workbook(source, read_only=True, data_only=True, keep_links=False)
    except Exception as exc:  # openpyxl raises several types for bad files
        raise WorkbookError("This file could not be opened as an Excel workbook (.xlsm or .xlsx).") from exc


def _find_sheet(wb, wanted: str):
    for name in wb.sheetnames:
        if name.strip().upper() == wanted.upper():
            return wb[name]
    return None


def read_workbook(source: Path | str | bytes | BinaryIO) -> WorkbookData:
    """Read SAP and HC_Teamlease and work out TO CONSIDER. Raises WorkbookError for fatal problems."""
    wb = _open(source)
    try:
        if _find_sheet(wb, TO_CONSIDER_SHEET) is not None:
            raise WorkbookError(
                "This workbook already has a 'TO CONSIDER' sheet. Please upload the file from SAP before the "
                "macro has run, with only the 'SAP' and 'HC_Teamlease' sheets."
            )
        sap_sheet = _find_sheet(wb, SAP_SHEET)
        hc_sheet = _find_sheet(wb, HC_SHEET)
        missing = [name for name, sheet in (("SAP", sap_sheet), ("HC_Teamlease", hc_sheet)) if sheet is None]
        if missing:
            raise WorkbookError(
                f"The workbook has no {' or '.join(repr(name) for name in missing)} sheet. Please check that you "
                "uploaded the attendance workbook downloaded from SAP."
            )
        sap_rows = list(sap_sheet.iter_rows(min_row=HEADER_ROW, values_only=True))
        hc_people = _read_hc(hc_sheet)
    finally:
        wb.close()

    issues: list[Issue] = []
    sap_days, sap_names, sap_columns, sap_last_row = _read_sap(sap_rows, issues)
    if not sap_names:
        raise WorkbookError("The 'SAP' sheet has no employee rows.")

    dates = sorted({date.fromisoformat(iso) for _, iso in sap_days})
    employees = _build_employees(sap_names, hc_people, issues)
    after_dates = FIRST_DATE_COLUMN + len(dates)
    layout = SheetLayout(
        header_row=HEADER_ROW,
        date_columns={d.isoformat(): get_column_letter(FIRST_DATE_COLUMN + i) for i, d in enumerate(dates)},
        employee_rows={e.employee_id: e.row_number for e in employees},
        total_columns=[TotalColumn(get_column_letter(after_dates + i), header, code)
                       for i, (header, code) in enumerate(TOTAL_COLUMNS)],
        sap_columns=sap_columns,
        sap_last_row=sap_last_row,
    )
    return WorkbookData(dates=dates, layout=layout, employees=employees, sap_days=sap_days, issues=issues)


# ---------- HC_Teamlease ----------

def _read_hc(sheet) -> dict[str, dict[str, object]]:
    """Employee ID -> {HC header: value} for the columns TO CONSIDER needs. The first row wins, like VLOOKUP."""
    header = next(sheet.iter_rows(min_row=HEADER_ROW, max_row=HEADER_ROW, values_only=True), ())
    columns: dict[str, int] = {}
    for index, cell in enumerate(header):
        key = normalize_header(cell)
        if key in HC_REQUIRED and key not in columns:
            columns[key] = index
    missing = [name for name in HC_REQUIRED if name not in columns]
    if missing:
        pretty = ", ".join(f"'{name.title()}'" for name in missing)
        raise WorkbookError(f"The 'HC_Teamlease' sheet is missing these column headers in row 1: {pretty}.")

    # Only read as far as the last column needed: the real sheet is about 22,000 rows x 64 columns.
    people: dict[str, dict[str, object]] = {}
    for row in sheet.iter_rows(min_row=HEADER_ROW + 1, max_col=max(columns.values()) + 1, values_only=True):
        emp_id = normalize_id(_cell(row, columns[HC_KEY]))
        if emp_id is not None and emp_id not in people:
            people[emp_id] = {name: _cell(row, index) for name, index in columns.items()}
    return people


def _build_employees(sap_names: dict[str, str], hc: dict[str, dict[str, object]], issues: list[Issue]) -> list[EmployeeRow]:
    employees: list[EmployeeRow] = []
    for row_number, emp_id in enumerate(sorted(sap_names, key=int), start=HEADER_ROW + 1):
        person = hc.get(emp_id)
        manager = hc.get(normalize_id(person["rep. mgr emp no."])) if person else None
        cells: list[object] = []
        for _, kind, source in TC_COLUMNS:
            if kind == "sap":
                value = int(emp_id) if source == HC_KEY else sap_names[emp_id]
            else:
                found = person if kind == "hc" else manager
                value = sheet_value(found[source]) if found else NOT_FOUND
            cells.append(value)

        values = dict(zip((header for header, _, _ in TC_COLUMNS), cells))
        if person is None:
            issues.append(Issue(
                type="missing_in_hc", severity="warning", employee_id=emp_id, employee_name=sap_names[emp_id],
                message="Employee is in SAP but not in HC_Teamlease, so DOJ, department and manager are #N/A.",
            ))
        employees.append(EmployeeRow(
            employee_id=emp_id,
            employee_name=sap_names[emp_id],
            row_number=row_number,
            in_hc=person is not None,
            sheet_cells=cells,
            doj=parse_date(values["DOJ"]),
            emp_status=clean_text(values["Emp Status"]),
            lwd=parse_date(values["LWD"]),
            department=clean_text(values["Department"]),
            parent_org=clean_text(values["Parent Org"]),
            emp_email=normalize_email(values["Emp Email ID"]),
            manager_id=normalize_id(values["Manager ID"]),
            manager_name=clean_text(values["Manager Name"]),
            manager_email=normalize_email(values["Manager Email"]),
            manager_status=clean_text(values["Manager Status"]),
        ))
    return employees


def _cell(row: tuple, index: int | None) -> object:
    if index is None or index >= len(row):
        return None
    return row[index]


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
