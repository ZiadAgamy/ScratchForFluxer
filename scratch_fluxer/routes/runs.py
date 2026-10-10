"""Running a bot: start it, restart it, stop it, and read what it writes.

The website never runs a bot itself. It compiles the bot's saved blocks, checks the code once more, decrypts the bot's
token and hands both to the runner (see ``scratch_fluxer.runner``). What comes back is a plain description of the run:
its state and the messages it has written so far. The editor asks for it about once a second while a bot is running.

* The token is read from the database for the length of one request, sent to the runner, and never put in any answer.
* A bot's blocks are only run if they compile without errors, exactly as they are saved.
* A person can only run, stop and read their own bots (someone else's bot id looks like a bot that doesn't exist).
"""

from __future__ import annotations

import logging
from typing import Any

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import store
from ..db import db_run
from ..deps import AuthUser, get_vault, require_csrf, require_user, user_rate_limit
from ..models import Asset, Bot, utcnow
from ..runner import service
from ..runner.clients import OFF_MESSAGE
from ..runner.protocol import RUN_ID_RE, RunnerError, StartRequest
from ..security import TokenVault
from .bots import get_owned_bot

log = logging.getLogger("scratch_fluxer.runs")
router = APIRouter()
change_limit = user_rate_limit("run-change", 20, 60)
read_limit = user_rate_limit("run-read", 400, 60)


def _move_to_newest_key(db: Session, bot: Bot, vault: TokenVault) -> str:
    """After SECRET_KEY was rotated, a token that is still under an older key is saved again under the new one, the
    first time its bot runs. That way the old key can be dropped once every bot has run (or ``python -m scratch_fluxer
    reencrypt`` has been used). Returns the saved token (ciphertext) as the database holds it now."""
    saved = bot.token_ciphertext
    try:
        newer = vault.refreshed(saved)
    except InvalidToken:  # it was just read with this vault, so this cannot happen
        return saved
    if newer is None:
        return saved
    # Only if the token is still the one that was read (it may have been replaced a moment ago). Saving a token
    # again is not editing the bot, so ``updated_at`` stays as it was.
    db.execute(
        update(Bot)
        .where(Bot.id == bot.id, Bot.token_ciphertext == saved)
        .values(token_ciphertext=newer, updated_at=Bot.updated_at)
    )
    return newer


def _prepare(db: Session, user_id: str, bot_id: str) -> tuple[StartRequest, str]:
    """What the runner needs to start this bot (its code, the files the code uses and its token), and the saved
    token as it is in the database, to notice later if the bot was changed while it was starting."""
    bot = get_owned_bot(db, user_id, bot_id)
    loaded = store.load(db, bot)
    if not loaded.compiled.ok:
        raise HTTPException(
            status_code=409, detail="Fix the problems listed in the editor first. Blocks with errors can't run."
        )
    vault = get_vault()
    try:
        token = vault.decrypt(bot.token_ciphertext)
    except InvalidToken:
        log.error("a saved bot token could not be decrypted (was SECRET_KEY changed without SECRET_KEY_PREVIOUS?)")
        raise HTTPException(
            status_code=409, detail="The saved token can't be read any more. Open My bots and paste the token again."
        ) from None
    saved = _move_to_newest_key(db, bot, vault)
    used = loaded.compiled.assets
    files: list[tuple[str, bytes]] = []
    if used:
        rows = db.execute(select(Asset.name, Asset.data).where(Asset.bot_id == bot.id, Asset.name.in_(used))).all()
        files = [(row.name, bytes(row.data)) for row in rows]
    return StartRequest(bot_id=bot.id, owner=user_id, code=loaded.compiled.code, token=token, files=files), saved


def _owned(db: Session, user_id: str, bot_id: str) -> str:
    return get_owned_bot(db, user_id, bot_id).id


def _mark_run(db: Session, bot_id: str, saved_token: str) -> bool:
    """Note that the bot has run. False if the bot is gone, or has a different token now, than when it was started."""
    # Running a bot is not editing it: keep ``updated_at`` (it orders the list of bots) as it was.
    done = db.execute(
        update(Bot)
        .where(Bot.id == bot_id, Bot.token_ciphertext == saved_token)
        .values(last_run_at=utcnow(), updated_at=Bot.updated_at)
    )
    return bool(done.rowcount)  # type: ignore[attr-defined]


def _fail(exc: RunnerError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


async def _start(bot_id: str, user: AuthUser, *, replace: bool) -> JSONResponse:
    runner = service.get_runner()
    if runner.mode == "off":
        await db_run(_owned, user.id, bot_id)  # a stranger's bot is still a 404
        raise HTTPException(status_code=503, detail=OFF_MESSAGE)
    request, saved_token = await db_run(_prepare, user.id, bot_id)
    try:
        result = await runner.start(request, replace=replace)
    except RunnerError as exc:
        raise _fail(exc) from None
    finally:
        request.token = ""
    if not result.get("already_running") and not await db_run(_mark_run, request.bot_id, saved_token):
        # The bot was removed, or given another token, while it was starting. Whoever did that has already asked the
        # runner to stop it, but that may have been before this start got there, so make sure.
        await service.stop_quietly(request.bot_id)
        raise HTTPException(
            status_code=409, detail="This bot was changed while it was starting, so it was stopped. Press Run again."
        )
    return JSONResponse(result)


@router.post("/api/bots/{bot_id}/run", dependencies=[Depends(change_limit)])
async def run_bot(bot_id: str, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    """Start the bot. If it is already running, nothing changes (so a double click does no harm)."""
    return await _start(bot_id, user, replace=False)


@router.post("/api/bots/{bot_id}/restart", dependencies=[Depends(change_limit)])
async def restart_bot(bot_id: str, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    """Stop the bot if it is running, and start it again from its saved blocks."""
    return await _start(bot_id, user, replace=True)


@router.post("/api/bots/{bot_id}/stop", dependencies=[Depends(change_limit)])
async def stop_bot(bot_id: str, user: AuthUser = Depends(require_csrf)) -> JSONResponse:
    await db_run(_owned, user.id, bot_id)
    try:
        return JSONResponse(await service.get_runner().stop(bot_id, "you"))
    except RunnerError as exc:
        raise _fail(exc) from None


@router.get("/api/bots/{bot_id}/run", dependencies=[Depends(read_limit)])
async def read_run(
    bot_id: str, after: int = 0, run: str | None = None, user: AuthUser = Depends(require_user)
) -> JSONResponse:
    """The state of the bot's latest run and the messages after number ``after``.

    ``run`` is the run the caller has been reading. If the bot has started again since, the answer begins
    again from the first message (``reset``).
    """
    if not 0 <= after < 2**31 or (run is not None and not RUN_ID_RE.fullmatch(run)):
        raise HTTPException(status_code=422, detail="That request wasn't valid.")
    await db_run(_owned, user.id, bot_id)
    try:
        answer: dict[str, Any] = await service.get_runner().status(bot_id, after=after, known=run)
    except RunnerError as exc:
        raise _fail(exc) from None
    return JSONResponse(answer)
