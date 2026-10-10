"""The runner as a web service:  uvicorn scratch_fluxer.runner.app:create_runner_app --factory

It is what a site on Vercel (or anywhere that can't keep a process alive) talks to. Every call under ``/v1`` must be
signed with ``RUNNER_SECRET``; ``/healthz`` is open and says nothing private.

    PUT    /v1/runs/{bot id}?replace=1   start a bot (the body is the request from ``protocol.StartRequest``)
    DELETE /v1/runs/{bot id}             stop it
    GET    /v1/runs/{bot id}?after=N     its state and the messages after number N
"""

from __future__ import annotations

import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import MIN_SECRET_LENGTH, Settings, get_settings
from ..deps import parse_json, read_body
from ..logs import show_our_log
from . import sandbox
from .manager import RunnerManager
from .protocol import (
    BOT_ID_RE,
    MAX_CODE_CHARS,
    RUN_ID_RE,
    Refused,
    RunnerError,
    StartRequest,
    Verifier,
)

log = logging.getLogger("scratch_fluxer.runner")
REMOTE_STOP_REASONS = ("you", "removed")


def create_runner_app(settings: Settings | None = None, manager: RunnerManager | None = None) -> FastAPI:
    settings = settings or get_settings()
    secret = settings.runner_secret
    if not secret or len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"RUNNER_SECRET must be set to a long random string (at least {MIN_SECRET_LENGTH} characters)."
        )
    verifier = Verifier(secret)
    own_users = settings.runner_uid_base is not None and sandbox.can_switch_users()
    if own_users:
        sandbox.require_user_switching()  # stop here, at the start, rather than at the first bot that can't be stopped
    holder: dict[str, RunnerManager | None] = {"manager": manager}
    max_body = settings.max_assets_total_bytes * 4 // 3 + 4 * MAX_CODE_CHARS + 65536

    def runs() -> RunnerManager:
        if holder["manager"] is None:
            holder["manager"] = RunnerManager(settings)
        return holder["manager"]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        show_our_log()
        yield
        if holder["manager"] is not None:
            await holder["manager"].shutdown()

    app = FastAPI(title="Scratch For Fluxer runner", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def plain_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    async def signed_body(request: Request) -> bytes:
        body = await read_body(request, max_body)
        target = request.scope.get("raw_path", request.url.path.encode("ascii", "replace")).decode("ascii", "replace")
        query = request.scope.get("query_string", b"").decode("ascii", "replace")
        verifier.check(request.method, target + ("?" + query if query else ""), body, request.headers)
        return body

    def bot_id_of(bot_id: str) -> str:
        if not BOT_ID_RE.fullmatch(bot_id):
            raise Refused("That request wasn't valid.")
        return bot_id

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, object]:
        manager = holder["manager"]
        return {
            "ok": True,
            "running": len(manager.active()) if manager else 0,
            "room": settings.runner_max_concurrent,
            "user_per_bot": own_users,
        }

    @app.put("/v1/runs/{bot_id}")
    async def start(bot_id: str, request: Request, replace: int = 1) -> JSONResponse:
        body = await signed_body(request)
        wanted = StartRequest.from_json(parse_json(body))
        if wanted.bot_id != bot_id_of(bot_id):
            raise Refused("That request wasn't valid.")
        return JSONResponse(await runs().start(wanted, replace=bool(replace)))

    @app.delete("/v1/runs/{bot_id}")
    async def stop(bot_id: str, request: Request, why: str = "you") -> JSONResponse:
        await signed_body(request)
        if why not in REMOTE_STOP_REASONS:
            raise Refused("That request wasn't valid.")
        return JSONResponse(await runs().stop(bot_id_of(bot_id), why))

    @app.get("/v1/runs/{bot_id}")
    async def status(bot_id: str, request: Request, after: int = 0, run: str | None = None) -> JSONResponse:
        await signed_body(request)
        if not 0 <= after < 2**31 or (run is not None and not RUN_ID_RE.fullmatch(run)):
            raise Refused("That request wasn't valid.")
        return JSONResponse(runs().snapshot(bot_id_of(bot_id), after, run))

    @app.exception_handler(RunnerError)
    async def runner_error(_request: Request, exc: RunnerError) -> JSONResponse:
        return JSONResponse({"detail": exc.message, "kind": exc.kind}, status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)  # also the "no such page" and "wrong method" answers
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"detail": str(exc.detail), "kind": "error"}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": "That request wasn't valid.", "kind": "refused"}, status_code=422)

    @app.exception_handler(Exception)
    async def broken(_request: Request, exc: Exception) -> JSONResponse:
        # Where it happened, never what the error said: a message can quote what the call carried (a bot token).
        where = "".join(traceback.format_tb(exc.__traceback__)).rstrip()
        log.error("The runner failed on a call: %s\n%s", type(exc).__name__, where)
        return JSONResponse({"detail": "Something went wrong on the runner.", "kind": "error"}, status_code=500)

    return app
