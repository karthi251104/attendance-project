"""AUTH_MODE=password: HR gives IDs and passwords, people sign in and can change their own password."""

from datetime import timedelta

import pytest

from app.auth.passwords import generate_password, hash_password, verify_password
from app.core.timeutil import now_utc
from tests.conftest import HR, MANAGER_ONE, MANAGER_TWO

HR_PASSWORD = "first-hr-password"


@pytest.fixture
def pw_portal(portal_factory, settings):
    """A portal in password mode (no dev login) where HR has the initial password."""
    return portal_factory(settings.model_copy(update={"dev_login": False, "auth_mode": "password",
                                                      "initial_hr_password": HR_PASSWORD}))


def sign_in(portal, email: str, password: str):
    portal.session()
    response = portal.post("/api/auth/login", {"email": email, "password": password})
    portal.session()
    return response


@pytest.fixture
def with_cycle(pw_portal, make_workbook):
    """HR signed in with a cycle uploaded, and logins created for everyone; returns (portal, {email: password})."""
    assert sign_in(pw_portal, HR, HR_PASSWORD).status_code == 200
    pw_portal.upload(make_workbook())
    created = pw_portal.post("/api/accounts/create-missing").json()["created"]
    return pw_portal, {c["email"]: c["password"] for c in created}


# ---------- passwords ----------

def test_hash_and_verify() -> None:
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$") and "correct horse" not in stored
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("anything", None)
    assert hash_password("same") != hash_password("same")  # salted


def test_generated_passwords_are_readable_and_random() -> None:
    passwords = {generate_password() for _ in range(50)}
    assert len(passwords) == 50
    sample = passwords.pop()
    assert len(sample) == 14 and sample.count("-") == 2
    assert not set(sample) & set("0O1lI")


# ---------- signing in ----------

def test_session_reports_password_mode(pw_portal) -> None:
    assert pw_portal.session()["auth_mode"] == "password"


def test_hr_signs_in_with_the_initial_password(pw_portal) -> None:
    assert sign_in(pw_portal, "HR@Example.com", HR_PASSWORD).status_code == 200  # email is case-insensitive
    assert pw_portal.session()["user"]["is_hr"] is True


def test_wrong_password_and_unknown_email_get_the_same_answer(pw_portal) -> None:
    wrong = sign_in(pw_portal, HR, "nope-nope")
    unknown = sign_in(pw_portal, "nobody@example.com", "nope-nope")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"error": "The email or password is wrong."}
    assert pw_portal.session()["user"] is None


def test_email_without_password_is_refused(pw_portal) -> None:
    assert sign_in(pw_portal, HR, "").status_code == 401


def test_five_wrong_passwords_lock_the_login(pw_portal) -> None:
    for _ in range(5):
        assert sign_in(pw_portal, HR, "wrong-password").status_code == 401
    locked = sign_in(pw_portal, HR, HR_PASSWORD)  # even the right password
    assert locked.status_code == 429 and "Try again in" in locked.json()["error"]
    pw_portal.repo.set_account_fields(HR, {"locked_until": now_utc() - timedelta(seconds=1)})
    assert sign_in(pw_portal, HR, HR_PASSWORD).status_code == 200


def test_dev_login_is_not_possible_in_password_mode(pw_portal) -> None:
    assert sign_in(pw_portal, HR, "").status_code == 401  # an email alone never signs in


# ---------- HR gives logins ----------

def test_hr_creates_logins_for_every_manager(with_cycle) -> None:
    portal, created = with_cycle
    assert set(created) == {MANAGER_ONE, MANAGER_TWO}
    people = {p["email"]: p for p in portal.get("/api/accounts").json()["people"]}
    assert people[HR]["role"] == "hr" and people[MANAGER_ONE]["role"] == "manager"
    assert all(p["has_login"] and p["active"] for p in people.values())
    assert "password" not in str(portal.get("/api/accounts").json()).replace("password_changed_at", "")
    # Running it again creates nothing new.
    assert portal.post("/api/accounts/create-missing").json() == {"created": []}


