from datetime import date, timedelta

from app.config import Settings
from app.core.grid import build_cycle_data
from app.core.workbook_reader import read_workbook

D1 = date(2026, 9, 21)  # a Monday: a working day for everyone
ISO1 = D1.isoformat()


def build(path, **settings):
    return build_cycle_data(read_workbook(path), Settings(_env_file=None, **settings))


def types(cycle) -> list[str]:
    return [issue.type for issue in cycle.exceptions]


def test_every_day_is_filled_from_sap(make_workbook) -> None:
    cycle = build(make_workbook())
    assert cycle.filled_days == 180
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
    assert emp.doj == date(2024, 1, 15) and emp.sheet_cells[0] == 10000001


def test_unknown_schedule(make_workbook) -> None:
    cycle = build(make_workbook(sap_overrides={(10000001, D1): ("NIGHT", timedelta(hours=9))}))
    emp = cycle.employees[0]
    assert emp.calculated_days[ISO1] == "?" and emp.days[ISO1] == "?"
    assert "unknown_schedule" in types(cycle)
    assert cycle.filled_days == 179


def test_missing_sap_row(make_workbook) -> None:
    cycle = build(make_workbook(drop_sap_rows={(10000002, D1)}))
    found = {(i.type, i.employee_id, i.date) for i in cycle.exceptions}
    assert ("missing_employee_date", "10000002", ISO1) in found
    assert cycle.employees[1].days[ISO1] is None


def test_employee_not_in_hc(make_workbook) -> None:
    cycle = build(make_workbook(employee_not_in_hc=True))
    found = {(i.type, i.employee_id) for i in cycle.exceptions}
    assert ("missing_in_hc", "10000088") in found
    assert ("invalid_manager_email", "10000088") in found
    assert ("empty_emp_email", "10000088") not in found  # already covered by missing_in_hc


def test_manager_problems(make_workbook) -> None:
    cycle = build(make_workbook(hc_overrides={
        20000001: {"Email ID": "#N/A"},
        20000002: {"Employment Status": "Withdrawn"},
    }))
    found = [(i.type, i.employee_id) for i in cycle.exceptions]
    assert ("invalid_manager_email", "10000001") in found
    assert [t for t, _ in found].count("manager_withdrawn") == 1  # one per manager, not per employee


def test_status_not_counted_in_totals_warns(make_workbook) -> None:
    cycle = build(make_workbook(), allowed_statuses="P,A,OFF,PH,CO,ML,PL,LOP")
    warnings = [i for i in cycle.exceptions if i.type == "status_not_in_totals"]
    assert len(warnings) == 1 and "'LOP'" in warnings[0].message


def test_custom_thresholds_change_calculation(make_workbook) -> None:
    # With a 4h working-day threshold, 4:59:59 becomes P.
    cycle = build(make_workbook(), workday_min_hours=4)
    assert cycle.employees[0].days[ISO1] == "P"
