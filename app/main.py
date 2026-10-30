"""FastAPI application: wiring, startup checks, security headers and error responses.

The same app serves the JSON API (/api), sign-in (/auth), the health check (/healthz)
and the built React app (everything else, see app/spa.py).

Run locally:      uvicorn app.main:app --reload
Run in Docker:    gunicorn -c gunicorn.conf.py app.main:app   (see Dockerfile)
"""

import logging
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app.auth.accounts import ensure_hr_accounts
from app.auth.session import CsrfError, Forbidden, NotLoggedIn
from app.config import Settings, get_settings
from app.db.file_store import FileStore, GridFSFileStore
from app.db.mongo import connect, ensure_indexes
from app.db.repositories import Repository
from app.email.base import EmailSender
from app.routes import accounts, auth, cycles, health
from app.spa import mount_spa
from app.web import Services

log = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
                               "form-action 'self'",
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
    ensure_hr_accounts(repo, settings)
    services = Services(settings=settings, repo=repo, files=files or GridFSFileStore(repo.db),
                        email_sender=email_sender or make_email_sender(settings))

    app = FastAPI(title="Attendance Correction Portal", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie="attendance_session",
                       max_age=settings.session_hours * 3600, same_site="lax", https_only=settings.is_production)
    for module in (health, auth, accounts, cycles):
        app.include_router(module.router)
    mount_spa(app, settings.frontend_dist_path)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if settings.is_production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/assets/"):
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")  # hashed file names
        else:
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(NotLoggedIn)
    async def not_logged_in(request: Request, exc: NotLoggedIn):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "Your session has ended. Please sign in again."}, status_code=401)
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)

    @app.exception_handler(Forbidden)
    async def forbidden(request: Request, exc: Forbidden):
        return JSONResponse({"error": exc.message}, status_code=403)

    @app.exception_handler(CsrfError)
    async def csrf_failed(request: Request, exc: CsrfError):
        return JSONResponse({"error": "Your session has expired or the page is out of date. Please reload and try again."},
                            status_code=403)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": "The request was not valid."}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"error": "Something went wrong. Please try again."}, status_code=500)

    log.info("App started: env=%s sign_in=%s email_mode=%s frontend=%s", settings.env, settings.sign_in_mode,
             settings.email_mode, "built" if (settings.frontend_dist_path / "index.html").is_file() else "not built")
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
