from datetime import date, timedelta

import pytest

from app.config import Settings
from app.core.grid import build_cycle_data, pick_starting_value
from app.core.workbook_reader import read_workbook

D1 = date(2026, 9, 21)  # a Monday: a working day for everyone
ISO1 = D1.isoformat()


def build(path, **settings):
    return build_cycle_data(read_workbook(path), Settings(_env_file=None, **settings))


def types(cycle) -> list[str]:
    return [issue.type for issue in cycle.exceptions]


def test_default_workbook_has_zero_mismatches(make_workbook) -> None:
    cycle = build(make_workbook())
    assert cycle.compared_days == 180
    assert cycle.mismatches == []
    # The only exception in the clean fixture is the employee with no email (information only).
    assert types(cycle) == ["empty_emp_email"]


def test_grid_values_and_details(make_workbook) -> None:
    emp = build(make_workbook()).employees[0]
    assert emp.days == emp.original_days == emp.calculated_days
    assert emp.schedule["2026-09-20"] == "OFF"  # Sunday
    assert emp.days["2026-09-20"] == "P"  # 9:08:08 on an OFF day is >= 8h
    assert emp.days[ISO1] == "A"  # 4:59:59 on a 6DAG day
    assert emp.days["2026-09-22"] == "P"  # exactly 5:00:00
    assert emp.worked_seconds[ISO1] == 17999


def test_mismatch_is_reported_and_grid_source_decides(make_workbook) -> None:
    path = make_workbook(tc_overrides={(10000001, D1): "CO"})  # SAP calculates A

    from_sheet = build(path, grid_source="to_consider")
    assert len(from_sheet.mismatches) == 1
    assert from_sheet.mismatches[0].to_consider == "CO" and from_sheet.mismatches[0].calculated == "A"
    assert "status_mismatch" in types(from_sheet)
    assert from_sheet.employees[0].days[ISO1] == "CO"

    from_sap = build(path, grid_source="sap")
    assert from_sap.employees[0].days[ISO1] == "A"


@pytest.mark.parametrize("grid_source", ["to_consider", "sap"])
def test_empty_sheet_cell_is_filled_from_sap(make_workbook, grid_source) -> None:
    cycle = build(make_workbook(tc_overrides={(10000001, D1): None}), grid_source=grid_source)
    assert cycle.employees[0].days[ISO1] == "A"
    assert cycle.mismatches == []


def test_pick_starting_value() -> None:
    assert pick_starting_value("CO", "A", "to_consider") == "CO"
    assert pick_starting_value(None, "A", "to_consider") == "A"
    assert pick_starting_value("CO", "A", "sap") == "A"
    assert pick_starting_value("P", "?", "sap") == "P"  # never prefer "?" over a real value
    assert pick_starting_value(None, None, "sap") is None


def test_unknown_schedule(make_workbook) -> None:
    cycle = build(make_workbook(sap_overrides={(10000001, D1): ("NIGHT", timedelta(hours=9))}))
    emp = cycle.employees[0]
    assert emp.calculated_days[ISO1] == "?"
    assert emp.days[ISO1] == "P"  # the sheet value is kept, "?" is never the starting value here
    assert "unknown_schedule" in types(cycle)
    assert cycle.mismatches == []


def test_employee_in_only_one_sheet(make_workbook) -> None:
    cycle = build(make_workbook(extra_sap_employee=True, extra_tc_employee=True))
    found = {(i.type, i.employee_id) for i in cycle.exceptions}
    assert ("missing_in_to_consider", "10000088") in found
    assert ("missing_in_sap", "10000099") in found


def test_missing_and_extra_sap_dates(make_workbook) -> None:
    cycle = build(make_workbook(drop_sap_rows={(10000002, D1)}, extra_sap_dates=[date(2026, 10, 20)]))
    found = {(i.type, i.employee_id, i.date) for i in cycle.exceptions}
    assert ("missing_employee_date", "10000002", ISO1) in found
    outside = [i for i in cycle.exceptions if i.type == "sap_date_outside_cycle"]
    assert len(outside) == 1 and outside[0].date == "2026-10-20"


def test_manager_problems(make_workbook) -> None:
    cycle = build(make_workbook(employee_overrides={
        10000001: {"Manager Email": "#N/A"},
        10000004: {"Manager Status": "Withdrawn"},
        10000005: {"Manager Status": "Withdrawn"},
    }))
    found = [(i.type, i.employee_id) for i in cycle.exceptions]
    assert ("invalid_manager_email", "10000001") in found
    assert [t for t, _ in found].count("manager_withdrawn") == 1  # one per manager, not per employee


def test_status_not_counted_in_totals_warns(make_workbook) -> None:
    cycle = build(make_workbook(old_macro_codes=True))
    warnings = [i for i in cycle.exceptions if i.type == "status_not_in_totals"]
    assert len(warnings) == 1 and "'ML'" in warnings[0].message


def test_invalid_status_in_sheet_warns(make_workbook) -> None:
    cycle = build(make_workbook(tc_overrides={(10000001, D1): "MTL"}))
    assert "invalid_status" in types(cycle)


def test_missing_sap_sheet_uses_to_consider(make_workbook) -> None:
    cycle = build(make_workbook(include_sap=False))
    assert "sap_sheet_missing" in types(cycle)
    assert "missing_in_sap" not in types(cycle)
    assert cycle.employees[0].days[ISO1] == "A"
    assert cycle.employees[0].calculated_days[ISO1] is None


def test_custom_thresholds_change_calculation(make_workbook) -> None:
    # With a 4h working-day threshold, 4:59:59 becomes P in SAP, so it now differs from the sheet.
    cycle = build(make_workbook(), workday_min_hours=4)
    assert any(m.date == ISO1 and m.calculated == "P" for m in cycle.mismatches if m.employee_id == "10000001")


def test_empty_sheet_is_filled_from_sap(make_workbook) -> None:
    # The usual upload: TO CONSIDER has employee details but no day values yet.
    filled = build(make_workbook(as_uploaded=True))
    reference = build(make_workbook())

    assert filled.filled_days == 180 and filled.compared_days == 0
    assert filled.mismatches == []
    assert types(filled) == ["empty_emp_email"]  # no "empty_day" warnings
    for emp, ref in zip(filled.employees, reference.employees):
        assert emp.days == emp.calculated_days == ref.days
        assert all(value is None for value in emp.sheet_days.values())


def test_sheet_days_keep_upload_values(make_workbook) -> None:
    cycle = build(make_workbook())
    emp = cycle.employees[0]
    assert cycle.filled_days == 0
    assert emp.sheet_days == emp.days  # nothing to write back until someone edits
