"""Phase 4: writing back into the workbook."""

import zipfile
from dataclasses import asdict
from datetime import date, datetime, timedelta
from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook

from app.core.grid import build_cycle_data
from app.core.processing import layout_to_doc
from app.core.rules import Thresholds
from app.core.workbook_reader import read_workbook
from app.core.workbook_writer import WriterError, build_updated_workbook, remarks_formula
from app.core.xlsx_patch import XlsxPatcher
from scripts.side_by_side import main as side_by_side

DAY = "2026-09-21"  # column N


def cells(data: bytes, sheet: str) -> dict[str, object]:
    wb = load_workbook(BytesIO(data), data_only=False)
    return {c.coordinate: c.value for row in wb[sheet].iter_rows() for c in row if c.value is not None}


def diff(a: dict, b: dict) -> set[str]:
    return {ref for ref in set(a) | set(b) if a.get(ref) != b.get(ref)}


# ---------- the XML patcher ----------

def blank_workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["A1"], ws["C1"], ws["A3"] = "head", 7, "x"
    ws["B3"] = "=A3"
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def test_patcher_inserts_cells_and_rows_in_order() -> None:
    patcher = XlsxPatcher(blank_workbook())
    sheet = patcher.sheet("data")  # sheet names match case-insensitively
    sheet.set_text("B1", "between")       # new cell between A1 and C1
    sheet.set_text("A2", "new row")       # new row between rows 1 and 3
    sheet.set_formula("D3", "=SUM(C1:C2)")
    patcher.set_full_calc_on_load()
    out = patcher.save()

    wb = load_workbook(BytesIO(out))
    ws = wb["Data"]
    assert [ws["A1"].value, ws["B1"].value, ws["C1"].value] == ["head", "between", 7]
    assert ws["A2"].value == "new row" and ws["B3"].value == "=A3" and ws["D3"].value == "=SUM(C1:C2)"
    assert wb.calculation.fullCalcOnLoad is True


def test_patcher_reports_cell_state() -> None:
    sheet = XlsxPatcher(blank_workbook()).sheet("Data")
    assert sheet.state("A1").kind == "value"
    assert sheet.state("B3").kind == "formula"
    assert sheet.state("Z9").kind == "empty"
    sheet.clear("A1")
    assert sheet.state("A1").kind == "empty"


def test_patcher_leaves_other_parts_unchanged() -> None:
    original = blank_workbook()
    patcher = XlsxPatcher(original)
    patcher.sheet("Data").set_text("E5", "hi")
    out = patcher.save()
    a, b = zipfile.ZipFile(BytesIO(original)), zipfile.ZipFile(BytesIO(out))
    changed = {n for n in a.namelist() if a.read(n) != b.read(n)}
    assert changed == {"xl/worksheets/sheet1.xml"}
    assert a.namelist() == b.namelist()


def test_remarks_formula_matches_macro() -> None:
    assert remarks_formula(2, "G", "R", Thresholds(), numeric=True) == (
        'IF(G2="PH",IF(R2>TIME(4,59,59),1,4),IF(OR(G2="5DAG",G2="6DAG"),IF(R2>TIME(4,59,59),1,2),'
        'IF(G2="OFF",IF(R2>TIME(7,59,59),1,3))))')
    assert remarks_formula(3, "G", "R", Thresholds(), numeric=False) == (
        'IF(G3="PH",IF(R3>TIME(4,59,59),"P","PH"),IF(OR(G3="5DAG",G3="6DAG"),IF(R3>TIME(4,59,59),"P","A"),'
        'IF(G3="OFF",IF(R3>TIME(7,59,59),"P","OFF"))))')


def test_patcher_adds_and_removes_a_sheet() -> None:
    patcher = XlsxPatcher(blank_workbook())
    sheet = patcher.add_sheet("New", position=0)
    sheet.set_number("A1", 42)
    sheet.set_style("A1", patcher.add_style(bold=True, border=True))
    out = patcher.save()
    wb = load_workbook(BytesIO(out))
    assert wb.sheetnames == ["New", "Data"]
    assert wb["New"]["A1"].value == 42 and wb["New"]["A1"].font.b and wb["New"]["A1"].border.left.style == "thin"
    assert wb.active.title == "Data"  # the active sheet stays the same

    patcher = XlsxPatcher(out)
    patcher.remove_sheet("New")
    assert load_workbook(BytesIO(patcher.save())).sheetnames == ["Data"]


# ---------- building the updated workbook ----------

def build(path_or_bytes, edits: dict[tuple[str, str], str] | None = None):
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else path_or_bytes.read_bytes()
    cycle = build_cycle_data(read_workbook(data))
    employees = [asdict(e) for e in cycle.employees]
    for emp in employees:
        for (emp_id, iso), value in (edits or {}).items():
            if emp["employee_id"] == emp_id:
                emp["days"][iso] = value
    return data, build_updated_workbook(data, layout_to_doc(cycle), employees, Thresholds())


