"""HR manages portal logins (AUTH_MODE=password): list, create missing, reset a password, disable/enable.

Generated passwords are returned once, in the response, for HR to pass on. They are never stored readable.
"""

from dataclasses import asdict

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth.accounts import AccountError, create_missing_logins, known_people, reset_password, set_active
from app.auth.session import CurrentUser, Forbidden
from app.views import iso
from app.web import Services, csrf_protect, require_hr, services

router = APIRouter(prefix="/api/accounts")


def password_mode(svc: Services = Depends(services)) -> None:
    if svc.settings.auth_mode != "password":
        raise Forbidden("Logins are managed by Microsoft sign-in (AUTH_MODE=entra).")


@router.get("", dependencies=[Depends(password_mode)])
def list_logins(user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    people = known_people(svc.repo, svc.settings)
    return {"people": [{**p, "last_login_at": iso(p["last_login_at"]), "password_changed_at": iso(p["password_changed_at"])}
                       for p in people]}


@router.post("/create-missing", dependencies=[Depends(csrf_protect), Depends(password_mode)])
def create_missing(user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    return {"created": [asdict(login) for login in create_missing_logins(svc.repo, svc.settings, user.email)]}


class EmailIn(BaseModel):
    email: str = Field(max_length=200)


@router.post("/reset", dependencies=[Depends(csrf_protect), Depends(password_mode)])
def reset(body: EmailIn, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    try:
        return asdict(reset_password(svc.repo, body.email, svc.settings, user.email))
    except AccountError as exc:
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)


class ActiveIn(BaseModel):
    email: str = Field(max_length=200)
    active: bool


@router.post("/active", dependencies=[Depends(csrf_protect), Depends(password_mode)])
def active(body: ActiveIn, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    if body.email.strip().lower() == user.email and not body.active:
        return JSONResponse({"error": "You cannot disable your own login."}, status_code=400)
    try:
        set_active(svc.repo, body.email, body.active)
    except AccountError as exc:
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)
    return {"ok": True}
