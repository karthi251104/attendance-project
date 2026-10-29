"""Phase 3: managers see and edit only their own team; every edit is logged; lock and deadline rules."""

from datetime import timedelta

from app.core.timeutil import now_utc
from tests.conftest import HR, MANAGER_ONE, MANAGER_TWO, OUTSIDER

TEAM_ONE = {"10000001", "10000002", "10000003"}
TEAM_TWO = {"10000004", "10000005", "10000006"}
DAY = "2026-09-21"  # 10000001 worked 4:59:59 on a 6DAG day -> A


def grid_ids(html: str) -> set[str]:
    import re
    return set(re.findall(r'data-employee-id="(\d+)"', html))


# ---------- reading ----------

def test_manager_sees_only_own_team(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login(MANAGER_ONE)
    page = portal.client.get(f"/team/{cycle_id}")
    assert page.status_code == 200
    assert grid_ids(page.text) == TEAM_ONE
    for other in ("Fake Employee D", "Fake Employee E", "Fake Employee F"):
        assert other not in page.text


def test_manager_email_match_is_case_insensitive(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login("Manager.Two@EXAMPLE.com")  # the sheet has "Manager.Two@Example.com"
    assert grid_ids(portal.client.get(f"/team/{cycle_id}").text) == TEAM_TWO


def test_user_without_team_gets_no_access(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login(OUTSIDER)
    response = portal.client.get(f"/team/{cycle_id}")
    assert response.status_code == 403
    assert "data-employee-id" not in response.text


def test_manager_cannot_open_hr_pages(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login(MANAGER_ONE)
    for url in ("/hr", "/hr/upload", f"/hr/cycles/{cycle_id}", f"/hr/cycles/{cycle_id}/employees",
                f"/hr/cycles/{cycle_id}/changes", f"/hr/cycles/{cycle_id}/changes.csv", f"/hr/cycles/{cycle_id}/exceptions.csv"):
        assert portal.client.get(url).status_code == 403, url
    assert portal.post(f"/hr/cycles/{cycle_id}/download").status_code == 403
    assert portal.post(f"/hr/cycles/{cycle_id}/status", action="lock").status_code == 403


def test_hr_sees_everyone(uploaded) -> None:
    portal, cycle_id = uploaded
    assert grid_ids(portal.client.get(f"/hr/cycles/{cycle_id}/employees").text) == TEAM_ONE | TEAM_TWO
    filtered = portal.client.get(f"/hr/cycles/{cycle_id}/employees", params={"manager": MANAGER_TWO})
    assert grid_ids(filtered.text) == TEAM_TWO


def test_not_logged_in(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.client.cookies.clear()
    page = portal.client.get(f"/team/{cycle_id}", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"].startswith("/login?next=")
    assert portal.save(cycle_id, []).status_code in (401, 403)


def test_unknown_cycle(uploaded) -> None:
    portal, _ = uploaded
    assert portal.client.get("/team/not-an-id").status_code == 403
    assert portal.client.get("/team/0123456789abcdef01234567").status_code == 403


# ---------- editing ----------

def test_manager_edit_is_saved_and_logged(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login(MANAGER_ONE)
    response = portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "co", "old_value": "A"}], comment="Worked Sunday")
    assert response.status_code == 200 and response.json()["saved"] == 1

    emp = portal.repo.get_employee(cycle_id, "10000001")
    assert emp["days"][DAY] == "CO"
    assert emp["original_days"][DAY] == "A"  # never edited
    [change] = portal.repo.list_changes(cycle_id)
    assert (change["old_value"], change["new_value"], change["changed_by_email"], change["changed_by_role"], change["comment"]) == \
        ("A", "CO", MANAGER_ONE, "manager", "Worked Sunday")


def test_manager_cannot_edit_other_team(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login(MANAGER_ONE)
    response = portal.save(cycle_id, [
        {"employee_id": "10000001", "date": DAY, "value": "P"},
        {"employee_id": "10000004", "date": DAY, "value": "P"},  # team two
    ])
    assert response.status_code == 403
    # All or nothing: the allowed change was not applied either.
    assert portal.repo.get_employee(cycle_id, "10000001")["days"][DAY] == "A"
    assert portal.repo.list_changes(cycle_id) == []


def test_unknown_employee_gets_same_error_as_other_team(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login(MANAGER_ONE)
    assert portal.save(cycle_id, [{"employee_id": "99999999", "date": DAY, "value": "P"}]).json() == \
        portal.save(cycle_id, [{"employee_id": "10000004", "date": DAY, "value": "P"}]).json()


def test_outsider_cannot_save(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login(OUTSIDER)
    assert portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "P"}]).status_code == 403


def test_hr_can_edit_any_employee(uploaded) -> None:
    portal, cycle_id = uploaded
    response = portal.save(cycle_id, [{"employee_id": "10000004", "date": DAY, "value": "ML"}])
    assert response.json()["saved"] == 1
    assert portal.repo.list_changes(cycle_id)[0]["changed_by_role"] == "hr"


def test_server_validates_status_and_date(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login(MANAGER_ONE)
    for bad in ({"value": "?"}, {"value": "X"}, {"value": "MTL"}, {"date": "2026-10-20"}, {"date": "garbage"}):
        change = {"employee_id": "10000001", "date": DAY, "value": "P", **bad}
        assert portal.save(cycle_id, [change]).status_code == 400, bad


def test_unchanged_value_is_not_logged(uploaded) -> None:
    portal, cycle_id = uploaded
    result = portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "A"}]).json()
    assert result == {"saved": 0, "unchanged": 1, "conflicts": []}
    assert portal.repo.list_changes(cycle_id) == []


def test_concurrent_edit_is_reported_not_overwritten(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "P", "old_value": "A"}])  # HR first
    portal.login(MANAGER_ONE)
    result = portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "CO", "old_value": "A"}]).json()
    assert result["saved"] == 0 and result["conflicts"] == [{"employee_id": "10000001", "date": DAY, "current_value": "P"}]
    assert portal.repo.get_employee(cycle_id, "10000001")["days"][DAY] == "P"


