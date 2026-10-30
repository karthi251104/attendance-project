from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from app.core.workbook_reader import (
    NOT_FOUND,
    WorkbookError,
    clean_text,
    normalize_email,
    normalize_header,
    normalize_id,
    parse_date,
    read_workbook,
    sheet_value,
)


# ---------- value helpers ----------

@pytest.mark.parametrize(
    "value, expected",
    [(10027539, "10027539"), (10027539.0, "10027539"), ("10027539", "10027539"), ("10027539.0", "10027539"),
     (" 10027539 ", "10027539"), (None, None), ("", None), ("#N/A", None), (0, None), ("E123", None), (1.5, None)],
)
def test_normalize_id(value, expected) -> None:
    assert normalize_id(value) == expected


def test_normalize_email_and_text() -> None:
    assert normalize_email(" Manager.Two@Example.com ") == "manager.two@example.com"
    assert normalize_email(0) is None
    assert normalize_email("#REF!") is None
    assert clean_text("#VALUE!") is None
    assert clean_text(12.0) == "12"


def test_normalize_header_handles_newlines() -> None:
    assert normalize_header("Remarks\nCode") == "remarks code"
    assert normalize_header("  Employee   ID ") == "employee id"


@pytest.mark.parametrize(
    "value, expected",
    [(datetime(2026, 9, 20), date(2026, 9, 20)), (date(2026, 9, 20), date(2026, 9, 20)),
     ("20-09-2026", date(2026, 9, 20)), ("20/09/2026", date(2026, 9, 20)), ("01/10/2026", date(2026, 10, 1)),
     ("Present", None), (None, None), (45000, None)],
)
def test_parse_date(value, expected) -> None:
    assert parse_date(value) == expected


def test_sheet_value() -> None:
    assert sheet_value(" Active ") == "Active"
    assert sheet_value("20000001") == 20000001  # text IDs become numbers, like the macro's Text to Columns
    assert sheet_value(date(2024, 1, 15)) == datetime(2024, 1, 15)
    assert sheet_value("") is None and sheet_value(None) is None


# ---------- building TO CONSIDER from SAP + HC_Teamlease ----------

def test_reads_layout_and_employees(make_workbook) -> None:
    data = read_workbook(make_workbook())

    assert len(data.dates) == 30
    assert data.dates[0] == date(2026, 9, 20) and data.dates[-1] == date(2026, 10, 19)
    assert data.layout.date_columns["2026-09-20"] == "M"  # no column D, so the dates start one column earlier
    assert data.layout.date_columns["2026-10-19"] == "AP"
    assert data.layout.employee_rows == {f"1000000{i}": i + 1 for i in range(1, 7)}
    assert len(data.sap_days) == 180

    emp = data.employees[1]
    assert emp.employee_id == "10000002" and emp.employee_name == "Fake Employee B"
    assert emp.emp_email is None
    assert emp.manager_id == "20000001" and emp.manager_name == "Fake Manager One"
    assert emp.manager_email == "manager.one@example.com" and emp.manager_status == "Active"
    withdrawn = data.employees[2]
    assert withdrawn.emp_status == "Withdrawn" and withdrawn.lwd == date(2026, 10, 10)
    assert withdrawn.doj == date(2024, 1, 15) and withdrawn.department == "Dept M1"
    assert data.employees[3].manager_email == "manager.two@example.com"  # lower-cased
    assert not data.issues


def test_sheet_cells_are_the_macro_columns(make_workbook) -> None:
    emp = read_workbook(make_workbook()).employees[3]
    assert emp.sheet_cells == [
        10000004, "Fake Employee D", datetime(2024, 1, 15), "Active", None, "Dept M2", "Fake Org",
        "emp.d@example.com", 20000002, "Fake Manager Two", "Manager.Two@Example.com", "Active",
    ]


def test_employees_are_sorted_by_id_like_the_pivot(make_workbook) -> None:
    data = read_workbook(make_workbook(employee_not_in_hc=True))
    ids = [e.employee_id for e in data.employees]
    assert ids == sorted(ids, key=int) and ids[-1] == "10000088"


