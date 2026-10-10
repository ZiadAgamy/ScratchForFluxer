"""Account: delete everything we hold about the logged-in person."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import db_run
from ..deps import SESSION_COOKIE, AuthUser, read_json, require_csrf, user_rate_limit
from ..models import Bot, User
from ..runner import service as runner_service

log = logging.getLogger("scratch_fluxer.account")
router = APIRouter()


def _delete_everything(db: Session, user_id: str) -> list[str]:
    """Delete the user; their bots and assets go with them (ON DELETE CASCADE). Returns the bot ids."""
    bot_ids = list(db.execute(select(Bot.id).where(Bot.user_id == user_id)).scalars())
    user = db.get(User, user_id)
    if user is not None:
        db.delete(user)
    return bot_ids


@router.delete("/api/me", dependencies=[Depends(user_rate_limit("account-delete", 5, 300))])
async def delete_account(request: Request, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    """Permanently delete the account's bots, saved tokens, blocks and uploaded files.

    The person must type their Fluxer username to confirm, so a stray click or a script can't do it.
    """
    data: dict[str, Any] = await read_json(request)
    typed = data.get("confirm")
    if not isinstance(typed, str) or typed.strip().lstrip("@").casefold() != user.username.casefold():
        raise HTTPException(status_code=422, detail="Type your Fluxer username exactly as shown to confirm.")

    bot_ids = await db_run(_delete_everything, user.id)
    await _stop_running_bots(bot_ids)
    log.info("account deleted (%d bots)", len(bot_ids))

    response = JSONResponse({"ok": True, "redirect": "/"})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


async def _stop_running_bots(bot_ids: list[str]) -> None:
    """Stop any running copies of the deleted bots."""
    for bot_id in bot_ids:
        await runner_service.stop_quietly(bot_id)
