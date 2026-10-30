"""Upload pipeline and the HR review data."""

from datetime import date

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
    assert cycle["stats"] == {"employees": 6, "managers": 2, "exceptions": 1, "filled_from_sap": 180}
    assert cycle["sheet_layout"]["date_columns"]["2026-09-20"] == "M"
    assert cycle["sheet_layout"]["sap_columns"]["remarks"] == "U"
    assert portal.files.load(cycle["original_file_blob"])[:2] == b"PK"

    employees = portal.repo.list_employees(cycle_id)
    assert len(employees) == 6
    emp = portal.repo.get_employee(cycle_id, "10000001")
    assert emp["days"]["2026-09-21"] == "A" and emp["sheet_cells"][0] == 10000001
    assert emp["doj"].date() == date(2024, 1, 15)
    assert emp["worked_seconds"]["2026-09-21"] == 17999 and emp["schedule"]["2026-09-21"] == "6DAG"
    assert portal.repo.get_employee(cycle_id, "10000004")["manager_email"] == "manager.two@example.com"
    assert [e["type"] for e in portal.repo.list_exceptions(cycle_id)] == ["empty_emp_email"]


def test_deadline_is_end_of_day_in_ist(portal, make_workbook) -> None:
    portal.login(HR)
    cycle_id = portal.upload(make_workbook(), deadline="2026-11-05")
    deadline = portal.repo.get_cycle(cycle_id)["correction_deadline"]
    assert deadline.isoformat().startswith("2026-11-05T18:29:59")  # 23:59:59 IST


def test_review_data(uploaded) -> None:
    portal, cycle_id = uploaded
    cycle = portal.get(f"/api/cycles/{cycle_id}").json()
    assert cycle["name"] == "20 Sep 2026 - 19 Oct 2026" and cycle["role"] == "hr"
    assert (cycle["start_date"], cycle["end_date"], cycle["uploaded_by"]) == ("2026-09-20", "2026-10-19", HR)
    assert cycle["stats"]["employees"] == 6 and cycle["can_edit"] is True
    [listed] = portal.get("/api/cycles").json()["cycles"]
    assert listed["id"] == cycle_id and "dates" not in listed
    [issue] = portal.get(f"/api/cycles/{cycle_id}/exceptions").json()["exceptions"]
    assert (issue["type"], issue["employee_id"]) == ("empty_emp_email", "10000002")


def test_exceptions_csv(uploaded) -> None:
    portal, cycle_id = uploaded
    response = portal.get(f"/api/cycles/{cycle_id}/exceptions.csv")
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().splitlines()
    assert lines[0].lstrip("\ufeff").startswith("Severity,Type") and "empty_emp_email" in lines[1]


@pytest.mark.parametrize("options, filename, message", [
    ({}, "notes.txt", "Please upload an Excel workbook"),
    ({"include_hc": False}, None, "HC_Teamlease' sheet. Please check that you uploaded"),
    ({"with_to_consider": True}, None, "already has a 'TO CONSIDER' sheet"),
])
def test_bad_uploads_show_clear_errors(portal, make_workbook, options, filename, message) -> None:
    portal.login(HR)
    path = make_workbook(**options)
    response = portal.upload_response(path, filename=filename)
    assert response.status_code == 400 and message in response.json()["error"]
    assert portal.repo.list_cycles() == []


def test_not_a_real_excel_file(portal) -> None:
    portal.login(HR)
    response = portal.client.post("/api/cycles", headers={"X-CSRF-Token": portal.csrf},
                                  files={"file": ("fake.xlsm", b"not a zip", "application/octet-stream")})
    assert response.status_code == 400 and "could not be opened" in response.json()["error"]


def test_file_too_large(portal_factory, settings, make_workbook) -> None:
    portal = portal_factory(settings.model_copy(update={"max_upload_mb": 0}))
    portal.login(HR)
    response = portal.upload_response(make_workbook())
    assert response.status_code == 400 and "larger than 0 MB" in response.json()["error"]


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
    assert portal.upload_response(make_workbook()).status_code == 403


def test_process_upload_rejects_wrong_extension(portal, settings, make_workbook) -> None:
    with pytest.raises(WorkbookError):
        process_upload(filename="w.csv", data=make_workbook().read_bytes(), uploaded_by=HR, correction_deadline=None,
                       repo=portal.repo, files=portal.files, settings=settings)
