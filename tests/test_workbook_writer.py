"""Phase 4: writing back into the workbook."""

import zipfile
from dataclasses import asdict
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
from tests.conftest import HR

DAY = "2026-09-21"


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


def test_empty_upload_becomes_the_macro_output(make_workbook) -> None:
    # Side by side: the app fills the empty upload exactly like the macro-filled file.
    macro = make_workbook().read_bytes()
    _, (out, report) = build(make_workbook(as_uploaded=True))
    assert (report.day_cells, report.total_cells, report.sap_cells) == (180, 6 * 8, 180 * 2)
    assert diff(cells(macro, "TO CONSIDER"), cells(out, "TO CONSIDER")) == set()
    assert diff(cells(macro, "SAP"), cells(out, "SAP")) == set()


def test_filled_workbook_with_no_edits_changes_nothing(make_workbook) -> None:
    original, (out, report) = build(make_workbook())
    assert (report.day_cells, report.total_cells, report.sap_cells) == (0, 0, 0)
    for sheet in ("TO CONSIDER", "SAP", "HC_Teamlease"):
        assert diff(cells(original, sheet), cells(out, sheet)) == set()


def test_existing_formula_cells_are_never_overwritten(make_workbook) -> None:
    original, (out, report) = build(make_workbook(), edits={("10000001", DAY): "CO"})
    after = cells(out, "TO CONSIDER")
    assert after["O2"] == "CO" and report.day_cells == 1
    assert after["AR2"] == '=COUNTIF(N2:AQ2,"P")' and after["AY2"] == "=SUM(AR2:AX2)"


def test_writer_refuses_when_rows_do_not_match(make_workbook) -> None:
    data = make_workbook().read_bytes()
    cycle = build_cycle_data(read_workbook(data))
    layout = layout_to_doc(cycle)
    layout["employee_rows"]["10000001"] = 3  # pretend the layout is stale
    with pytest.raises(WriterError):
        build_updated_workbook(data, layout, [asdict(e) for e in cycle.employees], Thresholds())


def test_side_by_side_script_on_fixture(make_workbook, capsys) -> None:
    assert side_by_side([str(make_workbook())]) == 0
    assert "IDENTICAL" in capsys.readouterr().out


# ---------- the round trip through the API (spec section 11) ----------

def test_round_trip_three_edits(portal, make_workbook) -> None:
    portal.login(HR)
    path = make_workbook()  # a file where TO CONSIDER is already filled
    original = path.read_bytes()
    cycle_id = portal.upload(path)
    edits = [("10000001", "2026-09-21", "CO"), ("10000004", "2026-10-02", "P"), ("10000006", "2026-10-19", "ML")]
    response = portal.save(cycle_id, [{"employee_id": e, "date": d, "value": v} for e, d, v in edits])
    assert response.json()["saved"] == 3

    download = portal.post(f"/hr/cycles/{cycle_id}/download")
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith('attachment; filename="Attendance_20Sep2026_19Oct2026_updated_')
    out = download.content

    before, after = cells(original, "TO CONSIDER"), cells(out, "TO CONSIDER")
    assert diff(before, after) == {"O2", "Z5", "AQ7"}  # exactly the 3 edited cells
    assert (after["O2"], after["Z5"], after["AQ7"]) == ("CO", "P", "ML")
    totals = {ref for ref in before if ref[:2] in ("AR", "AS", "AT", "AU", "AV", "AW", "AX", "AY")}
    assert all(before[ref] == after[ref] for ref in totals)  # every totals formula unchanged
    assert diff(cells(original, "SAP"), cells(out, "SAP")) == set()  # SAP identical
    assert diff(cells(original, "HC_Teamlease"), cells(out, "HC_Teamlease")) == set()


def test_download_of_empty_upload_is_complete(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "CO"}])
    out = portal.post(f"/hr/cycles/{cycle_id}/download").content
    after = cells(out, "TO CONSIDER")
    assert after["O2"] == "CO"  # the edit
    assert after["N2"] == "P" and after["AR2"] == '=COUNTIF(N2:AQ2,"P")'  # filled like the macro
    assert cells(out, "SAP")["U2"].startswith('=IF(G2="PH"')
