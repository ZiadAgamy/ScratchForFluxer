"""The editor: its page, the list of blocks, loading and saving a bot's blocks, and taking the code out.

Saving is "compare and set": the editor says which version it started from, and the save only goes through if that is
still the stored version. Two tabs (or two devices) can't quietly overwrite each other.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import catalogue, export, store
from .. import workspace as ws_lib
from ..config import Settings, get_settings
from ..db import db_run
from ..deps import AuthUser, read_body, render, require_csrf, require_json_content, require_user, user_rate_limit
from ..models import Asset, Bot, utcnow
from .bots import bot_json, bot_view, get_owned_bot

router = APIRouter()
save_limit = user_rate_limit("workspace-save", 90, 60)
export_limit = user_rate_limit("export", 20, 60)


class Conflict(Exception):
    def __init__(self, version: int) -> None:
        self.version = version


# --------------------------------------------------------------------------- the block list
@lru_cache(maxsize=1)
def _catalogue_json() -> tuple[bytes, str]:
    body = json.dumps(catalogue.browser_payload(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return body, f'"{hashlib.sha256(body).hexdigest()[:20]}"'


@router.get("/api/blocks")
async def block_list(request: Request) -> Response:
    """Every block the editor offers. The same for everyone, with nothing private in it, so it can be cached."""
    body, etag = _catalogue_json()
    headers = {"ETag": etag, "Cache-Control": "public, max-age=300, must-revalidate"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


# --------------------------------------------------------------------------- the page
@router.get("/bots/{bot_id}/edit", include_in_schema=False)
async def editor_page(bot_id: str, request: Request, user: AuthUser = Depends(require_user)) -> Response:
    settings = get_settings()
    bot = await db_run(_summary, user.id, bot_id)
    return render(
        request,
        "editor.html",
        {
            "page": "editor",
            "bot": bot,
            "limits": store.limits(settings),
            "runner_mode": settings.runner_effective_mode,
        },
    )


def _summary(db: Session, user_id: str, bot_id: str) -> dict[str, Any]:
    return bot_view(get_owned_bot(db, user_id, bot_id), get_settings())


# --------------------------------------------------------------------------- load and save
def _view(bot: Bot, loaded: store.Loaded, settings: Settings) -> dict[str, Any]:
    return {
        "bot": bot_json(bot, settings),
        "workspace": loaded.workspace,
        "version": bot.workspace_version,
        "dropped": loaded.dropped,
        "damaged": loaded.damaged,
        "files": loaded.files,
        "compile": loaded.compiled.as_dict(),
        "limits": store.limits(settings),
    }


def _load(db: Session, user_id: str, bot_id: str) -> dict[str, Any]:
    bot = get_owned_bot(db, user_id, bot_id)
    return _view(bot, store.load(db, bot), get_settings())


@router.get("/api/bots/{bot_id}/workspace")
async def load_workspace(bot_id: str, user: AuthUser = Depends(require_user)) -> JSONResponse:
    return JSONResponse(await db_run(_load, user.id, bot_id))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _comparable(state: Any) -> Any:
    """The workspace as far as it matters for "did the server change anything": the variables are a set (the order
    Blockly lists them in is its own business), and no variables at all may be written as a missing key."""
    if not isinstance(state, dict):
        return state
    variables = state.get("variables")
    if variables is None:
        variables = []
    if isinstance(variables, list):
        variables = sorted(variables, key=_canonical)
    return {**state, "variables": variables}


def _differs(sent: Any, kept: dict[str, Any]) -> bool:
    """True if what is stored is not just what the editor sent (so the editor should take the stored version)."""
    try:
        return _canonical(_comparable(sent)) != _canonical(_comparable(kept))
    except (ValueError, TypeError, RecursionError):
        return True


def _save(db: Session, user_id: str, bot_id: str, version: int, text: str, cleaned: dict[str, Any]) -> dict[str, Any]:
    bot = get_owned_bot(db, user_id, bot_id)
    done = db.execute(
        update(Bot)
        .where(Bot.id == bot.id, Bot.workspace_version == version)
        .values(workspace_json=text, workspace_version=Bot.workspace_version + 1, updated_at=utcnow())
    )
    if done.rowcount != 1:
        current = db.execute(select(Bot.workspace_version).where(Bot.id == bot.id)).scalar_one()
        raise Conflict(current)
    compiled, files = store.compile_with_files(db, bot.id, cleaned)
    return {"version": version + 1, "files": files, "compile": compiled.as_dict()}


@router.put("/api/bots/{bot_id}/workspace", dependencies=[Depends(save_limit)])
async def save_workspace(bot_id: str, request: Request, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    settings = get_settings()
    require_json_content(request)
    body = await read_body(request, settings.max_workspace_bytes + 4096, "This bot's blocks are too big to save.")
    try:
        data = await run_in_threadpool(ws_lib.loads, body)  # off the event loop: a big workspace takes a moment to read
    except ws_lib.WorkspaceTooBig as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ValueError:
        raise HTTPException(status_code=400, detail="That request wasn't valid JSON.") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object.")
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or not 0 <= version < 2**31 - 1:
        raise HTTPException(status_code=422, detail="Say which version of the blocks this started from.")
    try:
        cleaned = await run_in_threadpool(ws_lib.clean_workspace, data.get("workspace"))
    except ws_lib.WorkspaceTooBig as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    text = await run_in_threadpool(ws_lib.dumps, cleaned.workspace)
    if len(text.encode("utf-8")) > settings.max_workspace_bytes:
        raise HTTPException(status_code=413, detail="This bot's blocks are too big to save. Remove some blocks.")
    try:
        result = await db_run(_save, user.id, bot_id, version, text, cleaned.workspace)
    except Conflict as conflict:
        return JSONResponse(
            {
                "detail": "These blocks were changed somewhere else (another tab or device) since you opened them.",
                "version": conflict.version,
            },
            status_code=409,
        )
    result["dropped"] = cleaned.dropped
    if cleaned.dropped or _differs(data.get("workspace"), cleaned.workspace):
        result["workspace"] = cleaned.workspace  # the editor shows what is stored, not what it sent
    return JSONResponse(result)


# --------------------------------------------------------------------------- taking the code out
def _code_for_export(db: Session, user_id: str, bot_id: str, with_files: bool) -> tuple[Bot, str, list]:
    bot = get_owned_bot(db, user_id, bot_id)
    loaded = store.load(db, bot)
    if not loaded.compiled.ok:
        raise HTTPException(
            status_code=409, detail="Fix the problems listed in the editor first. Code with errors can't be downloaded."
        )
    files: list[tuple[str, bytes]] = []
    if with_files:
        rows = db.execute(select(Asset.name, Asset.data).where(Asset.bot_id == bot.id).order_by(Asset.name)).all()
        files = [(row.name, row.data) for row in rows]
    return bot, loaded.compiled.code, files


def _download(content: bytes | str, media_type: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/bots/{bot_id}/export/main.py", dependencies=[Depends(export_limit)], include_in_schema=False)
async def export_python(bot_id: str, user: AuthUser = Depends(require_user)) -> Response:
    _bot, code, _files = await db_run(_code_for_export, user.id, bot_id, False)
    return _download(code, "text/x-python; charset=utf-8", "main.py")


@router.get("/bots/{bot_id}/export/bot.zip", dependencies=[Depends(export_limit)], include_in_schema=False)
async def export_folder(bot_id: str, user: AuthUser = Depends(require_user)) -> Response:
    bot, code, files = await db_run(_code_for_export, user.id, bot_id, True)
    return _download(export.build_zip(code, files), "application/zip", f"{export.file_stem(bot.name)}.zip")
