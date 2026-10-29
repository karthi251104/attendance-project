"""Shared helpers for routes: dependencies, templates and the app's services."""

from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates

from app.auth.session import CurrentUser, Forbidden, NotLoggedIn, csrf_token, current_user, verify_csrf
from app.config import Settings
from app.core.editing import role_for
from app.core.timeutil import fmt_date, fmt_datetime
from app.db.file_store import FileStore
from app.db.repositories import Repository
from app.email.base import EmailSender

TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass
class Services:
    settings: Settings
    repo: Repository
    files: FileStore
    email_sender: EmailSender


def build_templates(settings: Settings) -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals.update(
        fmt_date=fmt_date,
        fmt_datetime=lambda value: fmt_datetime(value, settings.timezone),
        settings=settings,
    )
    return templates


def services(request: Request) -> Services:
    return request.app.state.services


def templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def require_user(request: Request, svc: Services = Depends(services)) -> CurrentUser:
    user = current_user(request, svc.settings)
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
        raise Forbidden("This attendance cycle does not exist.")
    return cycle


def cycle_role(svc: Services, cycle: dict, user: CurrentUser) -> str:
    """'hr' or 'manager'; raises Forbidden when the user has no team in this cycle."""
    role = role_for(svc.repo, cycle["id"], user.email, user.is_hr)
    if role is None:
        raise Forbidden("You are not listed as a manager in this attendance cycle.")
    return role


def render(request: Request, name: str, context: dict, status_code: int = 200):
    user = current_user(request, request.app.state.services.settings)
    base = {"request": request, "user": user, "csrf_token": csrf_token(request) if user else "",
            "flashes": request.session.pop("flash", []) if user else []}
    return request.app.state.templates.TemplateResponse(request, name, {**base, **context}, status_code=status_code)
