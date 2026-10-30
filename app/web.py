"""Shared helpers for routes: dependencies and the app's services."""

from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, Request

from app.auth.accounts import session_still_valid
from app.auth.session import CurrentUser, Forbidden, NotLoggedIn, current_user, logout_user, verify_csrf
from app.config import Settings
from app.core.editing import role_for
from app.db.file_store import FileStore
from app.db.repositories import Repository
from app.email.base import EmailSender


@dataclass
class Services:
    settings: Settings
    repo: Repository
    files: FileStore
    email_sender: EmailSender


def services(request: Request) -> Services:
    return request.app.state.services


def signed_in_user(request: Request, svc: Services) -> CurrentUser | None:
    user = current_user(request, svc.settings)
    if user is not None and svc.settings.sign_in_mode == "password":
        # A disabled login or a new password ends this session straight away.
        login_at = datetime.fromisoformat(request.session["login_at"])
        if not session_still_valid(svc.repo, user.email, login_at):
            logout_user(request)
            return None
    return user


def require_user(request: Request, svc: Services = Depends(services)) -> CurrentUser:
    user = signed_in_user(request, svc)
    if user is None:
        raise NotLoggedIn()
    return user


def require_hr(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if not user.is_hr:
        raise Forbidden("This page is for HR only.")
    return user


async def csrf_protect(request: Request) -> None:
    await verify_csrf(request)


def load_cycle(svc: Services, cycle_id: str) -> dict:
    cycle = svc.repo.get_cycle(cycle_id)
    if cycle is None:
        # Same answer as "no access", so cycle ids cannot be probed.
        raise Forbidden("This attendance cycle does not exist or you do not have access to it.")
    return cycle


def cycle_role(svc: Services, cycle: dict, user: CurrentUser) -> str:
    """'hr' or 'manager'; raises Forbidden when the user has no team in this cycle."""
    role = role_for(svc.repo, cycle["id"], user.email, user.is_hr)
    if role is None:
        raise Forbidden("This attendance cycle does not exist or you do not have access to it.")
    return role
