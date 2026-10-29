"""FastAPI application: wiring, startup checks, security headers and error pages.

Run locally:      uvicorn app.main:app --reload
Run in Azure:     gunicorn -w 2 -k uvicorn.workers.UvicornWorker app.main:app --timeout 300
"""

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth.session import CsrfError, Forbidden, NotLoggedIn
from app.config import Settings, get_settings
from app.db.file_store import FileStore, GridFSFileStore
from app.db.mongo import connect, ensure_indexes
from app.db.repositories import Repository
from app.email.base import EmailSender
from app.routes import api, auth, hr, team
from app.web import Services, build_templates, render

log = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "form-action 'self' https://login.microsoftonline.com; frame-ancestors 'none'; base-uri 'self'",
}


def make_email_sender(settings: Settings) -> EmailSender:
    if settings.email_mode == "graph":
        from app.email.graph_sender import GraphSender

        return GraphSender(settings)
    from app.email.console_sender import ConsoleSender

    return ConsoleSender()


def create_app(settings: Settings | None = None, *, repo: Repository | None = None, files: FileStore | None = None,
               email_sender: EmailSender | None = None) -> FastAPI:
    settings = settings or get_settings()
    problems = settings.startup_problems()
    if problems:
        raise RuntimeError("Refusing to start:\n  - " + "\n  - ".join(problems))

    if repo is None:
        db = connect(settings)
        ensure_indexes(db)
        repo = Repository(db)
    services = Services(settings=settings, repo=repo, files=files or GridFSFileStore(repo.db),
                        email_sender=email_sender or make_email_sender(settings))

    app = FastAPI(title="Attendance Correction Portal", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services
    app.state.templates = build_templates(settings)
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie="attendance_session",
                       max_age=settings.session_hours * 3600, same_site="lax", https_only=settings.is_production)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    for module in (auth, team, hr, api):
        app.include_router(module.router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if settings.is_production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if not request.url.path.startswith("/static"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    def wants_json(request: Request) -> bool:
        return request.url.path.startswith("/api/")

    @app.exception_handler(NotLoggedIn)
    async def not_logged_in(request: Request, exc: NotLoggedIn):
        if wants_json(request):
            return JSONResponse({"error": "Your session has ended. Please sign in again."}, status_code=401)
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)

    @app.exception_handler(Forbidden)
    async def forbidden(request: Request, exc: Forbidden):
        if wants_json(request):
            return JSONResponse({"error": exc.message}, status_code=403)
        return render(request, "error.html", {"title": "No access", "message": exc.message}, status_code=403)

    @app.exception_handler(CsrfError)
    async def csrf_failed(request: Request, exc: CsrfError):
        message = "Your session has expired or the form is out of date. Please reload the page and try again."
        if wants_json(request):
            return JSONResponse({"error": message}, status_code=403)
        return render(request, "error.html", {"title": "Please try again", "message": message}, status_code=403)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        if wants_json(request):
            return JSONResponse({"error": "The request was not valid."}, status_code=422)
        return render(request, "error.html", {"title": "Invalid request", "message": "Something in the form was missing or not valid."},
                      status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        if wants_json(request):
            return JSONResponse({"error": "Something went wrong. Please try again."}, status_code=500)
        return render(request, "error.html", {"title": "Something went wrong", "message": "Please try again. If it keeps happening, contact HR."},
                      status_code=500)

    log.info("App started: env=%s email_mode=%s dev_login=%s", settings.env, settings.email_mode, settings.dev_login)
    return app


def _create_default_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()


def __getattr__(name: str):
    # `app.main:app` is created on first access, so importing this module (e.g. in tests) has no side effects.
    if name == "app":
        global app
        app = _create_default_app()
        return app
    raise AttributeError(name)
