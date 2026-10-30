"""Sign-in and sign-out: Microsoft Entra ID, or an email-only form when DEV_LOGIN=true.

The React app asks GET /api/session who is signed in. Entra sign-in is a browser
redirect (/auth/login -> Microsoft -> /auth/callback -> back into the app).
"""

import logging
from urllib.parse import quote, urlencode, urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.auth import entra
from app.auth.session import Forbidden, csrf_token, current_user, login_user, logout_user
from app.web import Services, csrf_protect, services

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
    user = current_user(request, svc.settings)
    return {
        "user": {"email": user.email, "name": user.name, "is_hr": user.is_hr} if user else None,
        "csrf_token": csrf_token(request),
        "auth_mode": "dev" if svc.settings.dev_login else "entra",
        "statuses": svc.settings.allowed_status_list,
        "timezone": svc.settings.timezone,
        "max_upload_mb": svc.settings.max_upload_mb,
    }


class DevLoginIn(BaseModel):
    email: str = Field(max_length=200)


@router.post("/api/auth/dev-login", dependencies=[Depends(csrf_protect)])
def dev_login(request: Request, body: DevLoginIn, svc: Services = Depends(services)):
    if not svc.settings.dev_login:
        raise Forbidden("Development login is disabled.")
    email = body.email.strip().lower()
    if "@" not in email:
        return JSONResponse({"error": "Enter an email address."}, status_code=400)
    login_user(request, email, email.split("@")[0])
    log.info("Dev login")
    return {"ok": True}


@router.get("/auth/login")
def login(request: Request, next: str | None = None, svc: Services = Depends(services)):
    next_url = safe_next(next)
    if svc.settings.dev_login:
        return RedirectResponse(f"/login?{urlencode({'next': next_url})}", status_code=303)
    flow = entra.start_login(svc.settings)
    request.session["auth_flow"] = flow
    request.session["next"] = next_url
    return RedirectResponse(flow["auth_uri"], status_code=303)


@router.get("/auth/callback")
def auth_callback(request: Request, svc: Services = Depends(services)):
    flow = request.session.pop("auth_flow", None)
    next_url = safe_next(request.session.pop("next", "/"))
    if not flow:
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
    if svc.settings.dev_login or not svc.settings.entra_tenant_id:
        return {"redirect": "/login"}
    after = f"{svc.settings.app_base_url.rstrip('/')}/login"
    return {"redirect": f"https://login.microsoftonline.com/{svc.settings.entra_tenant_id}/oauth2/v2.0/logout"
                        f"?post_logout_redirect_uri={quote(after, safe='')}"}