def test_manager_signs_in_and_sees_only_the_team(with_cycle) -> None:
    portal, created = with_cycle
    assert sign_in(portal, MANAGER_ONE, created[MANAGER_ONE]).status_code == 200
    cycle_id = portal.get("/api/my-cycles").json()["cycles"][0]["id"]
    ids = {e["employee_id"] for e in portal.get(f"/api/cycles/{cycle_id}/employees").json()["employees"]}
    assert ids == {"10000001", "10000002", "10000003"}
    assert portal.get("/api/accounts").status_code == 403  # managers cannot manage logins


def test_manager_changes_own_password(with_cycle) -> None:
    portal, created = with_cycle
    old = created[MANAGER_ONE]
    sign_in(portal, MANAGER_ONE, old)
    assert portal.post("/api/auth/change-password", {"current_password": "wrong", "new_password": "new-secret-1"}).status_code == 400
    assert portal.post("/api/auth/change-password", {"current_password": old, "new_password": "short"}).status_code == 400
    response = portal.post("/api/auth/change-password", {"current_password": old, "new_password": "new-secret-1"})
    assert response.status_code == 200
    assert portal.session()["user"]["email"] == MANAGER_ONE  # still signed in on this device
    assert sign_in(portal, MANAGER_ONE, old).status_code == 401
    assert sign_in(portal, MANAGER_ONE, "new-secret-1").status_code == 200


def test_hr_reset_gives_a_new_password_and_signs_the_person_out(with_cycle, portal_factory, settings) -> None:
    portal, created = with_cycle
    manager = portal_factory(portal.client.app.state.services.settings)  # a second browser, same database
    assert sign_in(manager, MANAGER_TWO, created[MANAGER_TWO]).status_code == 200

    new = portal.post("/api/accounts/reset", {"email": MANAGER_TWO}).json()
    assert new["email"] == MANAGER_TWO and new["password"] != created[MANAGER_TWO]
    assert manager.session()["user"] is None  # the old session ended
    assert sign_in(manager, MANAGER_TWO, created[MANAGER_TWO]).status_code == 401
    assert sign_in(manager, MANAGER_TWO, new["password"]).status_code == 200


def test_reset_unlocks_a_locked_login(with_cycle) -> None:
    portal, created = with_cycle
    portal.repo.set_account_fields(MANAGER_ONE, {"locked_until": now_utc() + timedelta(minutes=10)})
    new = portal.post("/api/accounts/reset", {"email": MANAGER_ONE}).json()["password"]
    assert sign_in(portal, MANAGER_ONE, new).status_code == 200


def test_reset_of_unknown_person(with_cycle) -> None:
    portal, _ = with_cycle
    assert portal.post("/api/accounts/reset", {"email": "stranger@example.com"}).status_code == 404


def test_disabled_login_cannot_sign_in_and_is_signed_out(with_cycle, portal_factory) -> None:
    portal, created = with_cycle
    manager = portal_factory(portal.client.app.state.services.settings)
    sign_in(manager, MANAGER_ONE, created[MANAGER_ONE])
    assert portal.post("/api/accounts/active", {"email": MANAGER_ONE, "active": False}).json() == {"ok": True}
    assert manager.get("/api/my-cycles").status_code == 401
    disabled = sign_in(manager, MANAGER_ONE, created[MANAGER_ONE])
    assert disabled.status_code == 403 and "disabled" in disabled.json()["error"]
    portal.post("/api/accounts/active", {"email": MANAGER_ONE, "active": True})
    assert sign_in(manager, MANAGER_ONE, created[MANAGER_ONE]).status_code == 200


def test_hr_cannot_disable_own_login(with_cycle) -> None:
    portal, _ = with_cycle
    assert portal.post("/api/accounts/active", {"email": HR, "active": False}).status_code == 400


def test_logins_need_csrf(with_cycle) -> None:
    portal, _ = with_cycle
    assert portal.client.post("/api/accounts/create-missing").status_code == 403
    assert portal.client.post("/api/auth/login", json={"email": HR, "password": HR_PASSWORD}).status_code == 403


def test_logins_page_is_off_in_entra_mode(portal_factory, settings) -> None:
    portal = portal_factory(settings.model_copy(update={"auth_mode": "entra"}))
    portal.login(HR)  # dev login still works in development
    assert portal.get("/api/accounts").status_code == 403
