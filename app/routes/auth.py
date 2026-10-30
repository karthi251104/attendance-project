"""Sign-in and sign-out.

Three modes (Settings.sign_in_mode):
  password  email + password given by HR (AUTH_MODE=password, the default)
  entra     Microsoft sign-in: /auth/login -> Microsoft -> /auth/callback (AUTH_MODE=entra)
  dev       any email, no password (DEV_LOGIN=true, development only)
The React app asks GET /api/session who is signed in.
"""

import logging
from urllib.parse import quote, urlencode, urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.auth import entra
from app.auth.accounts import AccountError, change_own_password, check_login
from app.auth.session import CurrentUser, Forbidden, csrf_token, login_user, logout_user
from app.web import Services, csrf_protect, require_user, services, signed_in_user

log = logging.getLogger(__name__)
router = APIRouter()


def safe_next(target: str | None) -> str:
    """Only allow redirects to paths on this site (no open redirect)."""
    if not target or not target.startswith("/") or target.startswith("//") or urlparse(target).netloc:
        return "/"
    return target


@router.get("/api/session")
def session(request: Request, svc: Services = Depends(services)):
    """Who is signed in, plus the CSRF token the app sends back on every change."""
    user = signed_in_user(request, svc)
    return {
        "user": {"email": user.email, "name": user.name, "is_hr": user.is_hr} if user else None,
        "csrf_token": csrf_token(request),
        "auth_mode": svc.settings.sign_in_mode,
        "statuses": svc.settings.allowed_status_list,
        "timezone": svc.settings.timezone,
        "max_upload_mb": svc.settings.max_upload_mb,
    }


class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(default="", max_length=200)


@router.post("/api/auth/login", dependencies=[Depends(csrf_protect)])
def password_login(request: Request, body: LoginIn, svc: Services = Depends(services)):
    """Email + password (AUTH_MODE=password), or email only when DEV_LOGIN=true."""
    mode = svc.settings.sign_in_mode
    email = body.email.strip().lower()
    if "@" not in email:
        return JSONResponse({"error": "Enter your work email."}, status_code=400)
    if mode == "dev":
        login_user(request, email, email.split("@")[0])
        log.info("Dev login")
        return {"ok": True}
    if mode != "password":
        raise Forbidden("Sign in with Microsoft.")
    try:
        account = check_login(svc.repo, email, body.password)
    except AccountError as exc:
        log.info("Password login failed")
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)
    login_user(request, email, account.get("name") or email.split("@")[0])
    log.info("Password login")
    return {"ok": True}


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


@router.post("/api/auth/change-password", dependencies=[Depends(csrf_protect)])
def change_password(request: Request, body: ChangePasswordIn, user: CurrentUser = Depends(require_user),
                    svc: Services = Depends(services)):
    if svc.settings.sign_in_mode != "password":
        raise Forbidden("Passwords are managed by Microsoft sign-in.")
    try:
        change_own_password(svc.repo, user.email, body.current_password, body.new_password)
    except AccountError as exc:
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)
    login_user(request, user.email, user.name)  # a fresh session; other devices are signed out
    return {"ok": True}


@router.get("/auth/login")
def login(request: Request, next: str | None = None, svc: Services = Depends(services)):
    next_url = safe_next(next)
    if svc.settings.sign_in_mode != "entra":
        return RedirectResponse(f"/login?{urlencode({'next': next_url})}", status_code=303)
    flow = entra.start_login(svc.settings)
    request.session["auth_flow"] = flow
    request.session["next"] = next_url
    return RedirectResponse(flow["auth_uri"], status_code=303)


@router.get("/auth/callback")
def auth_callback(request: Request, svc: Services = Depends(services)):
    flow = request.session.pop("auth_flow", None)
    next_url = safe_next(request.session.pop("next", "/"))
    if not flow or svc.settings.sign_in_mode != "entra":
        return RedirectResponse("/login", status_code=303)
    try:
        email, name = entra.finish_login(svc.settings, flow, dict(request.query_params))
    except entra.LoginFailed as exc:
        log.warning("Entra login failed")
        return RedirectResponse(f"/login?{urlencode({'error': str(exc)})}", status_code=303)
    login_user(request, email, name)
    log.info("Entra login succeeded")
    return RedirectResponse(next_url, status_code=303)


@router.post("/api/auth/logout", dependencies=[Depends(csrf_protect)])
def logout(request: Request, svc: Services = Depends(services)):
    """Clear the session; the app then goes to `redirect` (Microsoft's sign-out page with Entra)."""
    logout_user(request)
    if svc.settings.sign_in_mode != "entra" or not svc.settings.entra_tenant_id:
        return {"redirect": "/login"}
    after = f"{svc.settings.app_base_url.rstrip('/')}/login"
    return {"redirect": f"https://login.microsoftonline.com/{svc.settings.entra_tenant_id}/oauth2/v2.0/logout"
                        f"?post_logout_redirect_uri={quote(after, safe='')}"}
