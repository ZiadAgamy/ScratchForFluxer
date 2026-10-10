"""Talking to Fluxer: OAuth2 login and bot-token validation.

Endpoints (from the Fluxer HTTP API docs):

* ``GET  /oauth2/authorize``        → redirects the browser to the consent screen
* ``POST /oauth2/token``            → exchange the authorisation code (form-encoded)
* ``GET  /oauth2/userinfo``         → who logged in (needs the ``identify`` scope)
* ``POST /oauth2/token/revoke``     → we revoke our token right after reading userinfo
* ``GET  /applications/@me``        → with ``Authorization: Bot <token>``: which application
                                      (and owner) a bot token belongs to

No OAuth token is ever stored. Secrets are never logged or put in exception messages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import Settings

log = logging.getLogger("scratch_fluxer.fluxer")

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_USER_AGENT = "ScratchForFluxer/0.1 (learning tool)"


class FluxerError(Exception):
    """Base class. ``user_message`` is safe to show to the person using the site."""

    user_message = "Something went wrong talking to Fluxer. Please try again."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.user_message)
        if message:
            self.user_message = message


class FluxerUnavailable(FluxerError):
    user_message = "Fluxer didn't respond. Please try again in a moment."


class FluxerAuthError(FluxerError):
    user_message = "Fluxer didn't accept the login. Please try signing in again."


class InvalidBotToken(FluxerError):
    user_message = (
        "Fluxer rejected this bot token. Check that you copied the whole token and that it hasn't been reset."
    )


# --------------------------------------------------------------------------- data
@dataclass(frozen=True)
class FluxerUser:
    id: str
    username: str
    global_name: str | None
    avatar_hash: str | None


@dataclass(frozen=True)
class BotIdentity:
    application_id: str
    application_name: str
    bot_user_id: str
    bot_username: str
    bot_avatar_hash: str | None
    owner_id: str | None
    owner_name: str | None


# --------------------------------------------------------------------------- helpers
def avatar_url(settings: Settings, user_id: str, avatar_hash: str | None) -> str:
    """Public avatar URL (same scheme fluxer.py uses)."""
    if avatar_hash:
        ext = "gif" if avatar_hash.startswith("a_") else "png"
        return f"{settings.fluxer_media_url}/avatars/{user_id}/{avatar_hash}.{ext}"
    try:
        index = int(user_id) % 6
    except ValueError:
        index = 0
    return f"{settings.fluxer_static_url}/avatars/{index}.png"


def build_authorize_url(settings: Settings, *, redirect_uri: str, state: str, code_challenge: str | None) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.fluxer_client_id or "",
        "redirect_uri": redirect_uri,
        "scope": settings.fluxer_oauth_scope,
        "state": state,
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    sep = "&" if "?" in settings.fluxer_oauth_authorize_url else "?"
    return f"{settings.fluxer_oauth_authorize_url}{sep}{urlencode(params)}"


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _str(value: Any, limit: int = 128) -> str:
    return str(value)[:limit] if value is not None else ""


# --------------------------------------------------------------------------- OAuth
async def exchange_code(
    settings: Settings,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str | None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Trade the authorisation code for an access token (returned; never stored)."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings.fluxer_client_id or "",
        "client_secret": settings.fluxer_client_secret or "",
    }
    if code_verifier:
        form["code_verifier"] = code_verifier
    async with _client(client) as http:
        try:
            resp = await http.post(settings.fluxer_oauth_token_url, data=form, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            log.warning("token exchange failed: %s", type(exc).__name__)
            raise FluxerUnavailable() from exc
    if resp.status_code >= 500:
        raise FluxerUnavailable()
    data = _json(resp)
    token = data.get("access_token")
    if resp.status_code != 200 or not isinstance(token, str) or not token:
        log.warning("token exchange rejected: status=%s error=%s", resp.status_code, _str(data.get("error"), 64))
        raise FluxerAuthError()
    return token


async def fetch_userinfo(
    settings: Settings, access_token: str, *, client: httpx.AsyncClient | None = None
) -> FluxerUser:
    async with _client(client) as http:
        try:
            resp = await http.get(
                settings.fluxer_oauth_userinfo_url,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            log.warning("userinfo failed: %s", type(exc).__name__)
            raise FluxerUnavailable() from exc
    if resp.status_code >= 500:
        raise FluxerUnavailable()
    data = _json(resp)
    user_id = data.get("id") or data.get("sub")
    username = data.get("username")
    if resp.status_code != 200 or not user_id or not isinstance(username, str):
        raise FluxerAuthError()
    return FluxerUser(
        id=_str(user_id, 32),
        username=_str(username, 64),
        global_name=_str(data.get("global_name"), 128) or None,
        avatar_hash=_str(data.get("avatar"), 64) or None,
    )


async def revoke_token(settings: Settings, access_token: str, *, client: httpx.AsyncClient | None = None) -> None:
    """Best effort: we only needed the token to read the user's identity."""
    form = {
        "token": access_token,
        "token_type_hint": "access_token",
        "client_id": settings.fluxer_client_id or "",
        "client_secret": settings.fluxer_client_secret or "",
    }
    try:
        async with _client(client) as http:
            await http.post(settings.fluxer_oauth_revoke_url, data=form)
    except httpx.HTTPError:
        pass


# --------------------------------------------------------------------------- bot tokens
async def verify_bot_token(
    settings: Settings, bot_token: str, *, client: httpx.AsyncClient | None = None
) -> BotIdentity:
    """Ask Fluxer which application/bot a token belongs to (also proves the token is valid)."""
    url = f"{settings.fluxer_api_url}/applications/@me"
    async with _client(client) as http:
        try:
            resp = await http.get(
                url,
                headers={"Authorization": f"Bot {bot_token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            log.warning("bot token check failed: %s", type(exc).__name__)
            raise FluxerUnavailable() from exc
    if resp.status_code in (401, 403):
        raise InvalidBotToken()
    if resp.status_code == 429:
        raise FluxerError("Fluxer is rate-limiting requests right now. Wait a few seconds and try again.")
    if resp.status_code >= 500:
        raise FluxerUnavailable()
    data = _json(resp)
    bot = data.get("bot") if isinstance(data.get("bot"), dict) else None
    if resp.status_code != 200 or not data.get("id") or not bot or not bot.get("id"):
        raise InvalidBotToken(
            "That token is valid but isn't a bot token. Open your application in Fluxer and copy the *bot* token."
        )
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    return BotIdentity(
        application_id=_str(data.get("id"), 32),
        application_name=_str(data.get("name"), 128),
        bot_user_id=_str(bot.get("id"), 32),
        bot_username=_str(bot.get("username"), 64),
        bot_avatar_hash=_str(bot.get("avatar"), 64) or None,
        owner_id=_str(owner.get("id"), 32) or None,
        owner_name=(_str(owner.get("global_name"), 128) or _str(owner.get("username"), 64)) or None,
    )


# --------------------------------------------------------------------------- plumbing
class _client:
    """Use the injected client (tests) or open a short-lived one (serverless friendly)."""

    def __init__(self, client: httpx.AsyncClient | None) -> None:
        self._given = client
        self._own: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        if self._given is not None:
            return self._given
        self._own = httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}, follow_redirects=False)
        return self._own

    async def __aexit__(self, *exc: object) -> None:
        if self._own is not None:
            await self._own.aclose()
