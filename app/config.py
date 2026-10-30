"""Application settings, loaded from environment variables (and an optional .env file).

Every setting has a default that is safe for local development. See .env.example
for the full list. Comma-separated lists are stored as plain strings and exposed
as Python lists through small helper properties, which keeps the .env format simple.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: str) -> list[str]:
    """Split "a, b,c" into ["a", "b", "c"], dropping empty items."""
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["development", "production"] = "development"
    app_base_url: str = "http://localhost:8000"
    secret_key: str = "change-me"
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "attendance"

    # password: HR gives each person an ID (their email) and a password. entra: Microsoft sign-in.
    auth_mode: Literal["password", "entra"] = "password"
    dev_login: bool = False  # development only: any email, no password (overrides AUTH_MODE)
    hr_admin_emails: str = ""
    # AUTH_MODE=password: the first password of each HR email that has no account yet (HR changes it after).
    initial_hr_password: str = ""

    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""

    email_mode: Literal["console", "graph"] = "console"
    sender_mailbox: str = ""
    email_cc: str = ""

    # Calculation thresholds (hours). Defaults match the existing Excel macro.
    workday_min_hours: float = 5
    offday_min_hours: float = 8
    ph_min_hours: float = 5

    allowed_statuses: str = "P,A,OFF,PH,CO,ML,PL"
    timezone: str = "Asia/Kolkata"
    max_upload_mb: int = 25

    session_hours: int = 8
    email_per_second: float = 2

    # Folder with the built React app (index.html + assets). Empty = frontend/dist in the repo.
    frontend_dist: str = ""

    @property
    def frontend_dist_path(self) -> Path:
        return Path(self.frontend_dist) if self.frontend_dist else Path(__file__).resolve().parent.parent / "frontend" / "dist"

    @property
    def allowed_status_list(self) -> list[str]:
        return [code.upper() for code in _split_csv(self.allowed_statuses)]

    @property
    def hr_admin_email_list(self) -> list[str]:
        return [email.lower() for email in _split_csv(self.hr_admin_emails)]

    @property
    def email_cc_list(self) -> list[str]:
        return _split_csv(self.email_cc)

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def startup_problems(self) -> list[str]:
        """Return configuration mistakes that must stop the app from starting."""
        problems: list[str] = []
        if self.is_production:
            if self.dev_login:
                problems.append("DEV_LOGIN must not be true when ENV=production.")
            if self.secret_key in ("", "change-me") or len(self.secret_key) < 32:
                problems.append("SECRET_KEY must be a random value of at least 32 characters in production.")
            if self.mongodb_uri.startswith("mongomock://"):
                problems.append("MONGODB_URI=mongomock:// is for local testing only.")
            if self.auth_mode == "entra" and not (self.entra_tenant_id and self.entra_client_id and self.entra_client_secret):
                problems.append("ENTRA_TENANT_ID, ENTRA_CLIENT_ID and ENTRA_CLIENT_SECRET are required when AUTH_MODE=entra.")
            if not self.app_base_url.startswith("https://"):
                problems.append("APP_BASE_URL must start with https:// in production.")
        if self.email_mode == "graph" and not self.sender_mailbox:
            problems.append("SENDER_MAILBOX is required when EMAIL_MODE=graph.")
        if self.email_mode == "graph" and not (self.entra_tenant_id and self.entra_client_id and self.entra_client_secret):
            problems.append("ENTRA_TENANT_ID, ENTRA_CLIENT_ID and ENTRA_CLIENT_SECRET are required when EMAIL_MODE=graph.")
        if not self.allowed_status_list:
            problems.append("ALLOWED_STATUSES must not be empty.")
        if self.initial_hr_password and len(self.initial_hr_password) < 8:
            problems.append("INITIAL_HR_PASSWORD must have at least 8 characters.")
        return problems

    @property
    def sign_in_mode(self) -> str:
        """What the sign-in page shows: dev (email only), password or entra."""
        return "dev" if self.dev_login else self.auth_mode


@lru_cache
def get_settings() -> Settings:
    """Return the settings object (created once per process)."""
    return Settings()
