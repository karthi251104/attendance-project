from datetime import date

from scripts.verify_against_macro import main


def test_verify_reports_zero_mismatches(make_workbook, capsys) -> None:
    assert main([str(make_workbook())]) == 0
    out = capsys.readouterr().out
    assert "Mismatches: 0" in out and "Matches:    180" in out


def test_verify_reports_mismatches(make_workbook, capsys) -> None:
    path = make_workbook(tc_overrides={(10000001, date(2026, 9, 21)): "P"})
    assert main([str(path)]) == 1
    out = capsys.readouterr().out
    assert "Mismatches: 1" in out
    assert "10000001  2026-09-21  TO CONSIDER=P  SAP=A" in out
    assert "Fake Employee" not in out  # never print names


def test_verify_bad_file(make_workbook, capsys) -> None:
    assert main([str(make_workbook(include_to_consider=False))]) == 2


def test_verify_empty_sheet_explains_nothing_to_compare(make_workbook, capsys) -> None:
    assert main([str(make_workbook(as_uploaded=True))]) == 0
    out = capsys.readouterr().out
    assert "filled from SAP: 180" in out and "Nothing to compare" in out
