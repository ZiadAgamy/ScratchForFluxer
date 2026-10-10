"""FastAPI application factory.

Run locally:   python -m scratch_fluxer        (or: uvicorn index:app --reload)
On Vercel:     the platform imports ``app`` from this module (see pyproject.toml / index.py).
"""

from __future__ import annotations

import html
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import CALLBACK_PATH, ON_VERCEL, PUBLIC_DIR, Settings, get_settings
from .db import init_db
from .deps import LoginRequired, peek_user, render
from .logs import show_our_log
from .routes import account, assets, auth, bots, editor_api, pages, runs
from .runner import service as runner_service
from .security import security_headers

log = logging.getLogger("scratch_fluxer")


def _setup_page(problems) -> str:
    items = "".join(f"<li><code>{html.escape(p.key)}</code>: {html.escape(p.message)}</li>" for p in problems)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Almost ready - Scratch For Fluxer</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0e0d10;color:#ebeaee;
font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:24px}}
main{{max-width:640px;background:#17161a;border:1px solid #2a2830;border-radius:16px;padding:28px 32px}}
h1{{margin:0 0 6px;font-size:22px}} p{{color:#a9a7b3;margin:0 0 14px}}
li{{margin:10px 0}} code{{background:#0e0d10;border:1px solid #2a2830;border-radius:6px;padding:2px 6px;color:#7fb0ff}}
</style></head><body><main>
<h1>Almost ready</h1>
<p>The site is deployed, but a few settings are missing. Add them in your host's environment variables and redeploy.</p>
<ul>{items}</ul>
<p style="margin-top:18px">The README walks through each one.</p>
</main></body></html>"""


def _server_error(request: Request) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Something went wrong on our side."}, status_code=500)
    return render(
        request,
        "error.html",
        {
            "title": "Something went wrong",
            "message": "That one's on us. Please try again in a moment.",
            "action_href": "/",
            "action_label": "Back home",
        },
        status_code=500,
    )


def _log_start_notes(settings: Settings) -> None:
    """Things worth knowing, in the log where whoever runs the site will look. None of them contain a secret."""
    for problem in (*settings.problems(), *settings.warnings()):
        if not problem.fatal:
            log.warning("%s: %s", problem.key, problem.message)
    if settings.public_base_url and settings.fluxer_client_id:
        log.info(
            "Login with Fluxer needs this redirect address in your Fluxer application: %s%s",
            settings.public_base_url,
            CALLBACK_PATH,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    show_our_log()
    settings = get_settings()
    if settings.fatal_problems():
        log.error("Configuration incomplete: %s", ", ".join(p.key for p in settings.fatal_problems()))
    else:
        _log_start_notes(settings)
        try:
            await run_in_threadpool(init_db)
        except Exception:  # noqa: BLE001
            log.exception("Could not initialise the database")
    yield
    await runner_service.shutdown()  # bots the site started itself are stopped with it


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )

    headers = security_headers(settings)
    problems = settings.fatal_problems()

    @app.middleware("http")
    async def guard_and_secure(request: Request, call_next):
        path = request.url.path
        if problems and path != "/healthz":
            response: Response = HTMLResponse(_setup_page(problems), status_code=503)
        else:
            try:
                response = await call_next(request)
            except Exception:  # noqa: BLE001 - last line of defence; keeps headers on 500 pages too
                log.exception("Unhandled error on %s %s", request.method, path)
                await peek_user(request)
                response = _server_error(request)
        for key, value in headers.items():
            response.headers.setdefault(key, value)
        if not path.startswith("/static/") and path != "/favicon.ico":
            # Pages and API answers are personal (they carry the CSRF token, bot names...): never cache them.
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # ---- routers
    app.include_router(pages.router)
    app.include_router(auth.router)
    app.include_router(bots.router)
    app.include_router(account.router)
    app.include_router(assets.router)
    app.include_router(editor_api.router)
    app.include_router(runs.router)

    # ---- static files (Vercel serves public/ from its CDN; locally FastAPI does)
    if not ON_VERCEL:
        app.mount("/static", StaticFiles(directory=str(PUBLIC_DIR / "static")), name="static")

        @app.get("/favicon.ico", include_in_schema=False)
        async def favicon() -> FileResponse:
            return FileResponse(PUBLIC_DIR / "favicon.ico", media_type="image/x-icon")

    # ---- error handling
    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Please log in again."}, status_code=401)
        target = request.url.path
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(f"/login?next={quote(target, safe='/')}", status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith(("/api/", "/static/")):
            return JSONResponse(
                {"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None)
            )
        await peek_user(request)
        title = {404: "Page not found", 403: "Not allowed", 409: "Not yet", 429: "Slow down"}.get(
            exc.status_code, "Something went wrong"
        )
        message = (
            "That page doesn't exist."
            if exc.status_code == 404
            else str(exc.detail)
            if exc.status_code < 500
            else "Please try again."
        )
        return render(
            request,
            "error.html",
            {"title": title, "message": message, "action_href": "/", "action_label": "Back home"},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "That request wasn't valid."}, status_code=422)

    return app


app = create_app()
