"""Turn the data read from the workbook into the attendance grid and the exceptions report.

For every TO CONSIDER employee and every date we keep three values:
  sheet_days      : what the TO CONSIDER cell held at upload (None = empty cell; never edited)
  calculated_days : the status recalculated from SAP with the macro rules (never edited)
  original_days   : the starting value at upload, chosen by GRID_SOURCE (never edited)
  days            : the current value; equal to original_days until someone edits it

HR usually uploads the workbook with the TO CONSIDER date cells still empty, so the
grid is filled from SAP. When downloading, every cell where `days` differs from
`sheet_days` is written: the cells filled from SAP and the cells edited in the portal.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from app.config import Settings, get_settings
from app.core.rules import UNKNOWN_STATUS, Thresholds, calculate_status, format_seconds
from app.core.workbook_reader import Issue, SheetLayout, WorkbookData

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class GridEmployee:
    """One employee in one cycle. Field names match the `employees` Mongo collection."""

    employee_id: str
    employee_name: str
    emp_status: str | None
    lwd: date | None
    department: str | None
    parent_org: str | None
    emp_email: str | None
    manager_id: str | None
    manager_name: str | None
    manager_email: str | None
    manager_status: str | None
    days: dict[str, str | None] = field(default_factory=dict)
    original_days: dict[str, str | None] = field(default_factory=dict)
    sheet_days: dict[str, str | None] = field(default_factory=dict)
    calculated_days: dict[str, str | None] = field(default_factory=dict)
    worked_seconds: dict[str, int] = field(default_factory=dict)
    schedule: dict[str, str] = field(default_factory=dict)


@dataclass
class Mismatch:
    employee_id: str
    date: str
    to_consider: str
    calculated: str


@dataclass
class CycleData:
    dates: list[date]
    layout: SheetLayout
    employees: list[GridEmployee]
    exceptions: list[Issue]
    mismatches: list[Mismatch]
    compared_days: int  # employee-days where both SAP and TO CONSIDER had a value
    filled_days: int  # empty TO CONSIDER cells that were filled from SAP

    @property
    def manager_emails(self) -> set[str]:
        return {e.manager_email for e in self.employees if e.manager_email}


def is_valid_email(value: str | None) -> bool:
    return bool(value) and bool(_EMAIL.match(value))


def pick_starting_value(tc_value: str | None, sap_value: str | None, grid_source: str) -> str | None:
    """Choose the starting status for one cell, following GRID_SOURCE.

    to_consider: keep what is in the sheet; use SAP only for empty cells.
    sap:         use the SAP calculation; fall back to the sheet if SAP has no usable value.
    """
    usable_sap = sap_value if sap_value and sap_value != UNKNOWN_STATUS else None
    if grid_source == "sap":
        return usable_sap or tc_value or sap_value
    return tc_value or sap_value


def build_cycle_data(data: WorkbookData, settings: Settings | None = None) -> CycleData:
    settings = settings or get_settings()
    thresholds = Thresholds.from_settings(settings)
    isos = [d.isoformat() for d in data.dates]
    exceptions: list[Issue] = list(data.issues)
    mismatches: list[Mismatch] = []
    compared = 0
    filled = 0

    if not data.sap_present:
        exceptions.append(Issue(
            type="sap_sheet_missing", severity="warning",
            message="The workbook has no 'SAP' sheet. The grid comes from TO CONSIDER and could not be verified.",
        ))

    employees: list[GridEmployee] = []
    for row in data.employees:
        emp = GridEmployee(
            employee_id=row.employee_id, employee_name=row.employee_name, emp_status=row.emp_status,
            lwd=row.lwd, department=row.department, parent_org=row.parent_org, emp_email=row.emp_email,
            manager_id=row.manager_id, manager_name=row.manager_name, manager_email=row.manager_email,
            manager_status=row.manager_status,
        )
        in_sap = row.employee_id in data.sap_names
        for iso in isos:
            tc_value = row.days.get(iso)
            sap_day = data.sap_days.get((row.employee_id, iso))
            sap_value = calculate_status(sap_day.schedule, sap_day.worked_seconds, thresholds) if sap_day else None
            if sap_day:
                emp.worked_seconds[iso] = sap_day.worked_seconds
                emp.schedule[iso] = sap_day.schedule
            emp.sheet_days[iso] = tc_value
            emp.calculated_days[iso] = sap_value
            emp.original_days[iso] = pick_starting_value(tc_value, sap_value, settings.grid_source)

            if not tc_value and emp.original_days[iso]:
                filled += 1
            _check_day(emp, iso, tc_value, sap_day, sap_value, in_sap, settings, exceptions)
            if tc_value and sap_value and sap_value != UNKNOWN_STATUS:
                compared += 1
                if tc_value != sap_value:
                    mismatches.append(Mismatch(row.employee_id, iso, tc_value, sap_value))
                    exceptions.append(_issue(
                        "status_mismatch", "info", emp, iso,
                        f"TO CONSIDER has {tc_value} but SAP calculates {sap_value} "
                        f"({sap_day.schedule} · {format_seconds(sap_day.worked_seconds)}).",
                    ))
        emp.days = dict(emp.original_days)
        _check_employee(emp, exceptions)
        employees.append(emp)

    exceptions.extend(_check_employee_sets(data))
    exceptions.extend(_check_sap_dates(data, set(isos)))
    exceptions.extend(_check_managers(employees))
    exceptions.extend(_check_totals(data.layout, settings))
    exceptions.extend(_check_sap_output_columns(data))
    return CycleData(data.dates, data.layout, employees, exceptions, mismatches, compared, filled)


def _issue(type_: str, severity: str, emp: GridEmployee, iso: str | None, message: str) -> Issue:
    return Issue(type=type_, severity=severity, message=message,
                 employee_id=emp.employee_id, employee_name=emp.employee_name, date=iso)


def _check_day(emp, iso, tc_value, sap_day, sap_value, in_sap, settings, exceptions) -> None:
    if sap_value == UNKNOWN_STATUS:
        exceptions.append(_issue(
            "unknown_schedule", "warning", emp, iso,
            f"Unknown Work Schedule '{sap_day.schedule or '(empty)'}' in SAP; status could not be calculated.",
        ))
    if in_sap and sap_day is None:
        exceptions.append(_issue("missing_employee_date", "warning", emp, iso, "SAP has no row for this employee on this date."))
    if tc_value and tc_value not in settings.allowed_status_list:
        exceptions.append(_issue(
            "invalid_status", "warning", emp, iso,
            f"TO CONSIDER has '{tc_value}', which is not an allowed status ({', '.join(settings.allowed_status_list)}).",
        ))
    if not tc_value and sap_value in (None, UNKNOWN_STATUS):
        exceptions.append(_issue("empty_day", "warning", emp, iso, "No status in TO CONSIDER and none could be calculated from SAP."))


def _check_employee(emp: GridEmployee, exceptions: list[Issue]) -> None:
    if not is_valid_email(emp.manager_email):
        exceptions.append(_issue(
            "invalid_manager_email", "error", emp, None,
            "Manager Email is missing or invalid, so this employee's manager cannot be emailed.",
        ))
    if not emp.emp_email:
        exceptions.append(_issue("empty_emp_email", "info", emp, None, "Emp Email ID is empty."))


def _check_employee_sets(data: WorkbookData) -> list[Issue]:
    if not data.sap_present:
        return []
    tc_ids = {e.employee_id for e in data.employees}
    issues = [
        Issue(type="missing_in_to_consider", severity="warning", employee_id=emp_id, employee_name=name,
              message="Employee is in SAP but not in TO CONSIDER.")
        for emp_id, name in sorted(data.sap_names.items()) if emp_id not in tc_ids
    ]
    issues += [
        Issue(type="missing_in_sap", severity="warning", employee_id=e.employee_id, employee_name=e.employee_name,
              message="Employee is in TO CONSIDER but not in SAP.")
        for e in data.employees if e.employee_id not in data.sap_names
    ]
    return issues


def _check_sap_dates(data: WorkbookData, cycle_isos: set[str]) -> list[Issue]:
    """One issue per SAP date that is not a TO CONSIDER date column."""
    rows_per_date: dict[str, int] = defaultdict(int)
    for _, iso in data.sap_days:
        if iso not in cycle_isos:
            rows_per_date[iso] += 1
    return [
        Issue(type="sap_date_outside_cycle", severity="warning", date=iso,
              message=f"SAP has {count} row(s) dated {date.fromisoformat(iso):%d/%m/%Y}, which is not a TO CONSIDER date column.")
        for iso, count in sorted(rows_per_date.items())
    ]


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
    if not data.sap_present:
        return []
    return [
        Issue(type="sap_column_missing", severity="warning",
              message=f"The SAP sheet has no '{header}' column, so it will not be filled in the downloaded file.")
        for name, header in (("remarks", "Remarks"), ("remarks_code", "Remarks Code"))
        if name not in data.layout.sap_columns
    ]
