from datetime import date, datetime

import pytest

from app.core.workbook_reader import (
    MISSING_CACHE_MESSAGE,
    WorkbookError,
    clean_text,
    normalize_email,
    normalize_header,
    normalize_id,
    parse_date,
    read_workbook,
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


# ---------- reading the workbook ----------

def test_reads_layout_and_employees(make_workbook) -> None:
    data = read_workbook(make_workbook())

    assert len(data.dates) == 30
    assert data.dates[0] == date(2026, 9, 20) and data.dates[-1] == date(2026, 10, 19)
    assert data.layout.date_columns["2026-09-20"] == "N"
    assert data.layout.date_columns["2026-10-19"] == "AQ"
    assert data.layout.employee_rows["10000001"] == 2
    assert len(data.employees) == 6
    assert data.sap_present
    assert len(data.sap_days) == 180

    emp = data.employees[1]
    assert emp.employee_id == "10000002"
    assert emp.emp_email is None
    assert emp.manager_email == "manager.one@example.com"
    withdrawn = data.employees[2]
    assert withdrawn.emp_status == "Withdrawn" and withdrawn.lwd == date(2026, 10, 10)
    assert data.employees[3].manager_email == "manager.two@example.com"  # lower-cased


def test_total_columns_read_from_formulas(make_workbook) -> None:
    layout = read_workbook(make_workbook()).layout
    codes = {col.letter: col.code for col in layout.total_columns}
    assert codes == {"AR": "P", "AS": "A", "AT": "OFF", "AU": "CO", "AV": "ML", "AW": "PL", "AX": "PH", "AY": None}
    assert layout.counted_statuses == {"P", "A", "OFF", "CO", "ML", "PL", "PH"}


def test_old_macro_mtl_code_is_detected(make_workbook) -> None:
    layout = read_workbook(make_workbook(old_macro_codes=True)).layout
    assert "MTL" in layout.counted_statuses and "ML" not in layout.counted_statuses


@pytest.mark.parametrize("num_days, last_letter", [(28, "AO"), (31, "AR")])
def test_cycle_length_is_dynamic(make_workbook, num_days, last_letter) -> None:
    data = read_workbook(make_workbook(num_days=num_days))
    assert len(data.dates) == num_days
    assert list(data.layout.date_columns.values())[-1] == last_letter


def test_text_date_headers(make_workbook) -> None:
    data = read_workbook(make_workbook(text_date_headers=True))
    assert data.dates[0] == date(2026, 9, 20) and len(data.dates) == 30


def test_text_ids_match_numeric_sap_ids(make_workbook) -> None:
    data = read_workbook(make_workbook(text_ids=True))
    assert {e.employee_id for e in data.employees} == set(data.sap_names)


def test_worked_hours_types_are_all_read(make_workbook) -> None:
    # The fixture stores worked hours as time, timedelta, day fraction and text in turn.
    data = read_workbook(make_workbook())
    first_four = [data.sap_days[("10000001", iso)].worked_seconds
                  for iso in ("2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23")]
    assert first_four == [32888, 17999, 18000, 28799]
    assert not data.issues


def test_missing_to_consider_is_fatal(make_workbook) -> None:
    with pytest.raises(WorkbookError, match="TO CONSIDER"):
        read_workbook(make_workbook(include_to_consider=False))


def test_formulas_without_cached_values_are_fatal(make_workbook) -> None:
    with pytest.raises(WorkbookError) as error:
        read_workbook(make_workbook(formulas_only=True))
    assert str(error.value) == MISSING_CACHE_MESSAGE


def test_missing_sap_sheet_is_not_fatal(make_workbook) -> None:
    data = read_workbook(make_workbook(include_sap=False))
    assert not data.sap_present and data.sap_days == {}


def test_not_an_excel_file(tmp_path) -> None:
    bad = tmp_path / "bad.xlsx"
    bad.write_text("hello")
    with pytest.raises(WorkbookError, match="could not be opened"):
        read_workbook(bad)


def test_accepts_bytes(make_workbook) -> None:
    data = read_workbook(make_workbook().read_bytes())
    assert len(data.employees) == 6


def test_layout_of_uploaded_file_with_empty_formulas(make_workbook) -> None:
    # As HR uploads it: headers only, no total formulas yet, so the codes come from the headers.
    layout = read_workbook(make_workbook(as_uploaded=True)).layout
    codes = {col.letter: col.code for col in layout.total_columns}
    assert codes == {"AR": "P", "AS": "A", "AT": "OFF", "AU": "CO", "AV": "ML", "AW": "PL", "AX": "PH", "AY": None}
    assert not any(col.has_formula for col in layout.total_columns)


def test_total_columns_with_formulas_are_marked(make_workbook) -> None:
    layout = read_workbook(make_workbook()).layout
    assert all(col.has_formula for col in layout.total_columns)


def test_sap_columns_are_recorded(make_workbook) -> None:
    layout = read_workbook(make_workbook(as_uploaded=True)).layout
    assert layout.sap_columns == {
        "employee_id": "A", "employee_name": "B", "date": "F", "work_schedule": "G",
        "worked_hours": "R", "remarks": "U", "remarks_code": "V",
    }
    assert layout.sap_last_row == 181
