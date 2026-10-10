"""Request-scoped helpers: settings, current user, CSRF protection, templates."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .config import PACKAGE_DIR, PUBLIC_DIR, SESSION_MAX_AGE, Settings, get_settings
from .db import db_run
from .fluxer_api import avatar_url
from .models import User
from .security import (
    SessionCodec,
    TokenVault,
    client_ip,
    csrf_matches,
    is_same_origin,
    limiter,
)

SESSION_COOKIE = "sff_session"
OAUTH_COOKIE = "sff_oauth"


# --------------------------------------------------------------------------- singletons
@lru_cache(maxsize=1)
def get_codec() -> SessionCodec:
    return SessionCodec(get_settings().effective_secret)


@lru_cache(maxsize=1)
def get_vault() -> TokenVault:
    s = get_settings()
    return TokenVault(s.effective_secret, s.previous_secrets)


def reset_singletons() -> None:
    get_codec.cache_clear()
    get_vault.cache_clear()
    _asset_version.cache_clear()


# --------------------------------------------------------------------------- current user
@dataclass(frozen=True)
class AuthUser:
    id: str
    fluxer_id: str
    username: str
    display_name: str
    avatar_url: str
    csrf: str


class LoginRequired(Exception):
    """Raised by :func:`require_user`; turned into a redirect (pages) or 401 (API)."""


def _load_user(db: Session, user_id: str, epoch: int) -> dict[str, Any] | None:
    user = db.get(User, user_id)
    if user is None or user.session_epoch != epoch:
        return None
    return {
        "id": user.id,
        "fluxer_id": user.fluxer_id,
        "username": user.username,
        "global_name": user.global_name,
        "avatar_hash": user.avatar_hash,
    }


async def current_user(request: Request) -> AuthUser | None:
    cached = getattr(request.state, "auth_user", False)
    if cached is not False:
        return cached
    user: AuthUser | None = None
    data = get_codec().load_session(request.cookies.get(SESSION_COOKIE), SESSION_MAX_AGE)
    if data and isinstance(data.get("u"), str) and isinstance(data.get("c"), str):
        row = await db_run(_load_user, data["u"], int(data.get("e", 0)))
        if row:
            settings = get_settings()
            user = AuthUser(
                id=row["id"],
                fluxer_id=row["fluxer_id"],
                username=row["username"],
                display_name=row["global_name"] or row["username"],
                avatar_url=avatar_url(settings, row["fluxer_id"], row["avatar_hash"]),
                csrf=data["c"],
            )
    request.state.auth_user = user
    return user


async def peek_user(request: Request) -> AuthUser | None:
    """Like :func:`current_user` but never raises. Error pages must always be able to render."""
    try:
        return await current_user(request)
    except Exception:  # noqa: BLE001 - e.g. the database is down; show the page as logged out
        request.state.auth_user = None
        return None


async def require_user(request: Request) -> AuthUser:
    user = await current_user(request)
    if user is None:
        raise LoginRequired()
    return user


async def require_csrf(request: Request, user: AuthUser = Depends(require_user)) -> AuthUser:
    """For state-changing API calls: valid session + matching X-CSRF-Token + same-origin."""
    settings = get_settings()
    if not is_same_origin(request, settings.public_base_url):
        raise HTTPException(status_code=403, detail="Cross-site request blocked.")
    if not csrf_matches(user.csrf, request.headers.get("x-csrf-token")):
        raise HTTPException(status_code=403, detail="Your session expired. Refresh the page and try again.")
    return user


def rate_limit(name: str, limit: int, window: float):
    """Dependency factory: ``Depends(rate_limit("verify", 10, 60))`` (per client IP)."""

    async def _dep(request: Request) -> None:
        settings = get_settings()
        key = f"{name}:{client_ip(request, settings)}"
        if not limiter.allow(key, limit, window):
            raise HTTPException(status_code=429, detail="Too many requests. Please slow down a little.")

    return _dep


def user_rate_limit(name: str, limit: int, window: float):
    """Like :func:`rate_limit` but keyed by the logged-in user (falls back to IP)."""

    async def _dep(request: Request, user: AuthUser = Depends(require_user)) -> None:
        if not limiter.allow(f"{name}:{user.id}", limit, window):
            raise HTTPException(status_code=429, detail="Too many requests. Please slow down a little.")

    return _dep


# --------------------------------------------------------------------------- request bodies
async def read_body(request: Request, max_bytes: int, too_large: str = "That request is too large.") -> bytes:
    """The request body, never buffering more than ``max_bytes`` (a longer one is a 413)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(status_code=413, detail=too_large)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=too_large)
        chunks.append(chunk)
    return b"".join(chunks)


def require_json_content(request: Request) -> None:
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="Send the request as JSON.")


def _not_a_json_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in JSON")


def parse_json(body: bytes) -> Any:
    """Strict JSON: no NaN or Infinity, and brackets nested too deeply to read are an error, not a crash."""
    try:
        return json.loads(body or b"null", parse_constant=_not_a_json_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(status_code=400, detail="That request wasn't valid JSON.") from None


async def read_json(request: Request, max_bytes: int = 32_768) -> dict[str, Any]:
    """Read a small JSON object from the request body without ever buffering more than ``max_bytes``."""
    require_json_content(request)
    data = parse_json(await read_body(request, max_bytes))
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object.")
    return data


# --------------------------------------------------------------------------- templates
def time_ago(value: datetime | None, now: datetime | None = None) -> str:
    """'just now', '5 minutes ago', '3 days ago', or a date for anything older than a month."""
    if value is None:
        return "never"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    seconds = ((now or datetime.now(timezone.utc)) - value).total_seconds()
    if seconds < 45:
        return "just now"
    for limit, unit, size in ((3600, "minute", 60), (86400, "hour", 3600), (86400 * 30, "day", 86400)):
        if seconds < limit:
            count = max(1, round(seconds / size))
            return f"{count} {unit}{'' if count == 1 else 's'} ago"
    return f"{value.day} {value.strftime('%b %Y')}"


@lru_cache(maxsize=1)
def _asset_version() -> str:
    sha = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")
    return sha[:8] if sha else ""


def static_url(path: str) -> str:
    """``/static/...`` URL with a cache-busting query string."""
    version = _asset_version()
    if not version:
        file = PUBLIC_DIR / "static" / path
        try:
            version = hashlib.md5(str(int(file.stat().st_mtime)).encode()).hexdigest()[:8]  # noqa: S324 - cache key only
        except OSError:
            version = "0"
    return f"/static/{path}?v={version}"


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
    templates.env.globals["static"] = static_url
    templates.env.filters["time_ago"] = time_ago
    templates.env.autoescape = True
    return templates


def render(request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200, **kw: Any):
    settings = get_settings()
    # Templates only ever see this small, non-secret subset of the settings.
    ctx: dict[str, Any] = {
        "app_name": settings.app_name,
        "fluxer_web_url": settings.fluxer_web_url,
        "community_url": settings.community_url,
        "user": getattr(request.state, "auth_user", None) or None,
    }
    ctx.update(context or {})
    ctx.update(kw)
    return get_templates().TemplateResponse(request, name, ctx, status_code=status_code)


def get_settings_dep() -> Settings:
    return get_settings()
