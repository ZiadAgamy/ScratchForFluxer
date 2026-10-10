"""Security primitives: token vault, signed sessions, CSRF, PKCE, rate limiting, headers.

Nothing here talks to the network or the database, so it is easy to test.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import itertools
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import ON_VERCEL, Settings

__all__ = [
    "TokenVault",
    "InvalidToken",
    "SessionCodec",
    "new_csrf",
    "csrf_matches",
    "make_pkce",
    "new_state",
    "safe_next",
    "normalize_bot_token",
    "BotTokenFormatError",
    "RateLimiter",
    "limiter",
    "client_ip",
    "security_headers",
    "is_same_origin",
]


def _derive(secret: str, purpose: bytes) -> bytes:
    """Derive an independent 32-byte key per purpose from the master secret (HKDF-SHA256)."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"scratch-for-fluxer/v1",
        info=purpose,
    ).derive(secret.encode("utf-8"))


# --------------------------------------------------------------------------- vault
class TokenVault:
    """Encrypts bot tokens at rest (Fernet = AES-128-CBC + HMAC-SHA256).

    ``previous`` keys can still decrypt, so SECRET_KEY can be rotated without losing data:
    put the new key in SECRET_KEY and the old one in SECRET_KEY_PREVIOUS.
    """

    def __init__(self, secret: str, previous: Sequence[str] = ()) -> None:
        keys = [self._fernet(s) for s in (secret, *previous) if s]
        self._fernet_multi = MultiFernet(keys)  # refuses an empty list
        self._newest = keys[0]

    @staticmethod
    def _fernet(secret: str) -> Fernet:
        return Fernet(base64.urlsafe_b64encode(_derive(secret, b"bot-token-vault")))

    @staticmethod
    def _raw(ciphertext: str) -> bytes:
        """Fernet text is plain ASCII. Anything else is not something this vault made."""
        try:
            return ciphertext.encode("ascii")
        except (UnicodeEncodeError, AttributeError):
            raise InvalidToken from None

    def encrypt(self, plaintext: str) -> str:
        return self._fernet_multi.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet_multi.decrypt(self._raw(ciphertext)).decode("utf-8")

    def rotate(self, ciphertext: str) -> str:
        """Re-encrypt with the primary key."""
        return self._fernet_multi.rotate(self._raw(ciphertext)).decode("ascii")

    def is_current(self, ciphertext: str) -> bool:
        """True when the newest key made this ciphertext, so there is nothing to re-encrypt."""
        try:
            self._newest.decrypt(self._raw(ciphertext))
        except InvalidToken:
            return False
        return True

    def refreshed(self, ciphertext: str) -> str | None:
        """The same secret under the newest key, or ``None`` when it already is. Raises ``InvalidToken`` if no key
        this vault knows can read it (that is the case to report, never to paper over)."""
        if self.is_current(ciphertext):
            return None
        return self.rotate(ciphertext)


# --------------------------------------------------------------------------- sessions
class SessionCodec:
    """Signs/verifies small cookie payloads. Stateless — the cookie *is* the session."""

    def __init__(self, secret: str) -> None:
        key = _derive(secret, b"cookie-signing").hex()
        self._session = URLSafeTimedSerializer(key, salt="sff.session.v1")
        self._oauth = URLSafeTimedSerializer(key, salt="sff.oauth.v1")

    def dump_session(self, data: dict[str, Any]) -> str:
        return self._session.dumps(data)

    def load_session(self, value: str | None, max_age: int) -> dict[str, Any] | None:
        return self._load(self._session, value, max_age)

    def dump_oauth(self, data: dict[str, Any]) -> str:
        return self._oauth.dumps(data)

    def load_oauth(self, value: str | None, max_age: int = 600) -> dict[str, Any] | None:
        return self._load(self._oauth, value, max_age)

    @staticmethod
    def _load(ser: URLSafeTimedSerializer, value: str | None, max_age: int) -> dict[str, Any] | None:
        if not value or len(value) > 2048:
            return None
        try:
            data = ser.loads(value, max_age=max_age)
        except (BadSignature, Exception):  # noqa: BLE001 - any parsing problem means "no session"
            return None
        return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- csrf / oauth helpers
def new_csrf() -> str:
    return secrets.token_urlsafe(32)


def new_state() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(expected: str | None, supplied: str | None) -> bool:
    if not expected or not supplied:
        return False
    return hmac.compare_digest(expected.encode(), supplied.encode())


def make_pkce() -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def safe_next(value: Any, default: str = "/bots") -> str:
    """Only allow same-site relative paths as post-login destinations (no open redirects)."""
    if not isinstance(value, str):
        return default
    value = value.strip()
    if not value or len(value) > 300:
        return default
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return default
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return default
    if parts.path.startswith("/auth/") or parts.path.startswith("/api/"):
        return default
    return value


