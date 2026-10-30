"""Managers see and edit only their own team; every edit is logged; lock and deadline rules."""

from datetime import timedelta

from app.core.timeutil import now_utc
from tests.conftest import HR, MANAGER_ONE, MANAGER_TWO, OUTSIDER

TEAM_ONE = {"10000001", "10000002", "10000003"}
TEAM_TWO = {"10000004", "10000005", "10000006"}
DAY = "2026-09-21"  # 10000001 worked 4:59:59 on a 6DAG day -> A


def team_ids(portal, cycle_id: str, **params) -> set[str]:
    response = portal.get(f"/api/cycles/{cycle_id}/employees", **params)
    assert response.status_code == 200, response.text
    return {e["employee_id"] for e in response.json()["employees"]}


# ---------- reading ----------

def test_manager_sees_only_own_team(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login(MANAGER_ONE)
    response = portal.get(f"/api/cycles/{cycle_id}/employees")
    assert {e["employee_id"] for e in response.json()["employees"]} == TEAM_ONE
    assert "Fake Employee D" not in response.text
    assert "manager_email" not in response.json()["employees"][0]  # managers do not get other people's emails


def test_manager_filters_are_ignored(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login(MANAGER_ONE)
    assert team_ids(portal, cycle_id, manager=MANAGER_TWO) == TEAM_ONE


def test_manager_email_match_is_case_insensitive(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login("Manager.Two@EXAMPLE.com")  # the sheet has "Manager.Two@Example.com"
    assert team_ids(portal, cycle_id) == TEAM_TWO


def test_cycle_for_manager(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login(MANAGER_ONE)
    cycle = portal.get(f"/api/cycles/{cycle_id}").json()
    assert cycle["role"] == "manager" and cycle["can_edit"] is True and len(cycle["dates"]) == 30
    assert [c["id"] for c in portal.get("/api/my-cycles").json()["cycles"]] == [cycle_id]


def test_user_without_team_gets_no_access(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login(OUTSIDER)
    for url in (f"/api/cycles/{cycle_id}", f"/api/cycles/{cycle_id}/employees"):
        response = portal.get(url)
        assert response.status_code == 403 and "Fake" not in response.text


def test_manager_cannot_use_hr_endpoints(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.login(MANAGER_ONE)
    for url in ("/api/cycles", f"/api/cycles/{cycle_id}/managers", f"/api/cycles/{cycle_id}/exceptions",
                f"/api/cycles/{cycle_id}/exceptions.csv", f"/api/cycles/{cycle_id}/changes",
                f"/api/cycles/{cycle_id}/changes.csv", f"/api/cycles/{cycle_id}/download"):
        assert portal.get(url).status_code == 403, url
    assert portal.post(f"/api/cycles/{cycle_id}/status", {"action": "lock"}).status_code == 403
    assert portal.post(f"/api/cycles/{cycle_id}/emails").status_code == 403
    assert portal.put(f"/api/cycles/{cycle_id}/deadline", {"deadline": "2099-01-01"}).status_code == 403


def test_hr_sees_everyone_and_can_filter(uploaded) -> None:
    portal, cycle_id = uploaded
    assert team_ids(portal, cycle_id) == TEAM_ONE | TEAM_TWO
    assert team_ids(portal, cycle_id, manager=MANAGER_TWO) == TEAM_TWO
    assert team_ids(portal, cycle_id, department="Dept M1") == TEAM_ONE
    employee = portal.get(f"/api/cycles/{cycle_id}/employees").json()["employees"][0]
    assert employee["manager_email"] in (MANAGER_ONE, MANAGER_TWO)


def test_not_logged_in(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.client.cookies.clear()
    assert portal.get(f"/api/cycles/{cycle_id}/employees").status_code == 401
    assert portal.save(cycle_id, []).status_code in (401, 403)


def test_unknown_cycle(uploaded) -> None:
    portal, _ = uploaded
    assert portal.get("/api/cycles/not-an-id").status_code == 403
    assert portal.get("/api/cycles/0123456789abcdef01234567").status_code == 403


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


def test_edits_show_against_the_original(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "CO"}])
    emp = portal.get(f"/api/cycles/{cycle_id}/employees").json()["employees"][0]
    assert (emp["days"][DAY], emp["original_days"][DAY], emp["calculated_days"][DAY]) == ("CO", "A", "A")
    assert emp["schedule"][DAY] == "6DAG" and emp["worked_seconds"][DAY] == 17999


def test_csrf_is_required(uploaded) -> None:
    portal, cycle_id = uploaded
    no_token = portal.client.post(f"/api/cycles/{cycle_id}/changes", json={"changes": []})
    wrong = portal.client.post(f"/api/cycles/{cycle_id}/changes", json={"changes": []}, headers={"X-CSRF-Token": "wrong"})
    lock = portal.client.post(f"/api/cycles/{cycle_id}/status", json={"action": "lock"})
    assert (no_token.status_code, wrong.status_code, lock.status_code) == (403, 403, 403)
    assert portal.repo.get_cycle(cycle_id)["status"] == "open"


# ---------- lock, deadline, versions ----------

def test_locked_cycle_blocks_everyone(uploaded) -> None:
    portal, cycle_id = uploaded
    assert portal.post(f"/api/cycles/{cycle_id}/status", {"action": "lock"}).json()["status"] == "locked"
    assert portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "P"}]).status_code == 403
    portal.login(MANAGER_ONE)
    cycle = portal.get(f"/api/cycles/{cycle_id}").json()
    assert cycle["can_edit"] is False and "HR has locked this cycle" in cycle["read_only_reason"]
    assert portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "P"}]).status_code == 403


