"""Turn the data read from the workbook into the attendance grid and the exceptions report.

For every employee and every date we keep:
  calculated_days : the status calculated from SAP with the macro rules (never edited)
  original_days   : the starting value at upload, the same as calculated_days (never edited)
  days            : the current value; equal to original_days until someone edits it

On download, `days` is written into the new TO CONSIDER sheet.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from app.config import Settings, get_settings
from app.core.rules import UNKNOWN_STATUS, Thresholds, calculate_status
from app.core.workbook_reader import Issue, SheetLayout, WorkbookData

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class GridEmployee:
    """One employee in one cycle. Field names match the `employees` Mongo collection."""

    employee_id: str
    employee_name: str
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
    sheet_cells: list[object] = field(default_factory=list)  # TO CONSIDER columns A-L, as written
    days: dict[str, str | None] = field(default_factory=dict)
    original_days: dict[str, str | None] = field(default_factory=dict)
    calculated_days: dict[str, str | None] = field(default_factory=dict)
    worked_seconds: dict[str, int] = field(default_factory=dict)
    schedule: dict[str, str] = field(default_factory=dict)


@dataclass
class CycleData:
    dates: list[date]
    layout: SheetLayout
    employees: list[GridEmployee]
    exceptions: list[Issue]
    filled_days: int  # employee-days with a status calculated from SAP

    @property
    def manager_emails(self) -> set[str]:
        return {e.manager_email for e in self.employees if e.manager_email}


def is_valid_email(value: str | None) -> bool:
    return bool(value) and bool(_EMAIL.match(value))


def build_cycle_data(data: WorkbookData, settings: Settings | None = None) -> CycleData:
    settings = settings or get_settings()
    thresholds = Thresholds.from_settings(settings)
    isos = [d.isoformat() for d in data.dates]
    exceptions: list[Issue] = list(data.issues)
    filled = 0

    employees: list[GridEmployee] = []
    for row in data.employees:
        emp = GridEmployee(
            employee_id=row.employee_id, employee_name=row.employee_name, doj=row.doj, emp_status=row.emp_status,
            lwd=row.lwd, department=row.department, parent_org=row.parent_org, emp_email=row.emp_email,
            manager_id=row.manager_id, manager_name=row.manager_name, manager_email=row.manager_email,
            manager_status=row.manager_status, sheet_cells=list(row.sheet_cells),
        )
        for iso in isos:
            sap_day = data.sap_days.get((row.employee_id, iso))
            sap_value = calculate_status(sap_day.schedule, sap_day.worked_seconds, thresholds) if sap_day else None
            if sap_day:
                emp.worked_seconds[iso] = sap_day.worked_seconds
                emp.schedule[iso] = sap_day.schedule
            emp.calculated_days[iso] = sap_value
            emp.original_days[iso] = sap_value
            if sap_value and sap_value != UNKNOWN_STATUS:
                filled += 1
            _check_day(emp, iso, sap_day, sap_value, exceptions)
        emp.days = dict(emp.original_days)
        _check_employee(emp, row.in_hc, exceptions)
        employees.append(emp)

    exceptions.extend(_check_managers(employees))
    exceptions.extend(_check_totals(data.layout, settings))
    exceptions.extend(_check_sap_output_columns(data))
    return CycleData(data.dates, data.layout, employees, exceptions, filled)


def _issue(type_: str, severity: str, emp: GridEmployee, iso: str | None, message: str) -> Issue:
    return Issue(type=type_, severity=severity, message=message,
                 employee_id=emp.employee_id, employee_name=emp.employee_name, date=iso)


def _check_day(emp, iso, sap_day, sap_value, exceptions) -> None:
    if sap_day is None:
        exceptions.append(_issue("missing_employee_date", "warning", emp, iso, "SAP has no row for this employee on this date."))
    elif sap_value == UNKNOWN_STATUS:
        exceptions.append(_issue(
            "unknown_schedule", "warning", emp, iso,
            f"Unknown Work Schedule '{sap_day.schedule or '(empty)'}' in SAP; status could not be calculated.",
        ))


def _check_employee(emp: GridEmployee, in_hc: bool, exceptions: list[Issue]) -> None:
    if not is_valid_email(emp.manager_email):
        exceptions.append(_issue(
            "invalid_manager_email", "error", emp, None,
            "Manager Email is missing or invalid, so this employee's manager cannot be emailed.",
        ))
    if in_hc and not emp.emp_email:
        exceptions.append(_issue("empty_emp_email", "info", emp, None, "Emp Email ID is empty."))


def _check_managers(employees: list[GridEmployee]) -> list[Issue]:
    """One issue per withdrawn manager (not one per employee)."""
    issues: list[Issue] = []
    reported: set[str] = set()
    for emp in employees:
        key = emp.manager_email or emp.manager_id or emp.manager_name or ""
        if (emp.manager_status or "").lower() == "withdrawn" and key not in reported:
            reported.add(key)
            team = sum(1 for e in employees if (e.manager_email or e.manager_id or e.manager_name or "") == key)
            issues.append(Issue(
                type="manager_withdrawn", severity="warning", employee_id=emp.manager_id, employee_name=emp.manager_name,
                message=f"Manager Status is Withdrawn ({team} team member(s)).",
            ))
    return issues


def _check_totals(layout: SheetLayout, settings: Settings) -> list[Issue]:
    counted = layout.counted_statuses
    return [
        Issue(type="status_not_in_totals", severity="warning",
              message=f"No total column in TO CONSIDER counts '{code}', so edits to {code} will not appear in the totals.")
        for code in settings.allowed_status_list if code not in counted
    ]


def _check_sap_output_columns(data: WorkbookData) -> list[Issue]:
    """The app fills SAP 'Remarks' and 'Remarks Code' like the macro; warn if a column is missing."""
    return [
        Issue(type="sap_column_missing", severity="warning",
              message=f"The SAP sheet has no '{header}' column, so it will not be filled in the downloaded file.")
        for name, header in (("remarks", "Remarks"), ("remarks_code", "Remarks Code"))
        if name not in data.layout.sap_columns
    ]
