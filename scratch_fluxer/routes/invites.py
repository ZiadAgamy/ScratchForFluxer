"""Sharing a bot: invite links, the people who joined, and what each of them may do.

* Only the owner makes or cancels links, sees who joined, changes what they may do and removes them.
* A link is a long random secret. Only its SHA-256 is stored, so the link can be shown once, when it is made.
* A link can end by time, by how many people used it, or when the owner cancels it. Joining checks all three in one
  database statement, so two people can't both use the last place.
* A person who joined can leave at any time. The owner can never be removed, and the token is never shown to anyone
  but the owner's own server-side use (see ``access.py``).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import access as acl
from .. import fluxer_api as fx
from ..config import get_settings
from ..db import db_run
from ..deps import AuthUser, rate_limit, read_json, render, require_csrf, require_user, user_rate_limit
from ..models import Bot, BotInvite, BotMember, User, iso_utc, utcnow
from .bots import BOT_ID_RE

log = logging.getLogger("scratch_fluxer.invites")
router = APIRouter()
write_limit = user_rate_limit("share-write", 30, 60)
read_limit = user_rate_limit("share-read", 120, 60)
lookup_limit = rate_limit("invite-lookup", 40, 60)

#: How long a link lasts: the words the editor sends, and the time they mean (``None`` is no end).
EXPIRIES: dict[str, timedelta | None] = {
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "never": None,
}
#: How many people may use a link (``None`` is any number, up to the limit of people on a bot).
USE_CHOICES = (1, 5, 10, 25)
CODE_LENGTH = 32


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _valid_code(code: Any) -> bool:
    return isinstance(code, str) and len(code) == CODE_LENGTH and all(ch.isalnum() or ch in "-_" for ch in code)


def _aware(value: Any) -> Any:
    """A stored time with its zone (SQLite hands them back without one; they were all saved as UTC)."""
    return value.replace(tzinfo=utcnow().tzinfo) if value is not None and value.tzinfo is None else value


def invite_state(invite: BotInvite, now: Any = None) -> str:
    """``active``, ``revoked``, ``expired`` or ``used up``."""
    now = now or utcnow()
    if invite.revoked:
        return "revoked"
    expires = _aware(invite.expires_at)
    if expires is not None and expires <= now:
        return "expired"
    if invite.max_uses is not None and invite.uses >= invite.max_uses:
        return "used up"
    return "active"


def invite_view(invite: BotInvite) -> dict[str, Any]:
    return {
        "id": invite.id,
        "hint": invite.code_hint,
        "perms": sorted(acl.parse_perms(invite.perms)),
        "state": invite_state(invite),
        "expires_at": iso_utc(invite.expires_at),
        "max_uses": invite.max_uses,
        "uses": invite.uses,
        "created_at": iso_utc(invite.created_at),
    }


def member_view(member: BotMember, user: User) -> dict[str, Any]:
    settings = get_settings()
    return {
        "id": member.id,
        "name": user.global_name or user.username,
        "username": user.username,
        "avatar_url": fx.avatar_url(settings, user.fluxer_id, user.avatar_hash),
        "perms": sorted(acl.parse_perms(member.perms)),
        "joined_at": iso_utc(member.created_at),
    }


def _owner_bot(db: Session, user_id: str, bot_id: str, *, lock: bool = False) -> Bot:
    """The bot, for its owner only. Everyone else gets the same 404 as for a bot that isn't there."""
    found = acl.get_access(db, user_id, bot_id, lock=lock)
    if not found.owner:
        raise HTTPException(status_code=404, detail=acl.NO_ACCESS)
    return found.bot


def _sharing(db: Session, bot: Bot) -> dict[str, Any]:
    settings = get_settings()
    invites = db.execute(
        select(BotInvite).where(BotInvite.bot_id == bot.id, BotInvite.revoked.is_(False)).order_by(BotInvite.created_at.desc())
    ).scalars()
    members = db.execute(
        select(BotMember, User)
        .join(User, User.id == BotMember.user_id)
        .where(BotMember.bot_id == bot.id)
        .order_by(BotMember.created_at)
    ).all()
    return {
        "invites": [invite_view(invite) for invite in invites],
        "members": [member_view(member, user) for member, user in members],
        "permissions": [{"key": key, "name": name, "what": what} for key, name, what in acl.PERMISSION_LIST],
        "expiries": list(EXPIRIES),
        "use_choices": list(USE_CHOICES),
        "limits": {"members": settings.max_members_per_bot, "invites": settings.max_invites_per_bot},
    }


# --------------------------------------------------------------------------- database work
def _read(db: Session, user_id: str, bot_id: str) -> dict[str, Any]:
    return _sharing(db, _owner_bot(db, user_id, bot_id))


def _create(db: Session, user_id: str, bot_id: str, perms: frozenset[str], expires: str, max_uses: int | None) -> tuple:
    settings = get_settings()
    bot = _owner_bot(db, user_id, bot_id, lock=True)
    live = [invite for invite in db.execute(select(BotInvite).where(BotInvite.bot_id == bot.id, BotInvite.revoked.is_(False))).scalars()]
    if sum(1 for invite in live if invite_state(invite) == "active") >= settings.max_invites_per_bot:
        raise HTTPException(
            status_code=409,
            detail=f"This bot already has {settings.max_invites_per_bot} active links. Cancel one to make another.",
        )
    code = secrets.token_urlsafe(CODE_LENGTH)[:CODE_LENGTH]
    delta = EXPIRIES[expires]
    invite = BotInvite(
        bot_id=bot.id,
        created_by=user_id,
        code_hash=hash_code(code),
        code_hint=code[-4:],
        perms=acl.join_perms(perms),
        expires_at=(utcnow() + delta) if delta is not None else None,
        max_uses=max_uses,
    )
    db.add(invite)
    db.flush()
    return invite_view(invite), code


def _cancel(db: Session, user_id: str, bot_id: str, invite_id: str) -> None:
    bot = _owner_bot(db, user_id, bot_id)
    done = db.execute(
        update(BotInvite)
        .where(BotInvite.id == invite_id, BotInvite.bot_id == bot.id)
        .values(revoked=True)
        .execution_options(synchronize_session=False)
    )
    if not done.rowcount:  # type: ignore[attr-defined]
        raise HTTPException(status_code=404, detail="That link doesn't exist.")


def _member_row(db: Session, bot: Bot, member_id: str) -> tuple[BotMember, User]:
    row = db.execute(
        select(BotMember, User)
        .join(User, User.id == BotMember.user_id)
        .where(BotMember.id == member_id, BotMember.bot_id == bot.id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="That person isn't on this bot.")
    return row[0], row[1]


def _set_perms(db: Session, user_id: str, bot_id: str, member_id: str, perms: frozenset[str]) -> dict[str, Any]:
    bot = _owner_bot(db, user_id, bot_id)
    member, user = _member_row(db, bot, member_id)
    member.perms = acl.join_perms(perms)
    db.flush()
    return member_view(member, user)


def _remove(db: Session, user_id: str, bot_id: str, member_id: str) -> None:
    bot = _owner_bot(db, user_id, bot_id)
    member, _user = _member_row(db, bot, member_id)
    db.delete(member)


def _leave(db: Session, user_id: str, bot_id: str) -> None:
    found = acl.get_access(db, user_id, bot_id)
    if found.owner or found.member is None:
        raise HTTPException(status_code=409, detail="You own this bot, so there is nothing to leave.")
    db.delete(found.member)


def _find_invite(db: Session, code: str) -> BotInvite | None:
    return db.execute(select(BotInvite).where(BotInvite.code_hash == hash_code(code))).scalar_one_or_none()


def _preview(db: Session, user_id: str, code: str) -> dict[str, Any]:
    """What the invite page says: the bot, who owns it, what joining allows, and whether the link still works."""
    invite = _find_invite(db, code) if _valid_code(code) else None
    if invite is None:
        return {"state": "invalid"}
    bot = db.get(Bot, invite.bot_id)
    owner = db.get(User, bot.user_id) if bot else None
    if bot is None or owner is None:
        return {"state": "invalid"}
    state = invite_state(invite)
    if bot.user_id == user_id:
        state = "own"
    elif db.execute(select(BotMember.id).where(BotMember.bot_id == bot.id, BotMember.user_id == user_id)).first():
        state = "already"
    perms = acl.parse_perms(invite.perms)
    settings = get_settings()
    return {
        "state": state,
        "bot": {
            "id": bot.id,
            "name": bot.name,
            "bot_username": bot.bot_username,
            "avatar_url": fx.avatar_url(settings, bot.bot_user_id, bot.bot_avatar_hash),
        },
        "owner_name": owner.global_name or owner.username,
        "allowed": [{"key": key, "name": name, "what": what, "yes": key in perms} for key, name, what in acl.PERMISSION_LIST],
    }


def _accept(db: Session, user_id: str, code: str) -> dict[str, Any]:
    settings = get_settings()
    invite = _find_invite(db, code) if _valid_code(code) else None
    if invite is None:
        raise HTTPException(status_code=404, detail="This invite link isn't valid.")
    bot = db.execute(select(Bot).where(Bot.id == invite.bot_id).with_for_update()).scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="This invite link isn't valid.")
    if bot.user_id == user_id:
        raise HTTPException(status_code=409, detail="This is your own bot.")
    already = db.execute(
        select(BotMember).where(BotMember.bot_id == bot.id, BotMember.user_id == user_id)
    ).scalar_one_or_none()
    if already is not None:
        return {"bot_id": bot.id, "redirect": f"/bots/{bot.id}/edit", "already": True}
    count = db.scalar(select(func.count()).select_from(BotMember).where(BotMember.bot_id == bot.id)) or 0
    if count >= settings.max_members_per_bot:
        raise HTTPException(
            status_code=409, detail=f"This bot already has {settings.max_members_per_bot} people on it. Ask the owner to remove someone."
        )
    # One statement checks and spends a place, so two people can't both take the last one.
    now = utcnow()
    spent = db.execute(
        update(BotInvite)
        .where(
            BotInvite.id == invite.id,
            BotInvite.revoked.is_(False),
            or_(BotInvite.expires_at.is_(None), BotInvite.expires_at > now),
            or_(BotInvite.max_uses.is_(None), BotInvite.uses < BotInvite.max_uses),
        )
        .values(uses=BotInvite.uses + 1)
        .execution_options(synchronize_session=False)  # the check is the database's; no comparing times in Python
    )
    if not spent.rowcount:  # type: ignore[attr-defined]
        raise HTTPException(status_code=410, detail="This invite link has ended. Ask the owner for a new one.")
    db.add(BotMember(bot_id=bot.id, user_id=user_id, perms=invite.perms, invite_id=invite.id))
    try:
        db.flush()
    except IntegrityError:  # the same person joined twice at once: the second one is already in
        db.rollback()
        return {"bot_id": bot.id, "redirect": f"/bots/{bot.id}/edit", "already": True}
    return {"bot_id": bot.id, "redirect": f"/bots/{bot.id}/edit", "already": False}


# --------------------------------------------------------------------------- the owner's API
def _bot_id(bot_id: str) -> str:
    if not BOT_ID_RE.fullmatch(bot_id or ""):
        raise HTTPException(status_code=404, detail=acl.NO_ACCESS)
    return bot_id


@router.get("/api/bots/{bot_id}/sharing", dependencies=[Depends(read_limit)])
async def read_sharing(bot_id: str, user: AuthUser = Depends(require_user)) -> JSONResponse:
    return JSONResponse(await db_run(_read, user.id, _bot_id(bot_id)))


@router.post("/api/bots/{bot_id}/invites", status_code=201, dependencies=[Depends(write_limit)])
async def create_invite(bot_id: str, request: Request, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    data = await read_json(request)
    perms = acl.clean_perms(data.get("perms", []))
    expires = data.get("expires", "7d")
    if not isinstance(expires, str) or expires not in EXPIRIES:
        raise HTTPException(status_code=422, detail="Choose how long the link lasts.")
    max_uses = data.get("max_uses")
    if max_uses is not None and (isinstance(max_uses, bool) or max_uses not in USE_CHOICES):
        raise HTTPException(status_code=422, detail="Choose how many people can use the link.")
    invite, code = await db_run(_create, user.id, _bot_id(bot_id), perms, expires, max_uses)
    base = get_settings().public_base_url or ""
    return JSONResponse({"invite": invite, "url": f"{base}/invite/{code}"}, status_code=201)


@router.delete("/api/bots/{bot_id}/invites/{invite_id}", dependencies=[Depends(write_limit)])
async def cancel_invite(bot_id: str, invite_id: str, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    await db_run(_cancel, user.id, _bot_id(bot_id), invite_id)
    return JSONResponse({"ok": True})


@router.patch("/api/bots/{bot_id}/members/{member_id}", dependencies=[Depends(write_limit)])
async def change_member(bot_id: str, member_id: str, request: Request, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    data = await read_json(request)
    perms = acl.clean_perms(data.get("perms"))
    return JSONResponse({"member": await db_run(_set_perms, user.id, _bot_id(bot_id), member_id, perms)})


@router.delete("/api/bots/{bot_id}/members/{member_id}", dependencies=[Depends(write_limit)])
async def remove_member(bot_id: str, member_id: str, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    await db_run(_remove, user.id, _bot_id(bot_id), member_id)
    return JSONResponse({"ok": True})


@router.delete("/api/bots/{bot_id}/membership", dependencies=[Depends(write_limit)])
async def leave_bot(bot_id: str, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    await db_run(_leave, user.id, _bot_id(bot_id))
    return JSONResponse({"ok": True, "redirect": "/bots"})


# --------------------------------------------------------------------------- joining
@router.get("/invite/{code}", include_in_schema=False, dependencies=[Depends(lookup_limit)])
async def invite_page(code: str, request: Request, user: AuthUser = Depends(require_user)) -> Response:
    preview = await db_run(_preview, user.id, code)
    status = 404 if preview["state"] == "invalid" else 200
    response = render(request, "invite.html", {"page": "invite", "invite": preview, "code": code}, status_code=status)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"  # the link is a secret: it must not leak in a Referer header
    return response


@router.post("/api/invites/accept", dependencies=[Depends(lookup_limit), Depends(write_limit)])
async def accept_invite(request: Request, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    data = await read_json(request)
    code = data.get("code")
    if not isinstance(code, str):
        raise HTTPException(status_code=422, detail="This invite link isn't valid.")
    return JSONResponse(await db_run(_accept, user.id, code))
