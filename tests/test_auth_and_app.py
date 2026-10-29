"""Phase 6: login, sessions, startup checks, security headers and health check."""

from datetime import timedelta

import pytest

from app.auth import entra
from app.config import Settings
from app.core.timeutil import now_utc
from app.main import create_app
from app.routes.auth import safe_next
from tests.conftest import HR, MANAGER_ONE

PROD = dict(env="production", secret_key="x" * 40, entra_tenant_id="tid", entra_client_id="cid",
            entra_client_secret="secret", app_base_url="https://attendance.example.com", dev_login=False)


def test_production_refuses_dev_login() -> None:
    with pytest.raises(RuntimeError, match="DEV_LOGIN"):
        create_app(Settings(_env_file=None, **{**PROD, "dev_login": True}))


@pytest.mark.parametrize("override, problem", [
    ({"secret_key": "change-me"}, "SECRET_KEY"),
    ({"mongodb_uri": "mongomock://"}, "mongomock"),
    ({"entra_client_secret": ""}, "ENTRA"),
    ({"app_base_url": "http://attendance.example.com"}, "https"),
])
def test_production_config_checks(override, problem) -> None:
    problems = Settings(_env_file=None, **{**PROD, **override}).startup_problems()
    assert any(problem in p for p in problems)


def test_valid_production_config_has_no_problems() -> None:
    assert Settings(_env_file=None, **PROD).startup_problems() == []


def test_graph_mode_needs_sender_mailbox() -> None:
    assert Settings(_env_file=None, email_mode="graph").startup_problems()


def test_dev_login_disabled_means_entra_redirect(portal_factory, settings, monkeypatch) -> None:
    monkeypatch.setattr(entra, "start_login", lambda s: {"auth_uri": "https://login.microsoftonline.com/x/authorize", "state": "s"})
    portal = portal_factory(settings.model_copy(update={"dev_login": False}))
    response = portal.client.get("/login", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("https://login.microsoftonline.com/")
    forced = portal.client.post("/login/dev", data={"email": HR, "csrf_token": "x"})
    assert forced.status_code == 403


def test_entra_callback_logs_user_in(portal_factory, settings, monkeypatch) -> None:
    monkeypatch.setattr(entra, "start_login", lambda s: {"auth_uri": "https://login.microsoftonline.com/x", "state": "s"})
    monkeypatch.setattr(entra, "finish_login", lambda s, flow, params: (HR, "HR Person"))
    portal = portal_factory(settings.model_copy(update={"dev_login": False}))
    portal.client.get("/login?next=/hr/upload", follow_redirects=False)
    response = portal.client.get("/auth/callback?code=abc&state=s", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/hr/upload"
    assert portal.client.get("/hr").status_code == 200


def test_entra_callback_failure(portal_factory, settings, monkeypatch) -> None:
    def fail(*args):
        raise entra.LoginFailed("Nope")

    monkeypatch.setattr(entra, "start_login", lambda s: {"auth_uri": "https://login.microsoftonline.com/x", "state": "s"})
    monkeypatch.setattr(entra, "finish_login", fail)
    portal = portal_factory(settings.model_copy(update={"dev_login": False}))
    portal.client.get("/login", follow_redirects=False)
    assert portal.client.get("/auth/callback?error=x").status_code == 400
    assert portal.client.get("/hr", follow_redirects=False).status_code == 303  # still not logged in


def test_finish_login_reads_claims(monkeypatch) -> None:
    class FakeClient:
        def acquire_token_by_auth_code_flow(self, flow, params):
            return {"id_token_claims": {"preferred_username": "Manager.One@Example.com", "name": "M One", "tid": "tid"}}

    monkeypatch.setattr(entra, "_client", lambda settings: FakeClient())
    assert entra.finish_login(Settings(_env_file=None, entra_tenant_id="tid"), {}, {}) == ("manager.one@example.com", "M One")


def test_finish_login_rejects_other_tenant(monkeypatch) -> None:
    class FakeClient:
        def acquire_token_by_auth_code_flow(self, flow, params):
            return {"id_token_claims": {"preferred_username": "x@other.com", "tid": "other"}}

    monkeypatch.setattr(entra, "_client", lambda settings: FakeClient())
    with pytest.raises(entra.LoginFailed):
        entra.finish_login(Settings(_env_file=None, entra_tenant_id="tid"), {}, {})


def test_session_expires_after_8_hours(portal, monkeypatch) -> None:
    portal.login(HR)
    assert portal.client.get("/hr").status_code == 200
    later = now_utc() + timedelta(hours=8, minutes=1)
    monkeypatch.setattr("app.auth.session.now_utc", lambda: later)
    assert portal.client.get("/hr", follow_redirects=False).status_code == 303


def test_logout(portal) -> None:
    portal.login(MANAGER_ONE)
    portal.post("/logout")
    assert portal.client.get("/", follow_redirects=False).headers["location"].startswith("/login")


@pytest.mark.parametrize("target, expected", [
    ("/hr", "/hr"), ("//evil.com", "/"), ("https://evil.com", "/"), (None, "/"), ("hr", "/"),
])
def test_safe_next(target, expected) -> None:
    assert safe_next(target) == expected


def test_security_headers_and_cookie(portal) -> None:
    response = portal.client.get("/login")
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    portal.login(HR)
    cookie = portal.client.post("/login/dev", data={"email": HR, "csrf_token": portal.csrf}, follow_redirects=False).headers["set-cookie"]
    assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()


def test_healthz(portal) -> None:
    response = portal.client.get("/healthz")
    assert response.status_code == 200 and response.json() == {"status": "ok", "database": True}


def test_manager_home_with_no_team(portal) -> None:
    portal.login("nobody@example.com")
    assert "no attendance for you to review" in portal.client.get("/").text