def is_same_origin(request: Request, base_url: str | None) -> bool:
    """Reject cross-site state-changing requests using Fetch-Metadata / Origin headers."""
    site = request.headers.get("sec-fetch-site")
    if site and site not in ("same-origin", "none"):
        return False
    origin = request.headers.get("origin")
    if origin:
        expected = base_url or f"{request.url.scheme}://{request.headers.get('host', '')}"
        return origin.rstrip("/") == expected.rstrip("/")
    return True


# --------------------------------------------------------------------------- bot tokens
class BotTokenFormatError(ValueError):
    """Raised with a user-friendly message when pasted text is clearly not a bot token."""


_BOT_TOKEN_RE = re.compile(
    r"^[0-9]{1,25}\.[A-Za-z0-9_\-.~+/=]{8,256}$"
)  # ASCII digits only (\d matches Arabic-Indic too)


def normalize_bot_token(raw: str | None) -> str:
    """Clean up pasted text and check it looks like ``<application id>.<secret>``.

    The real check is the API call that follows; this only catches obvious mistakes
    (pasting the client secret, an OAuth code, a whole sentence...) without a network round trip.
    """
    token = (raw or "").strip().strip("\"'`").strip()
    if token.lower().startswith("bot "):
        token = token[4:].strip()
    if not token:
        raise BotTokenFormatError("Paste your bot token first.")
    if any(c.isspace() for c in token):
        raise BotTokenFormatError("The token has spaces or line breaks in it. Copy just the token itself.")
    if len(token) > 300:
        raise BotTokenFormatError("That is far too long to be a bot token.")
    if not _BOT_TOKEN_RE.match(token):
        raise BotTokenFormatError(
            "That doesn't look like a Fluxer bot token. A bot token is your application's ID, "
            "a dot, then a long secret (for example 1234567890.AbC…). "
            "Make sure you copied the bot token, not the client secret."
        )
    return token


# --------------------------------------------------------------------------- rate limiting
class RateLimiter:
    """Tiny sliding-window limiter.

    On serverless each instance has its own memory, so this is *burst* protection per
    instance, not a global quota. It is cheap insurance for login/verify/upload/run routes.
    """

    #: Above this many remembered keys, the ones that have run out are cleared (at most once a second) ...
    CLEAR_ABOVE = 10_000
    #: ... and if they are all recent, the oldest are forgotten, so a flood of different addresses can't fill the memory.
    KEEP_AT_MOST = 30_000

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._longest = 1.0  # the longest window asked for so far: a key is needed until its newest hit is older
        self._cleared_at = float("-inf")

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        cutoff = now - window
        with self._lock:
            self._longest = max(self._longest, window)
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > self.CLEAR_ABOVE and now - self._cleared_at >= 1.0:
                self._clear(now)
            return True

    def _clear(self, now: float) -> None:
        self._cleared_at = now
        cutoff = now - self._longest
        for key in [k for k, q in self._hits.items() if not q or q[-1] <= cutoff]:
            del self._hits[key]
        for key in list(itertools.islice(self._hits, max(0, len(self._hits) - self.KEEP_AT_MOST))):
            del self._hits[key]  # a dict remembers the order its keys were added in: these are the oldest

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request, settings: Settings) -> str:
    """Who is asking, as far as it can be told.

    A forwarding header is only believed where something trustworthy wrote it. On Vercel that is Vercel itself (it
    replaces what the visitor sent). Behind a proxy of your own (``TRUST_PROXY``) it is the entry that proxy added at the
    end of ``X-Forwarded-For``: everything before it was written by the visitor, and ``TRUSTED_PROXY_HOPS`` says how
    many proxies in a row add an entry. Otherwise it is the address the connection came from.
    """
    peer = (request.client.host if request.client else "unknown")[:64]
    if ON_VERCEL:
        for header in ("x-vercel-forwarded-for", "x-real-ip", "x-forwarded-for"):
            value = request.headers.get(header)
            if value:
                return value.split(",")[0].strip()[:64]
    elif settings.trust_proxy:
        entries = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
        if len(entries) >= settings.trusted_proxy_hops:
            return entries[-settings.trusted_proxy_hops][:64]
    return peer


# --------------------------------------------------------------------------- response headers
def build_csp(settings: Settings) -> str:
    img = " ".join(["'self'", "data:", "blob:", *settings.img_src_origins])
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",  # Blockly injects inline styles
            f"img-src {img}",
            "font-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def security_headers(settings: Settings) -> dict[str, str]:
    headers = {
        "Content-Security-Policy": build_csp(settings),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    if settings.cookie_secure:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers
