"""Bots: the list page, the add-a-bot (paste token) flow, rename, replace token and remove.

Rules this module enforces:

* A person only ever sees and touches their own bots (everything is filtered by ``user_id``;
  someone else's bot id looks exactly like a bot that doesn't exist).
* Bot tokens are encrypted before they are stored and are never sent back to the browser.
  Only the last four characters are kept in the clear, so a person can tell tokens apart.
* You can only add a bot that your own Fluxer account owns.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import fluxer_api as fx
from ..config import Settings, get_settings
from ..db import db_run
from ..deps import AuthUser, get_vault, read_json, render, require_csrf, require_user, user_rate_limit
from ..models import Bot, User, iso_utc
from ..runner import service as runner_service
from ..security import BotTokenFormatError, TokenVault, normalize_bot_token

log = logging.getLogger("scratch_fluxer.bots")
router = APIRouter()

BOT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
NAME_MAX = 80

# Control characters, zero-width spaces and bidi overrides can be used to spoof or hide names.
# (Zero-width non-joiner/joiner and the left/right marks stay: Arabic and emoji text need them.)
_UNSAFE_CHARS = re.compile("[\x00-\x1f\x7f​⁠﻿‪-‮⁦-⁩]")


class Duplicate(Exception):
    def __init__(self, bot_id: str | None, name: str | None) -> None:
        self.bot_id, self.name = bot_id, name


# --------------------------------------------------------------------------- helpers
def clean_name(raw: Any, fallback: str = "") -> str:
    """A tidy single-line bot name, 1 to 80 characters."""
    if raw is None:
        raw = fallback
    if not isinstance(raw, str):
        raise HTTPException(status_code=422, detail="Give your bot a name.")
    # A lone surrogate (JSON can carry one as "\ud800") is not text any database or file can hold: drop it.
    name = " ".join(_UNSAFE_CHARS.sub("", raw.encode("utf-8", "ignore").decode("utf-8")).split())
    if not name:
        raise HTTPException(status_code=422, detail=f"Give your bot a name (1 to {NAME_MAX} characters).")
    if len(name) > NAME_MAX:
        raise HTTPException(status_code=422, detail=f"That name is too long. Use {NAME_MAX} characters or fewer.")
    return name


def get_owned_bot(db: Session, user_id: str, bot_id: str) -> Bot:
    """The bot, or a 404 (the same 404 whether it doesn't exist or belongs to someone else)."""
    if not BOT_ID_RE.fullmatch(bot_id or ""):
        raise HTTPException(status_code=404, detail="That bot doesn't exist.")
    bot = db.execute(select(Bot).where(Bot.id == bot_id, Bot.user_id == user_id)).scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="That bot doesn't exist.")
    return bot


def bot_view(bot: Bot, settings: Settings) -> dict[str, Any]:
    """Everything the browser may know about a bot. The token (or its ciphertext) is never part of it."""
    return {
        "id": bot.id,
        "name": bot.name,
        "bot_username": bot.bot_username,
        "application_name": bot.application_name,
        "avatar_url": fx.avatar_url(settings, bot.bot_user_id, bot.bot_avatar_hash),
        "token_last4": bot.token_last4,
        "updated_at": bot.updated_at,
        "last_run_at": bot.last_run_at,
    }


def bot_json(bot: Bot, settings: Settings) -> dict[str, Any]:
    """``bot_view`` with its dates written as text, ready for a JSON response."""
    return _jsonable(bot_view(bot, settings))


def identity_view(identity: fx.BotIdentity, settings: Settings) -> dict[str, Any]:
    return {
        "application_id": identity.application_id,
        "application_name": identity.application_name,
        "bot_username": identity.bot_username,
        "avatar_url": fx.avatar_url(settings, identity.bot_user_id, identity.bot_avatar_hash),
        "default_name": identity.application_name or identity.bot_username or "My bot",
    }


async def verify_token_for(user: AuthUser, raw_token: Any) -> tuple[str, fx.BotIdentity]:
    """Clean the pasted token, ask Fluxer who it belongs to, and make sure that is the logged-in person."""
    if not isinstance(raw_token, str):
        raise HTTPException(status_code=422, detail="Paste your bot token first.")
    try:
        token = normalize_bot_token(raw_token)
    except BotTokenFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        identity = await fx.verify_bot_token(get_settings(), token)
    except fx.InvalidBotToken as exc:
        raise HTTPException(status_code=400, detail=exc.user_message) from None
    except fx.FluxerError as exc:
        raise HTTPException(status_code=502, detail=exc.user_message) from None
    if not identity.owner_id:
        # The check below needs Fluxer to say who owns the application. Without that it can't be made.
        log.warning("Fluxer did not say who owns an application, so the owner check was skipped")
    elif identity.owner_id != user.fluxer_id:
        raise HTTPException(
            status_code=403,
            detail="This bot belongs to a different Fluxer account. You can only add bots that you own.",
        )
    return token, identity


# --------------------------------------------------------------------------- database work
def _list_bots(db: Session, user_id: str) -> list[dict[str, Any]]:
    settings = get_settings()
    rows = db.execute(select(Bot).where(Bot.user_id == user_id).order_by(Bot.updated_at.desc())).scalars().all()
    return [bot_view(bot, settings) for bot in rows]


def _find_existing(db: Session, user_id: str, bot_user_id: str) -> Bot | None:
    return db.execute(select(Bot).where(Bot.user_id == user_id, Bot.bot_user_id == bot_user_id)).scalar_one_or_none()


def _create_bot(
    db: Session, user_id: str, identity: fx.BotIdentity, token: str, name: str, max_bots: int, vault: TokenVault
) -> dict[str, Any]:
    # Lock the user row: two simultaneous requests can't both slip under the limit (Postgres; SQLite serialises).
    owner = db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one_or_none()
    if owner is None:
        raise HTTPException(status_code=401, detail="Please log in again.")
    existing = _find_existing(db, user_id, identity.bot_user_id)
    if existing is not None:
        raise Duplicate(existing.id, existing.name)
    count = db.scalar(select(func.count()).select_from(Bot).where(Bot.user_id == user_id)) or 0
    if count >= max_bots:
        raise HTTPException(
            status_code=409,
            detail=f"You've reached the limit of {max_bots} bots. Remove one to add another.",
        )
    bot = Bot(
        user_id=user_id,
        name=name,
        token_ciphertext=vault.encrypt(token),
        token_last4=token[-4:],
        application_id=identity.application_id,
        application_name=identity.application_name,
        bot_user_id=identity.bot_user_id,
        bot_username=identity.bot_username,
        bot_avatar_hash=identity.bot_avatar_hash,
    )
    db.add(bot)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Duplicate(None, None) from None
    return bot_view(bot, get_settings())


def _rename_bot(db: Session, user_id: str, bot_id: str, name: str) -> dict[str, Any]:
    bot = get_owned_bot(db, user_id, bot_id)
    bot.name = name
    db.flush()
    return bot_view(bot, get_settings())


def _replace_token(
    db: Session, user_id: str, bot_id: str, identity: fx.BotIdentity, token: str, vault: TokenVault
) -> dict[str, Any]:
    bot = get_owned_bot(db, user_id, bot_id)
    if identity.bot_user_id != bot.bot_user_id:
        raise HTTPException(
            status_code=409,
            detail="That token belongs to a different bot. To use another bot, add it as a new bot instead.",
        )
    bot.token_ciphertext = vault.encrypt(token)
    bot.token_last4 = token[-4:]
    bot.application_id = identity.application_id
    bot.application_name = identity.application_name
    bot.bot_username = identity.bot_username
    bot.bot_avatar_hash = identity.bot_avatar_hash
    db.flush()
    return bot_view(bot, get_settings())


def _delete_bot(db: Session, user_id: str, bot_id: str) -> None:
    db.delete(get_owned_bot(db, user_id, bot_id))  # assets go with it (ON DELETE CASCADE)


