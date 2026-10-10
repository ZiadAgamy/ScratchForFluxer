"""A bot's files: list, upload, download and delete (the pictures, JSON and text its blocks can use).

* Only the bot's owner can reach its files (another person's bot looks like one that doesn't exist).
* A file is stored only if its name is plain and its bytes are what the extension says (see ``assets.inspect``).
* Files are served back with ``nosniff`` and a sandboxing policy, so even a file that fooled the checks can't run
  anything on this site.
"""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import assets as assetlib
from .. import store
from .. import workspace as ws_lib
from ..config import Settings, get_settings
from ..db import db_run
from ..deps import AuthUser, read_body, require_csrf, require_user, user_rate_limit
from ..models import Asset, Bot, utcnow
from .bots import BOT_ID_RE, get_owned_bot

router = APIRouter()
write_limit = user_rate_limit("assets-write", 40, 60)

#: What a downloaded file is allowed to do when it is opened on its own (nothing).
FILE_CSP = "default-src 'none'; sandbox"


def megabytes(size: int) -> str:
    value = size / (1024 * 1024)
    return f"{value:g} MB" if value >= 1 else f"{size // 1024} KB"


def _locked_bot(db: Session, user_id: str, bot_id: str) -> Bot:
    """The person's bot, locked until the transaction ends so two uploads can't both fit under a limit."""
    if not BOT_ID_RE.fullmatch(bot_id or ""):
        raise HTTPException(status_code=404, detail="That bot doesn't exist.")
    bot = db.execute(select(Bot).where(Bot.id == bot_id, Bot.user_id == user_id).with_for_update()).scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="That bot doesn't exist.")
    return bot


def _bad_name() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=(
            "That file name can't be used. Use letters, numbers, spaces, dots, dashes or underscores, "
            f"and a file type from this list: {assetlib.ACCEPTED_LABEL}."
        ),
    )


# --------------------------------------------------------------------------- database work
def _list(db: Session, user_id: str, bot_id: str) -> dict[str, Any]:
    bot = get_owned_bot(db, user_id, bot_id)
    workspace, _dropped, _damaged = ws_lib.from_text(bot.workspace_json)
    used = ws_lib.files_used(workspace)
    return {
        "files": [store.file_view(row, used) for row in store.asset_rows(db, bot.id)],
        "limits": store.limits(get_settings()),
    }


def _store(db: Session, user_id: str, bot_id: str, name: str, upload: assetlib.Upload, settings: Settings) -> dict:
    bot = _locked_bot(db, user_id, bot_id)
    rows = db.execute(select(Asset.id, Asset.name, Asset.size).where(Asset.bot_id == bot.id)).all()
    same = next((row for row in rows if row.name == name), None)
    clash = next((row for row in rows if row.name.lower() == name.lower() and row.name != name), None)
    if clash is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"You already have a file called “{clash.name}”. File names are not case-sensitive, so rename "
                "the new one, or delete the other file first."
            ),
        )
    if same is None and len(rows) >= settings.max_assets_per_bot:
        raise HTTPException(
            status_code=409,
            detail=f"This bot already has {settings.max_assets_per_bot} files. Delete one to add another.",
        )
    total = sum(row.size for row in rows if row.name != name) + len(upload.data)
    if total > settings.max_assets_total_bytes:
        raise HTTPException(
            status_code=409,
            detail=(
                f"That would use more than {megabytes(settings.max_assets_total_bytes)} for this bot's files. "
                "Delete a file you don't need first."
            ),
        )
    fields = {
        "mime": upload.content_type,
        "kind": upload.kind,
        "size": len(upload.data),
        "sha256": hashlib.sha256(upload.data).hexdigest(),
        "data": upload.data,
    }
    if same is not None:
        row = db.get(Asset, same.id)
        assert row is not None
        for key, value in fields.items():
            setattr(row, key, value)
        row.created_at = utcnow()
    else:
        db.add(Asset(bot_id=bot.id, name=name, **fields))
    db.flush()
    workspace, _dropped, _damaged = ws_lib.from_text(bot.workspace_json)
    compiled, files = store.compile_with_files(db, bot.id, workspace)
    file = next(item for item in files if item["name"] == name)
    return {"file": file, "replaced": same is not None, "files": files, "compile": compiled.as_dict()}


def _fetch(db: Session, user_id: str, bot_id: str, name: str) -> tuple[str, str, str, str, bytes]:
    bot = get_owned_bot(db, user_id, bot_id)
    row = db.execute(
        select(Asset.name, Asset.mime, Asset.kind, Asset.sha256, Asset.data).where(
            Asset.bot_id == bot.id, Asset.name == name
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="That file doesn't exist.")
    return row.name, row.mime, row.kind, row.sha256, row.data


def _delete(db: Session, user_id: str, bot_id: str, name: str) -> dict[str, Any]:
    bot = _locked_bot(db, user_id, bot_id)
    removed = db.execute(delete(Asset).where(Asset.bot_id == bot.id, Asset.name == name)).rowcount
    if not removed:
        raise HTTPException(status_code=404, detail="That file doesn't exist.")
    workspace, _dropped, _damaged = ws_lib.from_text(bot.workspace_json)
    compiled, files = store.compile_with_files(db, bot.id, workspace)
    return {"ok": True, "files": files, "compile": compiled.as_dict()}


# --------------------------------------------------------------------------- API
@router.get("/api/bots/{bot_id}/assets")
async def list_files(bot_id: str, user: AuthUser = Depends(require_user)) -> JSONResponse:
    return JSONResponse(await db_run(_list, user.id, bot_id))


@router.put("/api/bots/{bot_id}/assets/{name}", dependencies=[Depends(write_limit)])
async def upload_file(bot_id: str, name: str, request: Request, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    """Store the request body as a file called ``name`` (an existing file with that name is replaced)."""
    settings = get_settings()
    plain = assetlib.clean_name(name)
    if plain is None:
        raise _bad_name()
    data = await read_body(
        request, settings.max_asset_bytes, f"That file is bigger than {megabytes(settings.max_asset_bytes)}."
    )
    upload, problem = assetlib.inspect(plain, data)
    if upload is None:
        raise HTTPException(status_code=422, detail=problem)
    result = await db_run(_store, user.id, bot_id, plain, upload, settings)
    return JSONResponse(result, status_code=200 if result["replaced"] else 201)


@router.get("/api/bots/{bot_id}/assets/{name}")
async def download_file(bot_id: str, name: str, request: Request, user: AuthUser = Depends(require_user)) -> Response:
    stored_name, mime, kind, sha256, data = await db_run(_fetch, user.id, bot_id, name)
    ascii_name = "".join(ch if ch.isascii() and ch.isalnum() or ch in " ._()-" else "_" for ch in stored_name)
    disposition = "inline" if kind == "image" else "attachment"
    headers = {
        "Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(stored_name)}",
        "Content-Security-Policy": FILE_CSP,
        "Cache-Control": "private, no-cache",
        "ETag": f'"{sha256}"',
    }
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type=mime, headers=headers)


@router.delete("/api/bots/{bot_id}/assets/{name}", dependencies=[Depends(write_limit)])
async def delete_file(bot_id: str, name: str, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    return JSONResponse(await db_run(_delete, user.id, bot_id, name))
