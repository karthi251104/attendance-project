from collections.abc import Callable
from pathlib import Path

import mongomock
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.file_store import MemoryFileStore
from app.db.repositories import Repository
from app.email.console_sender import ConsoleSender
from app.main import create_app
from tests.fixtures.make_sample_workbook import make_sample_workbook

HR = "hr@example.com"
MANAGER_ONE = "manager.one@example.com"  # employees 10000001-10000003
MANAGER_TWO = "manager.two@example.com"  # employees 10000004-10000006
OUTSIDER = "someone.else@example.com"


@pytest.fixture
def make_workbook(tmp_path: Path) -> Callable[..., Path]:
    """Return a function that writes a fake workbook (with options) into a temp folder."""
    counter = iter(range(1000))

    def _make(**options) -> Path:
        return make_sample_workbook(tmp_path / f"workbook_{next(counter)}.xlsx", **options)

    return _make


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, dev_login=True, hr_admin_emails=HR, app_base_url="http://testserver")


class Portal:
    """A test client plus helpers to sign in and call the API like the React app does."""

    def __init__(self, app, repo: Repository, files: MemoryFileStore, sender: ConsoleSender) -> None:
        self.client = TestClient(app)
        self.repo, self.files, self.sender = repo, files, sender
        self.csrf = ""

    def session(self) -> dict:
        data = self.client.get("/api/session").json()
        self.csrf = data["csrf_token"]
        return data

    def login(self, email: str) -> None:
        self.session()
        response = self.client.post("/api/auth/dev-login", json={"email": email}, headers={"X-CSRF-Token": self.csrf})
        assert response.status_code == 200, response.text
        self.session()  # signing in starts a new session with a new token

    def upload_response(self, path: Path, deadline: str = "", filename: str | None = None):
        return self.client.post("/api/cycles", data={"deadline": deadline}, headers={"X-CSRF-Token": self.csrf},
                                files={"file": (filename or path.name, path.read_bytes(), "application/octet-stream")})

    def upload(self, path: Path, deadline: str = "", filename: str | None = None) -> str:
        response = self.upload_response(path, deadline, filename)
        assert response.status_code == 200, response.text
        return response.json()["id"]

    def post(self, url: str, json: dict | None = None):
        return self.client.post(url, json=json or {}, headers={"X-CSRF-Token": self.csrf})

    def put(self, url: str, json: dict):
        return self.client.put(url, json=json, headers={"X-CSRF-Token": self.csrf})

    def get(self, url: str, **params):
        return self.client.get(url, params=params)

    def save(self, cycle_id: str, changes: list[dict], comment: str | None = None):
        return self.post(f"/api/cycles/{cycle_id}/changes", {"changes": changes, "comment": comment})


@pytest.fixture
def portal_factory(settings: Settings):
    repo = Repository(mongomock.MongoClient(tz_aware=True).db)
    files = MemoryFileStore()
    sender = ConsoleSender()

    def _make(custom: Settings | None = None) -> Portal:
        app = create_app(custom or settings, repo=repo, files=files, email_sender=sender)
        return Portal(app, repo, files, sender)

    return _make


@pytest.fixture
def portal(portal_factory) -> Portal:
    return portal_factory()


@pytest.fixture
def uploaded(portal: Portal, make_workbook) -> tuple[Portal, str]:
    """HR has uploaded the usual workbook (SAP + HC_Teamlease); returns (portal logged in as HR, cycle_id)."""
    portal.login(HR)
    return portal, portal.upload(make_workbook())
