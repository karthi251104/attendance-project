"""Generate a FAKE attendance workbook with the same layout as the real SAP export.

No real employee data is used. The workbook has the sheets TO CONSIDER, SAP and a
small HC_Teamlease, the same headers, date columns and COUNTIF total formulas.
The TO CONSIDER date cells are filled using the same rules as the macro, so a
default workbook has 0 mismatches between SAP and TO CONSIDER.

`as_uploaded=True` gives the file the way HR uploads it: all headers are there, but the
TO CONSIDER day cells and total formulas and the SAP Remarks / Remarks Code cells are
empty (the macro fills those).

Keyword options let tests create broken or unusual variants.

Command line (from the repo root):  python -m tests.fixtures.make_sample_workbook out.xlsx
"""

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from app.core.rules import Thresholds, calculate_status

DEFAULT_START = date(2026, 9, 20)
PUBLIC_HOLIDAY = date(2026, 10, 2)

MANAGERS = {
    "M1": {"id": 20000001, "name": "Fake Manager One", "email": "manager.one@example.com", "status": "Active"},
    "M2": {"id": 20000002, "name": "Fake Manager Two", "email": "Manager.Two@Example.com", "status": "Active"},
}

# (employee id, name, manager key, 5DAG or 6DAG, emp status, lwd, emp email)
EMPLOYEES = [
    (10000001, "Fake Employee A", "M1", "6DAG", "Active", 0, "emp.a@example.com"),
    (10000002, "Fake Employee B", "M1", "6DAG", "Active", 0, None),
    (10000003, "Fake Employee C", "M1", "5DAG", "Withdrawn", date(2026, 10, 10), "emp.c@example.com"),
    (10000004, "Fake Employee D", "M2", "6DAG", "Active", 0, "emp.d@example.com"),
    (10000005, "Fake Employee E", "M2", "5DAG", "Active", 0, "emp.e@example.com"),
    (10000006, "Fake Employee F", "M2", "6DAG", "Active", 0, "emp.f@example.com"),
]

# Worked-hours pattern, including the rule boundaries.
WORKED_PATTERN = [
    timedelta(hours=9, minutes=8, seconds=8),
    timedelta(hours=4, minutes=59, seconds=59),
    timedelta(hours=5),
    timedelta(hours=7, minutes=59, seconds=59),
    timedelta(hours=8),
    timedelta(0),
    timedelta(hours=9),
]

SAP_HEADERS = [
    "Employee ID", "Employee Name", "Parent Org Unit", "Designation", "Level", "Date",
    "Work Schedule", "Shift Name", "Planned (In)", "Planned (Out)", "Actual (In)", "Actual (Out)",
    "OD (In)", "OD (Out)", "Leave", "Leave Days", "Planned Working Hours", "Worked Hours",
    "Shortfall Hours", "Extra Hours", "Remarks", "Remarks\nCode",
]

TC_HEADERS = [
    "Employee ID", "Employee Name", "DOJ", None, "Emp Status", "LWD", "Department", "Parent Org",
    "Emp Email ID", "Manager ID", "Manager Name", "Manager Email", "Manager Status",
]

# (header, code counted) for the columns after the dates. None = not a COUNTIF.
TOTAL_COLUMNS = [
    ("Present", "P"), ("Absent", "A"), ("Weekly Off", "OFF"), ("Comp Off", "CO"),
    ("Maternity Leave", "ML"), ("Maternity Leave", "PL"), ("PH", "PH"), ("Total", None), ("Remarks", None),
]


def default_schedule(base_schedule: str, day: date) -> str:
    """Sunday is OFF for everyone, Saturday is OFF for 5DAG, 2 Oct is a public holiday."""
    if day == PUBLIC_HOLIDAY:
        return "PH"
    if day.weekday() == 6 or (base_schedule == "5DAG" and day.weekday() == 5):
        return "OFF"
    return base_schedule


def remarks_formula(row: int, numeric: bool) -> str:
    """The macro's SAP 'Remarks' (1-4) or 'Remarks Code' (P/A/OFF/PH) formula for one row."""
    p, a, off, ph = ("1", "2", "3", "4") if numeric else ('"P"', '"A"', '"OFF"', '"PH"')
    return (
        f'=IF(G{row}="PH",IF(R{row}>TIME(4,59,59),{p},{ph}),'
        f'IF(OR(G{row}="5DAG",G{row}="6DAG"),IF(R{row}>TIME(4,59,59),{p},{a}),'
        f'IF(G{row}="OFF",IF(R{row}>TIME(7,59,59),{p},{off}))))'
    )


def as_cell_value(worked: timedelta, variant: int) -> object:
    """Store worked hours in the different ways real files do."""
    if variant == 0:
        return (datetime.min + worked).time()                # datetime.time
    if variant == 1:
        return worked                                         # timedelta
    if variant == 2:
        return worked.total_seconds() / 86400                 # Excel day fraction
    total = int(worked.total_seconds())
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"  # text


def make_sample_workbook(
    path: Path | str,
    *,
    start: date = DEFAULT_START,
    num_days: int = 30,
    text_date_headers: bool = False,
    formulas_only: bool = False,
    include_sap: bool = True,
    include_to_consider: bool = True,
    old_macro_codes: bool = False,
    text_ids: bool = False,
    as_uploaded: bool = False,
    tc_overrides: dict[tuple[int, date], str | None] | None = None,
    sap_overrides: dict[tuple[int, date], tuple[str, timedelta]] | None = None,
    drop_sap_rows: set[tuple[int, date]] | None = None,
    extra_sap_dates: list[date] | None = None,
    extra_sap_employee: bool = False,
    extra_tc_employee: bool = False,
    employee_overrides: dict[int, dict[str, object]] | None = None,
) -> Path:
    """Write a fake workbook to `path` and return the path."""
    tc_overrides = tc_overrides or {}
    sap_overrides = sap_overrides or {}
    drop_sap_rows = drop_sap_rows or set()
    employee_overrides = employee_overrides or {}
    dates = [start + timedelta(days=i) for i in range(num_days)]
    thresholds = Thresholds()

    employees = [list(e) for e in EMPLOYEES]
    if extra_tc_employee:
        employees.append([10000099, "Fake Only In TC", "M2", "6DAG", "Active", 0, "only.tc@example.com"])

    # 1. Per employee-day: schedule, worked hours, status.
    sap_days: dict[tuple[int, date], tuple[str, timedelta]] = {}
    for index, (emp_id, _, _, base, *_rest) in enumerate(employees):
        if emp_id == 10000099:
            continue  # this employee exists only in TO CONSIDER
        for d_index, day in enumerate(dates):
            sap_days[(emp_id, day)] = (default_schedule(base, day), WORKED_PATTERN[(index + d_index) % len(WORKED_PATTERN)])
    sap_days.update(sap_overrides)

    wb = Workbook()
    wb.remove(wb.active)

    # 2. TO CONSIDER sheet.
    if include_to_consider:
        ws = wb.create_sheet("TO CONSIDER")
        date_headers = [day.strftime("%d-%m-%Y") if text_date_headers else datetime(day.year, day.month, day.day) for day in dates]
        ws.append(TC_HEADERS + date_headers + [header for header, _ in TOTAL_COLUMNS])
        first_date_col = len(TC_HEADERS) + 1
        first_letter = get_column_letter(first_date_col)
        last_letter = get_column_letter(first_date_col + num_days - 1)

        for row_number, (emp_id, name, mgr_key, _, status, lwd, email) in enumerate(employees, start=2):
            mgr = dict(MANAGERS[mgr_key])
            fields = {
                "Employee ID": f"{emp_id}" if text_ids else emp_id,
                "Employee Name": name, "DOJ": datetime(2024, 1, 15), "Display": "#N/A",
                "Emp Status": status, "LWD": lwd, "Department": f"Dept {mgr_key}", "Parent Org": "Fake Org",
                "Emp Email ID": email, "Manager ID": mgr["id"], "Manager Name": mgr["name"],
                "Manager Email": mgr["email"], "Manager Status": mgr["status"],
            }
            fields.update(employee_overrides.get(emp_id, {}))
            row: list[object] = list(fields.values())
            if formulas_only:
                # Columns C-M as formulas with no cached values (as if never opened in Excel).
                row[2:] = [f"=VLOOKUP($A{row_number},HC_Teamlease!$A:$M,{col},0)" for col in range(3, 14)]

            for day in dates:
                if as_uploaded and (emp_id, day) not in tc_overrides:
                    row.append(None)  # the macro has not filled the days yet
                elif (emp_id, day) in tc_overrides:
                    row.append(tc_overrides[(emp_id, day)])
                elif (emp_id, day) in sap_days:
                    schedule, worked = sap_days[(emp_id, day)]
                    status_code = calculate_status(schedule, int(worked.total_seconds()), thresholds)
                    row.append("P" if status_code == "?" else status_code)
                else:
                    row.append("P")

            span = f"{first_letter}{row_number}:{last_letter}{row_number}"
            total_letters = []
            for col_offset, (header, code) in enumerate(TOTAL_COLUMNS):
                letter = get_column_letter(first_date_col + num_days + col_offset)
                if as_uploaded:
                    row.append(None)  # the macro writes the total formulas
                elif code is not None:
                    if old_macro_codes and code == "ML":
                        code = "MTL"
                    row.append(f'=COUNTIF({span},"{code}")')
                    total_letters.append(letter)
                elif header == "Total":
                    row.append(f"=SUM({total_letters[0]}{row_number}:{total_letters[-1]}{row_number})")
                else:
                    row.append(None)
            ws.append(row)

    # 3. SAP sheet (one row per employee per day).
    if include_sap:
        ws = wb.create_sheet("SAP")
        ws.append(SAP_HEADERS)
        sap_employees = [(e[0], e[1]) for e in employees if e[0] != 10000099]
        if extra_sap_employee:
            sap_employees.append((10000088, "Fake Only In SAP"))
            for day in dates:
                sap_days[(10000088, day)] = ("6DAG", timedelta(hours=9))
        for emp_id, name in sap_employees:
            emp_dates = dates + [d for d in (extra_sap_dates or [])]
            for d_index, day in enumerate(emp_dates):
                if (emp_id, day) in drop_sap_rows:
                    continue
                schedule, worked = sap_days.get((emp_id, day), ("6DAG", timedelta(hours=9)))
                excel_row = ws.max_row + 1
                ws.append([
                    emp_id, name, "Fake Org Unit", "Associate", "L1", datetime(day.year, day.month, day.day),
                    schedule, None, time(9), time(18), None, None, None, None, None, None,
                    time(9), as_cell_value(worked, d_index % 4), None, None,
                    # Remarks columns: the macro's formulas (empty in the uploaded file). The app never reads them.
                    None if as_uploaded else remarks_formula(excel_row, numeric=True),
                    None if as_uploaded else remarks_formula(excel_row, numeric=False),
                ])

    # 4. A small HC_Teamlease sheet (the real one is huge and must never be loaded).
    ws = wb.create_sheet("HC_Teamlease")
    ws.append(["Employee ID", "Employee Name", "Some Column"])
    for emp_id, name, *_ in employees:
        ws.append([emp_id, name, "x"])

    path = Path(path)
    wb.save(path)
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "sample_workbook.xlsx")
    print(f"Wrote {make_sample_workbook(out)}")