def test_edited_cells_are_highlighted(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "CO"}])
    page = portal.client.get(f"/team/{cycle_id}").text
    assert page.count('class="day edited"') == 1


def test_csrf_is_required(uploaded) -> None:
    portal, cycle_id = uploaded
    no_token = portal.client.post(f"/api/cycles/{cycle_id}/changes", json={"changes": []})
    wrong = portal.client.post(f"/api/cycles/{cycle_id}/changes", json={"changes": []}, headers={"X-CSRF-Token": "wrong"})
    form = portal.client.post(f"/hr/cycles/{cycle_id}/status", data={"action": "lock"})
    assert (no_token.status_code, wrong.status_code, form.status_code) == (403, 403, 403)
    assert portal.repo.get_cycle(cycle_id)["status"] == "open"


# ---------- lock, deadline, versions ----------

def test_locked_cycle_blocks_everyone(uploaded) -> None:
    portal, cycle_id = uploaded
    assert portal.post(f"/hr/cycles/{cycle_id}/status", action="lock").status_code == 303
    assert portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "P"}]).status_code == 403
    portal.login(MANAGER_ONE)
    page = portal.client.get(f"/team/{cycle_id}").text
    assert "HR has locked this cycle" in page and "<select" not in page
    assert portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "P"}]).status_code == 403


def test_unlock_reopens(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.post(f"/hr/cycles/{cycle_id}/status", action="lock")
    portal.post(f"/hr/cycles/{cycle_id}/status", action="unlock")
    portal.login(MANAGER_ONE)
    assert portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "P"}]).status_code == 200


def test_deadline_passed_blocks_managers_not_hr(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.repo.set_cycle_fields(cycle_id, {"correction_deadline": now_utc() - timedelta(minutes=1)})
    assert portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "P"}]).status_code == 200  # HR
    portal.login(MANAGER_ONE)
    assert "deadline" in portal.client.get(f"/team/{cycle_id}").text
    response = portal.save(cycle_id, [{"employee_id": "10000002", "date": DAY, "value": "CO"}])
    assert response.status_code == 403 and "deadline" in response.json()["error"]


def test_future_deadline_allows_managers(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.post(f"/hr/cycles/{cycle_id}/deadline", deadline="2099-12-31")
    portal.login(MANAGER_ONE)
    assert portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "P"}]).status_code == 200


def test_new_upload_replaces_old_version(uploaded, make_workbook) -> None:
    portal, old_id = uploaded
    new_id = portal.upload(make_workbook(as_uploaded=True))
    assert portal.repo.get_cycle(new_id)["active"] and not portal.repo.get_cycle(old_id)["active"]
    portal.login(MANAGER_ONE)
    assert portal.client.get("/", follow_redirects=False).headers["location"] == f"/team/{new_id}"
    assert "replaced by a newer version" in portal.client.get(f"/team/{old_id}").text
    assert portal.save(old_id, [{"employee_id": "10000001", "date": DAY, "value": "P"}]).status_code == 403


def test_hr_can_reactivate_old_version(uploaded, make_workbook) -> None:
    portal, old_id = uploaded
    new_id = portal.upload(make_workbook(as_uploaded=True))
    portal.post(f"/hr/cycles/{old_id}/status", action="activate")
    assert portal.repo.get_cycle(old_id)["active"] and not portal.repo.get_cycle(new_id)["active"]


# ---------- history ----------

def test_change_history_and_csv(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "CO"}], comment="=HYPERLINK(evil)")
    page = portal.client.get(f"/hr/cycles/{cycle_id}/changes")
    assert "21/09/2026" in page.text and "CO" in page.text
    csv_text = portal.client.get(f"/hr/cycles/{cycle_id}/changes.csv").text
    assert "10000001" in csv_text and "21/09/2026" in csv_text
    assert "'=HYPERLINK(evil)" in csv_text  # protected against CSV formula injection
    assert portal.client.get(f"/hr/cycles/{cycle_id}/changes.csv", params={"employee": "10000002"}).text.count("\n") == 1


def test_review_counts_changes_per_manager(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "CO"},
                           {"employee_id": "10000002", "date": DAY, "value": "CO"}])
    assert portal.repo.change_counts_by_manager(cycle_id) == {MANAGER_ONE: 2}
