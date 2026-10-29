"""Phase 2: upload pipeline and HR screens."""

import pytest

from app.core.processing import process_upload
from app.core.workbook_reader import WorkbookError
from tests.conftest import HR, MANAGER_ONE


def test_upload_saves_cycle_employees_and_exceptions(uploaded) -> None:
    portal, cycle_id = uploaded
    cycle = portal.repo.get_cycle(cycle_id)
    assert cycle["name"] == "20 Sep 2026 - 19 Oct 2026"
    assert cycle["status"] == "open" and cycle["active"] is True
    assert len(cycle["dates"]) == 30
    assert cycle["stats"] == {"employees": 6, "managers": 2, "exceptions": 1, "mismatches": 0, "filled_from_sap": 180}
    assert cycle["sheet_layout"]["date_columns"]["2026-09-20"] == "N"
    assert cycle["sheet_layout"]["sap_columns"]["remarks"] == "U"
    assert portal.files.load(cycle["original_file_blob"])[:2] == b"PK"

    employees = portal.repo.list_employees(cycle_id)
    assert len(employees) == 6
    emp = portal.repo.get_employee(cycle_id, "10000001")
    assert emp["days"]["2026-09-21"] == "A" and emp["sheet_days"]["2026-09-21"] is None
    assert emp["worked_seconds"]["2026-09-21"] == 17999 and emp["schedule"]["2026-09-21"] == "6DAG"
    assert portal.repo.get_employee(cycle_id, "10000004")["manager_email"] == "manager.two@example.com"
    assert [e["type"] for e in portal.repo.list_exceptions(cycle_id)] == ["empty_emp_email"]


def test_deadline_is_end_of_day_in_ist(portal, make_workbook) -> None:
    portal.login(HR)
    cycle_id = portal.upload(make_workbook(as_uploaded=True), deadline="2026-11-05")
    deadline = portal.repo.get_cycle(cycle_id)["correction_deadline"]
    assert deadline.isoformat().startswith("2026-11-05T18:29:59")  # 23:59:59 IST


def test_review_page_shows_summary(uploaded) -> None:
    portal, cycle_id = uploaded
    page = portal.client.get(f"/hr/cycles/{cycle_id}").text
    for text in ("20 Sep 2026 - 19 Oct 2026", "Employees", "Fake Manager One", "manager.one@example.com",
                 "Send emails to all managers", "Download updated workbook", "Lock cycle", "empty emp email"):
        assert text in page
    assert "20 Sep 2026" in portal.client.get("/hr").text


def test_exceptions_csv(uploaded) -> None:
    portal, cycle_id = uploaded
    response = portal.client.get(f"/hr/cycles/{cycle_id}/exceptions.csv")
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().splitlines()
    assert lines[0].lstrip("﻿").startswith("Severity,Type") and "empty_emp_email" in lines[1]


@pytest.mark.parametrize("options, filename, message", [
    ({}, "notes.txt", "Please upload an Excel workbook"),
    ({"include_to_consider": False}, None, "TO CONSIDER&#39; sheet. Please check that you uploaded"),
    ({"formulas_only": True}, None, "Please open the file in Excel, save it, and upload again."),
])
def test_bad_uploads_show_clear_errors(portal, make_workbook, options, filename, message) -> None:
    portal.login(HR)
    path = make_workbook(**options)
    response = portal.client.post("/hr/upload", data={"csrf_token": portal.csrf},
                                  files={"file": (filename or path.name, path.read_bytes(), "application/octet-stream")})
    assert response.status_code == 400 and message in response.text
    assert portal.repo.list_cycles() == []


def test_not_a_real_excel_file(portal) -> None:
    portal.login(HR)
    response = portal.client.post("/hr/upload", data={"csrf_token": portal.csrf},
                                  files={"file": ("fake.xlsm", b"not a zip", "application/octet-stream")})
    assert response.status_code == 400 and "could not be opened" in response.text


def test_file_too_large(portal_factory, settings, make_workbook) -> None:
    portal = portal_factory(settings.model_copy(update={"max_upload_mb": 0}))
    portal.login(HR)
    response = portal.client.post("/hr/upload", data={"csrf_token": portal.csrf},
                                  files={"file": ("w.xlsx", make_workbook().read_bytes(), "application/octet-stream")})
    assert response.status_code == 400 and "larger than 0 MB" in response.text


def test_failed_save_rolls_back(portal, settings, make_workbook, monkeypatch) -> None:
    def broken(docs):
        raise RuntimeError("database down")

    monkeypatch.setattr(portal.repo, "insert_employees", broken)
    with pytest.raises(RuntimeError):
        process_upload(filename="w.xlsx", data=make_workbook().read_bytes(), uploaded_by=HR, correction_deadline=None,
                       repo=portal.repo, files=portal.files, settings=settings)
    assert portal.repo.list_cycles() == []
    assert portal.files._files == {}


def test_manager_cannot_upload(portal, make_workbook) -> None:
    portal.login(MANAGER_ONE)
    path = make_workbook()
    response = portal.client.post("/hr/upload", data={"csrf_token": portal.csrf},
                                  files={"file": (path.name, path.read_bytes(), "application/octet-stream")})
    assert response.status_code == 403


def test_process_upload_rejects_wrong_extension(portal, settings, make_workbook) -> None:
    with pytest.raises(WorkbookError):
        process_upload(filename="w.csv", data=make_workbook().read_bytes(), uploaded_by=HR, correction_deadline=None,
                       repo=portal.repo, files=portal.files, settings=settings)