def test_unlock_reopens(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.post(f"/api/cycles/{cycle_id}/status", {"action": "lock"})
    portal.post(f"/api/cycles/{cycle_id}/status", {"action": "unlock"})
    portal.login(MANAGER_ONE)
    assert portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "P"}]).status_code == 200


def test_bad_status_action_is_rejected(uploaded) -> None:
    portal, cycle_id = uploaded
    assert portal.post(f"/api/cycles/{cycle_id}/status", {"action": "explode"}).status_code == 422


def test_deadline_passed_blocks_managers_not_hr(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.repo.set_cycle_fields(cycle_id, {"correction_deadline": now_utc() - timedelta(minutes=1)})
    assert portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "P"}]).status_code == 200  # HR
    portal.login(MANAGER_ONE)
    assert "deadline" in portal.get(f"/api/cycles/{cycle_id}").json()["read_only_reason"]
    response = portal.save(cycle_id, [{"employee_id": "10000002", "date": DAY, "value": "CO"}])
    assert response.status_code == 403 and "deadline" in response.json()["error"]


def test_set_and_remove_deadline(uploaded) -> None:
    portal, cycle_id = uploaded
    cycle = portal.put(f"/api/cycles/{cycle_id}/deadline", {"deadline": "2099-12-31"}).json()
    assert cycle["deadline_date"] == "2099-12-31" and cycle["correction_deadline"].startswith("2099-12-31T18:29:59")
    assert portal.put(f"/api/cycles/{cycle_id}/deadline", {"deadline": "31/12/2099"}).status_code == 400
    assert portal.put(f"/api/cycles/{cycle_id}/deadline", {"deadline": None}).json()["deadline_date"] is None


def test_future_deadline_allows_managers(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.put(f"/api/cycles/{cycle_id}/deadline", {"deadline": "2099-12-31"})
    portal.login(MANAGER_ONE)
    assert portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "P"}]).status_code == 200


def test_new_upload_replaces_old_version(uploaded, make_workbook) -> None:
    portal, old_id = uploaded
    new_id = portal.upload(make_workbook())
    assert portal.repo.get_cycle(new_id)["active"] and not portal.repo.get_cycle(old_id)["active"]
    portal.login(MANAGER_ONE)
    assert [c["id"] for c in portal.get("/api/my-cycles").json()["cycles"]] == [new_id]
    assert "replaced by a newer version" in portal.get(f"/api/cycles/{old_id}").json()["read_only_reason"]
    assert portal.save(old_id, [{"employee_id": "10000001", "date": DAY, "value": "P"}]).status_code == 403


def test_hr_can_reactivate_old_version(uploaded, make_workbook) -> None:
    portal, old_id = uploaded
    new_id = portal.upload(make_workbook())
    portal.post(f"/api/cycles/{old_id}/status", {"action": "activate"})
    assert portal.repo.get_cycle(old_id)["active"] and not portal.repo.get_cycle(new_id)["active"]


# ---------- history ----------

def test_change_history_and_csv(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "CO"}], comment="=HYPERLINK(evil)")
    [change] = portal.get(f"/api/cycles/{cycle_id}/changes").json()["changes"]
    assert (change["employee_name"], change["date"], change["old_value"], change["new_value"]) == \
        ("Fake Employee A", DAY, "A", "CO")
    csv_text = portal.get(f"/api/cycles/{cycle_id}/changes.csv").text
    assert "10000001" in csv_text and "21/09/2026" in csv_text
    assert "'=HYPERLINK(evil)" in csv_text  # protected against CSV formula injection
    assert portal.get(f"/api/cycles/{cycle_id}/changes.csv", employee="10000002").text.count("\n") == 1
    assert portal.get(f"/api/cycles/{cycle_id}/changes", changed_by="someone@else.com").json()["changes"] == []


def test_managers_list_counts_changes(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.save(cycle_id, [{"employee_id": "10000001", "date": DAY, "value": "CO"},
                           {"employee_id": "10000002", "date": DAY, "value": "CO"}])
    data = portal.get(f"/api/cycles/{cycle_id}/managers").json()
    by_email = {m["manager_email"]: m for m in data["managers"]}
    assert by_email[MANAGER_ONE]["changes"] == 2 and by_email[MANAGER_ONE]["team_size"] == 3
    assert by_email[MANAGER_TWO]["email_status"] is None
    assert data["departments"] == ["Dept M1", "Dept M2"]
    assert HR not in by_email
