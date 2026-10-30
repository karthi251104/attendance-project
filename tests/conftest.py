import re
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
    """A test client plus helpers to log in and call the app like a browser."""

    def __init__(self, app, repo: Repository, files: MemoryFileStore, sender: ConsoleSender) -> None:
        self.client = TestClient(app)
        self.repo, self.files, self.sender = repo, files, sender
        self.csrf = ""

    def _page_csrf(self) -> str:
        page = self.client.get("/login")  # renders the session's CSRF token
        return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

    def login(self, email: str) -> None:
        response = self.client.post("/login/dev", data={"email": email, "csrf_token": self._page_csrf(), "next": "/"},
                                    follow_redirects=False)
        assert response.status_code == 303
        self.csrf = self._page_csrf()  # login creates a new session with a new token

    def upload(self, path: Path, deadline: str = "", filename: str | None = None) -> str:
        response = self.client.post("/hr/upload", data={"csrf_token": self.csrf, "deadline": deadline},
                                    files={"file": (filename or path.name, path.read_bytes(), "application/octet-stream")},
                                    follow_redirects=False)
        assert response.status_code == 303, response.text
        return response.headers["location"].rsplit("/", 1)[1]

    def post(self, url: str, **data):
        return self.client.post(url, data={"csrf_token": self.csrf, **data}, follow_redirects=False)

    def save(self, cycle_id: str, changes: list[dict], comment: str | None = None):
        return self.client.post(f"/api/cycles/{cycle_id}/changes", json={"changes": changes, "comment": comment},
                                headers={"X-CSRF-Token": self.csrf})


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