# --------------------------------------------------------------------------- pages
@router.get("/bots", include_in_schema=False)
async def bots_page(request: Request, user: AuthUser = Depends(require_user)) -> Response:
    settings = get_settings()
    bots = await db_run(_list_bots, user.id)
    return render(request, "bots.html", {"page": "bots", "bots": bots, "max_bots": settings.max_bots_per_user})


@router.get("/bots/new", include_in_schema=False)
async def new_bot_page(request: Request, user: AuthUser = Depends(require_user)) -> Response:
    settings = get_settings()
    bots = await db_run(_list_bots, user.id)
    return render(
        request,
        "bot_new.html",
        {
            "page": "bot_new",
            "at_limit": len(bots) >= settings.max_bots_per_user,
            "max_bots": settings.max_bots_per_user,
        },
    )


# --------------------------------------------------------------------------- API
@router.post("/api/bots/verify", dependencies=[Depends(user_rate_limit("bots-verify", 12, 60))])
async def verify_bot(request: Request, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    """Check a pasted token with Fluxer and say who it belongs to. Nothing is stored."""
    data = await read_json(request)
    _token, identity = await verify_token_for(user, data.get("token"))

    def _lookup(db: Session) -> dict[str, Any] | None:
        existing = _find_existing(db, user.id, identity.bot_user_id)
        return {"id": existing.id, "name": existing.name} if existing else None

    already = await db_run(_lookup)
    return JSONResponse({"bot": identity_view(identity, get_settings()), "already_added": already})


@router.post("/api/bots", status_code=201, dependencies=[Depends(user_rate_limit("bots-write", 20, 60))])
async def add_bot(request: Request, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    data = await read_json(request)
    token, identity = await verify_token_for(user, data.get("token"))
    name = clean_name(data.get("name"), fallback=identity.application_name or identity.bot_username)
    settings = get_settings()
    try:
        bot = await db_run(_create_bot, user.id, identity, token, name, settings.max_bots_per_user, get_vault())
    except Duplicate as dup:
        label = f" as “{dup.name}”" if dup.name else ""
        return JSONResponse(
            {"detail": f"This bot is already in your list{label}.", "existing_id": dup.bot_id}, status_code=409
        )
    return JSONResponse({"bot": _jsonable(bot), "redirect": f"/bots/{bot['id']}/edit"}, status_code=201)


@router.patch("/api/bots/{bot_id}", dependencies=[Depends(user_rate_limit("bots-write", 20, 60))])
async def rename_bot(bot_id: str, request: Request, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    data = await read_json(request)
    name = clean_name(data.get("name"))
    bot = await db_run(_rename_bot, user.id, bot_id, name)
    return JSONResponse({"bot": _jsonable(bot)})


@router.put("/api/bots/{bot_id}/token", dependencies=[Depends(user_rate_limit("bots-verify", 12, 60))])
async def replace_bot_token(bot_id: str, request: Request, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    data = await read_json(request)
    token, identity = await verify_token_for(user, data.get("token"))
    bot = await db_run(_replace_token, user.id, bot_id, identity, token, get_vault())
    await _after_token_change(bot_id)
    return JSONResponse({"bot": _jsonable(bot)})


@router.delete("/api/bots/{bot_id}", dependencies=[Depends(user_rate_limit("bots-write", 20, 60))])
async def remove_bot(bot_id: str, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    await db_run(_delete_bot, user.id, bot_id)
    await _after_bot_removed(bot_id)
    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------- the runner
async def _after_bot_removed(bot_id: str) -> None:
    """Called after a bot's row is gone. Stops a running copy of it."""
    await runner_service.stop_quietly(bot_id)


async def _after_token_change(bot_id: str) -> None:
    """A running bot still holds the old token; stop it so the next Run uses the new one."""
    await _after_bot_removed(bot_id)


def _jsonable(view: dict[str, Any]) -> dict[str, Any]:
    out = dict(view)
    for key in ("updated_at", "last_run_at"):
        out[key] = iso_utc(out.get(key))
    return out
