"""Sign-in, sessions, startup checks, security headers, the health check and the SPA fallback."""

from datetime import timedelta

import pytest

from app.auth import entra
from app.config import Settings
from app.core.timeutil import now_utc
from app.main import create_app
from app.routes.auth import safe_next
from tests.conftest import HR, MANAGER_ONE

PROD = dict(env="production", secret_key="x" * 40, entra_tenant_id="tid", entra_client_id="cid",
            entra_client_secret="secret", app_base_url="https://attendance.example.com", dev_login=False,
            auth_mode="entra")


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


def test_password_mode_needs_no_entra_settings() -> None:
    settings = Settings(_env_file=None, env="production", secret_key="x" * 40, app_base_url="https://a.example.com")
    assert settings.auth_mode == "password" and settings.startup_problems() == []
    assert Settings(_env_file=None, initial_hr_password="short").startup_problems()


def test_graph_mode_needs_sender_mailbox() -> None:
    assert Settings(_env_file=None, email_mode="graph").startup_problems()


def test_session_before_and_after_login(portal) -> None:
    anonymous = portal.session()
    assert anonymous["user"] is None and anonymous["auth_mode"] == "dev" and anonymous["csrf_token"]
    assert anonymous["statuses"] == ["P", "A", "OFF", "PH", "CO", "ML", "PL"]
    portal.login("Manager.One@Example.com")
    assert portal.session()["user"] == {"email": MANAGER_ONE, "name": "manager.one", "is_hr": False}
    portal.login(HR)
    assert portal.session()["user"]["is_hr"] is True


def test_dev_login_needs_csrf_and_an_email(portal) -> None:
    portal.session()
    assert portal.client.post("/api/auth/login", json={"email": HR}).status_code == 403
    assert portal.post("/api/auth/login", {"email": "nope"}).status_code == 400


def test_entra_mode_redirects_and_refuses_dev_login(portal_factory, settings, monkeypatch) -> None:
    monkeypatch.setattr(entra, "start_login", lambda s: {"auth_uri": "https://login.microsoftonline.com/x/authorize", "state": "s"})
    portal = portal_factory(settings.model_copy(update={"dev_login": False, "auth_mode": "entra"}))
    assert portal.session()["auth_mode"] == "entra"
    response = portal.client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("https://login.microsoftonline.com/")
    assert portal.post("/api/auth/login", {"email": HR}).status_code == 403


def test_dev_mode_login_link_goes_to_the_app_form(portal) -> None:
    response = portal.client.get("/auth/login?next=/hr", follow_redirects=False)
    assert response.headers["location"] == "/login?next=%2Fhr"


def test_entra_callback_logs_user_in(portal_factory, settings, monkeypatch) -> None:
    monkeypatch.setattr(entra, "start_login", lambda s: {"auth_uri": "https://login.microsoftonline.com/x", "state": "s"})
    monkeypatch.setattr(entra, "finish_login", lambda s, flow, params: (HR, "HR Person"))
    portal = portal_factory(settings.model_copy(update={"dev_login": False, "auth_mode": "entra"}))
    portal.client.get("/auth/login?next=/hr/upload", follow_redirects=False)
    response = portal.client.get("/auth/callback?code=abc&state=s", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/hr/upload"
    assert portal.session()["user"] == {"email": HR, "name": "HR Person", "is_hr": True}
    assert portal.get("/api/cycles").status_code == 200


def test_entra_callback_failure(portal_factory, settings, monkeypatch) -> None:
    def fail(*args):
        raise entra.LoginFailed("Nope")

    monkeypatch.setattr(entra, "start_login", lambda s: {"auth_uri": "https://login.microsoftonline.com/x", "state": "s"})
    monkeypatch.setattr(entra, "finish_login", fail)
    portal = portal_factory(settings.model_copy(update={"dev_login": False, "auth_mode": "entra"}))
    portal.client.get("/auth/login", follow_redirects=False)
    response = portal.client.get("/auth/callback?error=x", follow_redirects=False)
    assert response.headers["location"] == "/login?error=Nope"
    assert portal.session()["user"] is None


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
    assert portal.get("/api/cycles").status_code == 200
    later = now_utc() + timedelta(hours=8, minutes=1)
    monkeypatch.setattr("app.auth.session.now_utc", lambda: later)
    response = portal.get("/api/cycles")
    assert response.status_code == 401 and "sign in again" in response.json()["error"]


def test_logout(portal) -> None:
    portal.login(MANAGER_ONE)
    assert portal.post("/api/auth/logout").json() == {"redirect": "/login"}
    assert portal.session()["user"] is None
    assert portal.get("/api/my-cycles").status_code == 401


def test_logout_with_entra_goes_to_microsoft(portal_factory, settings) -> None:
    portal = portal_factory(settings.model_copy(update={"dev_login": False, "auth_mode": "entra", "entra_tenant_id": "tid"}))
    portal.session()
    redirect = portal.post("/api/auth/logout").json()["redirect"]
    assert redirect.startswith("https://login.microsoftonline.com/tid/oauth2/v2.0/logout?post_logout_redirect_uri=")


@pytest.mark.parametrize("target, expected", [
    ("/hr", "/hr"), ("//evil.com", "/"), ("https://evil.com", "/"), (None, "/"), ("hr", "/"),
])
def test_safe_next(target, expected) -> None:
    assert safe_next(target) == expected


def test_security_headers_and_cookie(portal) -> None:
    response = portal.client.get("/api/session")
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_healthz(portal) -> None:
    response = portal.client.get("/healthz")
    assert response.status_code == 200 and response.json() == {"status": "ok", "database": True}


def test_manager_with_no_team_has_no_cycles(portal) -> None:
    portal.login("nobody@example.com")
    assert portal.get("/api/my-cycles").json() == {"cycles": []}


def test_spa_fallback_serves_index(portal_factory, settings, tmp_path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-123.js").write_text("console.log(1)")
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    portal = portal_factory(settings.model_copy(update={"frontend_dist": str(tmp_path)}))
    for path in ("/", "/hr", "/team/abc", "/login?next=/hr"):
        response = portal.client.get(path)
        assert response.status_code == 200 and "id=root" in response.text, path
    asset = portal.client.get("/assets/app-123.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert portal.client.get("/favicon.svg").text == "<svg/>"
    assert portal.client.get("/api/nothing-here").status_code in (401, 404)
    assert portal.client.get("/..%2F..%2Fapp%2Fconfig.py").text.startswith("<!doctype html>")


def test_unbuilt_frontend_shows_a_hint(portal_factory, settings, tmp_path) -> None:
    portal = portal_factory(settings.model_copy(update={"frontend_dist": str(tmp_path / "missing")}))
    assert "npm run build" in portal.client.get("/").text


def test_app_base_url_comes_from_render_when_not_set(monkeypatch) -> None:
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://attendance-portal.onrender.com")
    assert Settings(_env_file=None).app_base_url == "https://attendance-portal.onrender.com"
    assert Settings(_env_file=None, app_base_url="https://attendance.company.com").app_base_url == "https://attendance.company.com"
    monkeypatch.delenv("RENDER_EXTERNAL_URL")
    assert Settings(_env_file=None).app_base_url == "http://localhost:8000"

