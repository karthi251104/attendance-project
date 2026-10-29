"""Login session and CSRF protection.

The session lives in a signed cookie (Starlette SessionMiddleware): HttpOnly,
SameSite=Lax, Secure in production. It holds only the user's email, name, login
time and CSRF token. Sessions end after SESSION_HOURS (default 8), counted from login.
"""

import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Request

from app.config import Settings
from app.core.timeutil import now_utc


@dataclass(frozen=True)
class CurrentUser:
    email: str  # lower-cased
    name: str
    is_hr: bool


class NotLoggedIn(Exception):
    pass


class Forbidden(Exception):
    def __init__(self, message: str = "You do not have access to this page.") -> None:
        super().__init__(message)
        self.message = message


class CsrfError(Exception):
    pass


def login_user(request: Request, email: str, name: str) -> None:
    request.session.clear()
    request.session.update({
        "email": email.strip().lower(),
        "name": name.strip() or email,
        "login_at": now_utc().isoformat(),
        "csrf": secrets.token_urlsafe(32),
    })


def logout_user(request: Request) -> None:
    request.session.clear()


def current_user(request: Request, settings: Settings) -> CurrentUser | None:
    email = request.session.get("email")
    login_at = request.session.get("login_at")
    if not email or not login_at:
        return None
    if now_utc() - datetime.fromisoformat(login_at) > timedelta(hours=settings.session_hours):
        request.session.clear()
        return None
    return CurrentUser(email=email, name=request.session.get("name", email),
                       is_hr=email in settings.hr_admin_email_list)


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def verify_csrf(request: Request) -> None:
    """Check the token from the X-CSRF-Token header (fetch calls) or the csrf_token form field."""
    expected = request.session.get("csrf")
    supplied = request.headers.get("x-csrf-token")
    if not supplied and request.headers.get("content-type", "").startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
        supplied = (await request.form()).get("csrf_token")
    if not expected or not supplied or not hmac.compare_digest(str(expected), str(supplied)):
        raise CsrfError()