def test_employee_not_in_hc_gets_na(make_workbook) -> None:
    data = read_workbook(make_workbook(employee_not_in_hc=True))
    emp = data.employees[-1]
    assert emp.sheet_cells[:2] == [10000088, "Fake Only In SAP"]
    assert emp.sheet_cells[2:] == [NOT_FOUND] * 10
    assert emp.manager_email is None and emp.department is None
    assert [(i.type, i.employee_id) for i in data.issues] == [("missing_in_hc", "10000088")]


def test_manager_not_in_hc_gets_na(make_workbook) -> None:
    emp = read_workbook(make_workbook(hc_overrides={10000001: {"Rep. Mgr Emp No.": 29999999}})).employees[0]
    assert emp.sheet_cells[8:] == [29999999, "Fake Manager One", NOT_FOUND, NOT_FOUND]
    assert emp.manager_email is None


@pytest.mark.parametrize("num_days, last_letter", [(28, "AN"), (31, "AQ")])
def test_cycle_length_is_dynamic(make_workbook, num_days, last_letter) -> None:
    data = read_workbook(make_workbook(num_days=num_days))
    assert len(data.dates) == num_days
    assert list(data.layout.date_columns.values())[-1] == last_letter


def test_total_columns_follow_the_dates(make_workbook) -> None:
    layout = read_workbook(make_workbook()).layout
    codes = {col.letter: col.code for col in layout.total_columns}
    assert codes == {"AQ": "P", "AR": "A", "AS": "OFF", "AT": "CO", "AU": "ML", "AV": "PL", "AW": "PH", "AX": None}
    assert layout.counted_statuses == {"P", "A", "OFF", "CO", "ML", "PL", "PH"}


def test_text_ids_match(make_workbook) -> None:
    data = read_workbook(make_workbook(text_ids=True))
    assert [e.manager_email for e in data.employees[:2]] == ["manager.one@example.com"] * 2
    assert data.employees[0].sheet_cells[0] == 10000001 and data.employees[0].sheet_cells[8] == 20000001
    assert not data.issues


def test_worked_hours_types_are_all_read(make_workbook) -> None:
    # The fixture stores worked hours as time, timedelta, day fraction and text in turn.
    data = read_workbook(make_workbook())
    first_four = [data.sap_days[("10000001", iso)].worked_seconds
                  for iso in ("2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23")]
    assert first_four == [32888, 17999, 18000, 28799]


def test_only_dates_in_sap_become_columns(make_workbook) -> None:
    # Like the pivot table: a date with no SAP row at all gets no column.
    day = date(2026, 9, 25)
    data = read_workbook(make_workbook(drop_sap_rows={(e, day) for e in range(10000001, 10000007)}))
    assert len(data.dates) == 29 and day not in data.dates


@pytest.mark.parametrize("options, message", [
    ({"with_to_consider": True}, "already has a 'TO CONSIDER' sheet"),
    ({"include_sap": False}, "no 'SAP' sheet"),
    ({"include_hc": False}, "no 'HC_Teamlease' sheet"),
    ({"hc_missing_header": "Rep. Mgr Emp No."}, "'Rep. Mgr Emp No.'"),
])
def test_bad_workbooks_are_fatal(make_workbook, options, message) -> None:
    with pytest.raises(WorkbookError, match=message):
        read_workbook(make_workbook(**options))


def test_not_an_excel_file(tmp_path) -> None:
    bad = tmp_path / "bad.xlsx"
    bad.write_text("hello")
    with pytest.raises(WorkbookError, match="could not be opened"):
        read_workbook(bad)


def test_accepts_bytes(make_workbook) -> None:
    data = read_workbook(make_workbook().read_bytes())
    assert len(data.employees) == 6


def test_sap_columns_are_recorded(make_workbook) -> None:
    layout = read_workbook(make_workbook()).layout
    assert layout.sap_columns == {
        "employee_id": "A", "employee_name": "B", "date": "F", "work_schedule": "G",
        "worked_hours": "R", "remarks": "U", "remarks_code": "V",
    }
    assert layout.sap_last_row == 181


def test_first_hc_row_wins_like_vlookup(make_workbook) -> None:
    path = make_workbook()
    wb = load_workbook(path)
    wb["HC_Teamlease"].append([10000001] + [None] * 21 + ["Second Dept"])
    wb.save(path)
    assert read_workbook(path).employees[0].department == "Dept M1"
