"""Login and logout: Microsoft Entra ID, or a simple email form when DEV_LOGIN=true."""

import logging
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import entra
from app.auth.session import Forbidden, csrf_token, login_user, logout_user
from app.web import Services, csrf_protect, render, services

log = logging.getLogger(__name__)
router = APIRouter()


def safe_next(target: str | None) -> str:
    """Only allow redirects to paths on this site (no open redirect)."""
    if not target or not target.startswith("/") or target.startswith("//") or urlparse(target).netloc:
        return "/"
    return target


@router.get("/login")
def login(request: Request, next: str | None = None, svc: Services = Depends(services)):
    next_url = safe_next(next)
    if svc.settings.dev_login:
        return render(request, "login_dev.html", {"next": next_url, "csrf_token": csrf_token(request)})
    flow = entra.start_login(svc.settings)
    request.session["auth_flow"] = flow
    request.session["next"] = next_url
    return RedirectResponse(flow["auth_uri"], status_code=303)


@router.post("/login/dev", dependencies=[Depends(csrf_protect)])
def dev_login(request: Request, email: str = Form(...), next: str = Form("/"), svc: Services = Depends(services)):
    if not svc.settings.dev_login:
        raise Forbidden("Development login is disabled.")
    email = email.strip().lower()
    if "@" not in email:
        return render(request, "login_dev.html", {"next": next, "error": "Enter an email address.",
                                                   "csrf_token": csrf_token(request)}, status_code=400)
    login_user(request, email, email.split("@")[0])
    log.info("Dev login")
    return RedirectResponse(safe_next(next), status_code=303)


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
        return render(request, "error.html", {"title": "Sign-in failed", "message": str(exc)}, status_code=400)
    login_user(request, email, name)
    log.info("Entra login succeeded")
    return RedirectResponse(next_url, status_code=303)


@router.post("/logout", dependencies=[Depends(csrf_protect)])
def logout(request: Request, svc: Services = Depends(services)):
    logout_user(request)
    if svc.settings.dev_login or not svc.settings.entra_tenant_id:
        return RedirectResponse("/login", status_code=303)
    after = f"{svc.settings.app_base_url.rstrip('/')}/login"
    return RedirectResponse(
        f"https://login.microsoftonline.com/{svc.settings.entra_tenant_id}/oauth2/v2.0/logout?post_logout_redirect_uri={quote(after, safe='')}",
        status_code=303)
