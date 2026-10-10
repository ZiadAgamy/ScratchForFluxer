"""Login with Fluxer (OAuth2 authorisation-code flow + PKCE) and logout."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import fluxer_api as fx
from ..config import SESSION_MAX_AGE, Settings, get_settings
from ..db import db_run
from ..deps import (
    OAUTH_COOKIE,
    SESSION_COOKIE,
    current_user,
    get_codec,
    rate_limit,
    read_body,
    render,
)
from ..models import User, utcnow
from ..security import csrf_matches, is_same_origin, make_pkce, new_csrf, new_state, safe_next

log = logging.getLogger("scratch_fluxer.auth")
router = APIRouter()

OAUTH_COOKIE_PATH = "/auth"
OAUTH_MAX_AGE = 600
MAX_LOGOUT_BODY = 4096  # the form holds one short field


def _error(request: Request, message: str, status: int = 400, title: str = "Login problem") -> Response:
    response = render(
        request,
        "error.html",
        {"title": title, "message": message, "action_href": "/login", "action_label": "Back to login"},
        status_code=status,
    )
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
    return response


async def _revoke_quietly(settings: Settings, access_token: str) -> None:
    """We only needed the token to learn who you are, so give it back. Never fails the login."""
    try:
        await asyncio.wait_for(fx.revoke_token(settings, access_token), timeout=4)
    except Exception:  # noqa: BLE001 - best effort
        log.info("Could not revoke the login token; continuing")


def _upsert_user(db: Session, fx_user: fx.FluxerUser) -> dict[str, Any]:
    user = db.execute(select(User).where(User.fluxer_id == fx_user.id)).scalar_one_or_none()
    if user is None:
        user = User(fluxer_id=fx_user.id, username=fx_user.username)
        db.add(user)
    user.username = fx_user.username
    user.global_name = fx_user.global_name
    user.avatar_hash = fx_user.avatar_hash
    user.last_login_at = utcnow()
    db.flush()
    return {"id": user.id, "epoch": user.session_epoch}


@router.get("/auth/login", dependencies=[Depends(rate_limit("auth-login", 30, 60))])
async def login_start(request: Request, next: str | None = None) -> Response:
    """Send the browser to Fluxer's consent screen."""
    settings = get_settings()
    if not settings.fluxer_client_id or not settings.fluxer_client_secret:
        return render(
            request,
            "error.html",
            {
                "title": "Login isn't set up yet",
                "message": "FLUXER_CLIENT_ID and FLUXER_CLIENT_SECRET are missing. "
                "Create an application in Fluxer, then add them to your environment (see the README).",
                "action_href": "/",
                "action_label": "Back home",
            },
            status_code=503,
        )
    try:
        redirect_uri = settings.oauth_redirect_uri(str(request.base_url))
    except RuntimeError:
        return render(
            request,
            "error.html",
            {
                "title": "Login isn't set up yet",
                "message": "APP_BASE_URL is not configured.",
                "action_href": "/",
                "action_label": "Back home",
            },
            status_code=503,
        )

    state = new_state()
    verifier, challenge = make_pkce() if settings.fluxer_oauth_pkce else (None, None)
    payload = {"s": state, "v": verifier, "n": safe_next(next), "r": redirect_uri, "t": int(time.time())}
    url = fx.build_authorize_url(settings, redirect_uri=redirect_uri, state=state, code_challenge=challenge)

    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        OAUTH_COOKIE,
        get_codec().dump_oauth(payload),
        max_age=OAUTH_MAX_AGE,
        path=OAUTH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return response


@router.get("/auth/callback", dependencies=[Depends(rate_limit("auth-callback", 30, 60))])
async def login_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    settings = get_settings()
    pending = get_codec().load_oauth(request.cookies.get(OAUTH_COOKIE), OAUTH_MAX_AGE)

    if error:
        if error == "access_denied":
            return _error(request, "You cancelled the login on Fluxer. Nothing was changed.", 400, "Login cancelled")
        return _error(request, "Fluxer returned an error while logging you in. Please try again.")

    if not pending or not csrf_matches(str(pending.get("s") or ""), state):
        return _error(
            request,
            "Your login session expired or didn't match. Start again from the login page "
            "(and make sure cookies are enabled).",
        )
    if not code or len(code) > 512:
        return _error(request, "Fluxer didn't send back a login code. Please try again.")

    redirect_uri = pending.get("r")
    if not isinstance(redirect_uri, str):
        return _error(request, "Your login session was invalid. Please try again.")

    access_token: str | None = None
    try:
        access_token = await fx.exchange_code(
            settings, code=code, redirect_uri=redirect_uri, code_verifier=pending.get("v")
        )
        fx_user = await fx.fetch_userinfo(settings, access_token)
    except fx.FluxerUnavailable as exc:
        return _error(request, exc.user_message, 502, "Fluxer is unavailable")
    except fx.FluxerError as exc:
        return _error(request, exc.user_message, 400)
    finally:
        if access_token:
            await _revoke_quietly(settings, access_token)

    row = await db_run(_upsert_user, fx_user)
    session_value = get_codec().dump_session(
        {"u": row["id"], "e": row["epoch"], "c": new_csrf(), "t": int(time.time())}
    )
    response = RedirectResponse(safe_next(pending.get("n")), status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        session_value,
        max_age=SESSION_MAX_AGE,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
    return response


def _end_sessions(db: Session, user_id: str) -> None:
    """Every login cookie issued so far stops working (they carry the number this changes)."""
    db.execute(update(User).where(User.id == user_id).values(session_epoch=User.session_epoch + 1))


@router.post("/auth/logout")
async def logout(request: Request) -> Response:
    """Plain HTML form post (carries a csrf_token field). It ends the session for good: a copy of the cookie is useless."""
    settings = get_settings()
    user = await current_user(request)
    if user is not None:
        refused = _error(
            request, "Couldn't verify that request. Refresh the page and try again.", 403, "Request blocked"
        )
        if not is_same_origin(request, settings.public_base_url):  # before anything the request carries is read
            return refused
        fields = parse_qs((await read_body(request, MAX_LOGOUT_BODY)).decode("utf-8", "replace"))
        supplied = (fields.get("csrf_token") or [None])[0]
        if not csrf_matches(user.csrf, supplied):
            return refused
        await db_run(_end_sessions, user.id)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