def test_upload_becomes_the_macro_output(make_workbook) -> None:
    # The fixture's macro version has the same SAP formulas and the same day values.
    macro = make_workbook(with_to_consider=True).read_bytes()
    _, (out, report) = build(make_workbook())
    assert (report.employee_rows, report.day_cells, report.total_cells, report.sap_cells) == (6, 180, 6 * 8, 180 * 2)
    assert diff(cells(macro, "SAP"), cells(out, "SAP")) == set()
    assert load_workbook(BytesIO(out)).sheetnames == ["TO CONSIDER", "SAP", "HC_Teamlease"]


def test_to_consider_layout(make_workbook) -> None:
    _, (out, _) = build(make_workbook(employee_not_in_hc=True))
    sheet = load_workbook(BytesIO(out))["TO CONSIDER"]
    headers = [c.value for c in sheet[1]]
    assert headers[:12] == ["Employee ID", "Employee Name", "DOJ", "Emp Status", "LWD", "Department", "Parent Org",
                            "Emp Email ID", "Manager ID", "Manager Name", "Manager Email", "Manager Status"]
    assert headers[12] == datetime(2026, 9, 20) and headers[41] == datetime(2026, 10, 19)
    assert headers[42:] == ["Present", "Absent", "Weekly Off", "Comp Off", "Maternity Leave", "Maternity Leave",
                            "PH", "Total", "Remarks"]

    row = [c.value for c in sheet[2]]
    assert row[:12] == [10000001, "Fake Employee A", datetime(2024, 1, 15), "Active", None, "Dept M1", "Fake Org",
                        "emp.a@example.com", 20000001, "Fake Manager One", "manager.one@example.com", "Active"]
    assert sheet["C2"].number_format == "mm-dd-yy"  # Excel's built-in short date
    assert row[12:15] == ["P", "A", "P"]
    assert sheet["AQ2"].value == '=COUNTIF(M2:AP2,"P")' and sheet["AX2"].value == "=SUM(AQ2:AW2)"
    assert sheet["A1"].font.b and sheet["B2"].border.bottom.style == "thin"

    missing = [c.value for c in sheet[8]]  # 10000088 has no HC_Teamlease row
    assert missing[:2] == [10000088, "Fake Only In SAP"] and missing[2:12] == ["#N/A"] * 10


def test_edits_go_into_the_sheet(make_workbook) -> None:
    _, (out, report) = build(make_workbook(), edits={("10000001", DAY): "CO"})
    after = cells(out, "TO CONSIDER")
    assert after["N2"] == "CO" and report.day_cells == 180


def test_unknown_status_is_left_empty(make_workbook) -> None:
    _, (out, report) = build(make_workbook(sap_overrides={(10000001, date(2026, 9, 21)): ("NIGHT", timedelta(hours=9))}))
    assert "N2" not in cells(out, "TO CONSIDER") and report.day_cells == 179


def test_writer_refuses_a_file_with_to_consider(make_workbook) -> None:
    data = make_workbook().read_bytes()
    cycle = build_cycle_data(read_workbook(data))
    macro = make_workbook(with_to_consider=True).read_bytes()
    with pytest.raises(WriterError):
        build_updated_workbook(macro, layout_to_doc(cycle), [asdict(e) for e in cycle.employees], Thresholds())


def test_side_by_side_script_on_fixture(make_workbook, capsys) -> None:
    assert side_by_side([str(make_workbook(with_to_consider=True))]) == 0
    assert "SAME as the macro output" in capsys.readouterr().out


def test_side_by_side_script_finds_differences(make_workbook, capsys) -> None:
    path = make_workbook(with_to_consider=True)
    wb = load_workbook(path)
    wb["TO CONSIDER"]["N2"] = "CO"  # the macro file says CO, SAP calculates P
    wb.save(path)
    assert side_by_side([str(path)]) == 1
    out = capsys.readouterr().out
    assert "TO CONSIDER 10000001 [2026-09-20]: macro='CO' app='P'" in out
    assert "Fake Employee" not in out  # never print names


# ---------- the round trip through the API ----------

def test_round_trip_three_edits(uploaded) -> None:
    portal, cycle_id = uploaded
    edits = [("10000001", "2026-09-21", "CO"), ("10000004", "2026-10-02", "P"), ("10000006", "2026-10-19", "ML")]
    response = portal.save(cycle_id, [{"employee_id": e, "date": d, "value": v} for e, d, v in edits])
    assert response.json()["saved"] == 3

    download = portal.get(f"/api/cycles/{cycle_id}/download")
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith('attachment; filename="Attendance_20Sep2026_19Oct2026_updated_')
    out = download.content

    original = portal.files.load(portal.repo.get_cycle(cycle_id)["original_file_blob"])
    _, (unedited, _) = build(original)
    before, after = cells(unedited, "TO CONSIDER"), cells(out, "TO CONSIDER")
    assert diff(before, after) == {"N2", "Y5", "AP7"}  # exactly the 3 edited cells
    assert (after["N2"], after["Y5"], after["AP7"]) == ("CO", "P", "ML")
    assert after["C2"] == datetime(2024, 1, 15)  # dates survive the trip through MongoDB
    assert cells(out, "SAP")["U2"].startswith('=IF(G2="PH"')
    assert diff(cells(original, "HC_Teamlease"), cells(out, "HC_Teamlease")) == set()
