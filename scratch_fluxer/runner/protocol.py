"""What the website and the runner say to each other: the request that starts a bot, the errors, and the signature.

The website never runs a bot itself on Vercel. It asks a runner (a small separate service, or a runner built into the
same process while developing) to start the bot's file, and polls it for the messages the bot writes. Every call from
the website to a remote runner is signed with a shared secret, so only the website can start, stop or read a bot.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .. import assets as assetlib
from ..config import Settings

BOT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_CODE_CHARS = 400_000  # the compiler refuses longer files
MAX_TOKEN_CHARS = 300

SIGNATURE_HEADER = "X-SFF-Signature"
TIMESTAMP_HEADER = "X-SFF-Timestamp"
NONCE_HEADER = "X-SFF-Nonce"
MAX_SKEW_SECONDS = 60
MIN_SECRET_CHARS = 32


# --------------------------------------------------------------------------------------------- errors
class RunnerError(Exception):
    """Something the person should be told. ``message`` is written for them, ``status`` is the HTTP status to use."""

    status = 500
    kind = "error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Refused(RunnerError):
    """The request itself is not something that can be run (unsafe code, a file that is too big...)."""

    status = 422
    kind = "refused"


class TooMany(RunnerError):
    """This person already runs as many bots as they are allowed to at the same time."""

    status = 409
    kind = "too_many"


class Busy(RunnerError):
    """The runner has no room for another bot right now."""

    status = 503
    kind = "busy"


class Unavailable(RunnerError):
    """Running bots is switched off, not set up, or the runner can't be reached."""

    status = 503
    kind = "unavailable"


class NotAllowed(RunnerError):
    """The signature on a call was missing, wrong or too old."""

    status = 401
    kind = "not_allowed"


ERRORS: dict[str, type[RunnerError]] = {
    cls.kind: cls for cls in (RunnerError, Refused, TooMany, Busy, Unavailable, NotAllowed)
}


# --------------------------------------------------------------------------------------------- the start request
@dataclass
class StartRequest:
    """Everything the runner needs to start one bot. ``token`` is the bot's token in the clear (it goes to the process
    through its environment and nowhere else); ``files`` are the uploaded files the code uses."""

    bot_id: str
    owner: str
    code: str
    token: str
    files: list[tuple[str, bytes]] = field(default_factory=list)

    def __repr__(self) -> str:  # never let the token end up in a log line or a traceback
        return f"StartRequest(bot_id={self.bot_id!r}, files={len(self.files)})"

    def to_json(self) -> bytes:
        body = {
            "bot_id": self.bot_id,
            "owner": self.owner,
            "code": self.code,
            "token": self.token,
            "files": [{"name": name, "data": base64.b64encode(data).decode("ascii")} for name, data in self.files],
        }
        return json.dumps(body, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_json(cls, raw: Any) -> StartRequest:
        """Read the body of a start call. Anything unexpected is a ``Refused``, never a crash."""
        if not isinstance(raw, dict):
            raise Refused("That request wasn't valid.")
        bot_id, owner, code, token, files = (raw.get(k) for k in ("bot_id", "owner", "code", "token", "files"))
        if not all(isinstance(value, str) for value in (bot_id, owner, code, token)) or not isinstance(files, list):
            raise Refused("That request wasn't valid.")
        decoded: list[tuple[str, bytes]] = []
        for item in files:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("data"), str)
            ):
                raise Refused("That request wasn't valid.")
            try:
                data = base64.b64decode(item["data"], validate=True)
            except (binascii.Error, ValueError):
                raise Refused("That request wasn't valid.") from None
            decoded.append((item["name"], data))
        return cls(bot_id=bot_id, owner=owner, code=code, token=token, files=decoded)


def validate_start(request: StartRequest, settings: Settings) -> None:
    """The checks that don't need the code to be read: shapes, names and sizes. Raises ``Refused``."""
    if not BOT_ID_RE.fullmatch(request.bot_id) or not BOT_ID_RE.fullmatch(request.owner):
        raise Refused("That request wasn't valid.")
    if not request.code.strip() or len(request.code) > MAX_CODE_CHARS:
        raise Refused("There is no code to run, or it is too long.")
    token = request.token
    if not token or len(token) > MAX_TOKEN_CHARS or any(ch.isspace() or ord(ch) < 32 for ch in token):
        raise Refused("The saved bot token can't be used. Paste the token again on the My bots page.")
    if len(request.files) > settings.max_assets_per_bot:
        raise Refused("Too many files.")
    seen: set[str] = set()
    total = 0
    for name, data in request.files:
        if not assetlib.is_safe_name(name) or name in seen:
            raise Refused("A file has a name that can't be used.")
        if len(data) > settings.max_asset_bytes:
            raise Refused(f"The file {name} is too big.")
        seen.add(name)
        total += len(data)
    if total > settings.max_assets_total_bytes:
        raise Refused("The files are too big in total.")


# --------------------------------------------------------------------------------------------- signing
def signature(secret: str, method: str, target: str, body: bytes, timestamp: str, nonce: str) -> str:
    """HMAC-SHA256 over everything that matters about a call: when, which one, where, and what it carries."""
    message = "\n".join(("SFF1", timestamp, nonce, method.upper(), target, hashlib.sha256(body).hexdigest()))
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_headers(secret: str, method: str, target: str, body: bytes = b"", now: float | None = None) -> dict[str, str]:
    """The headers a call to the runner carries. ``target`` is the path plus the query string, exactly as sent."""
    timestamp = str(int(now if now is not None else time.time()))
    nonce = secrets.token_hex(16)
    return {
        TIMESTAMP_HEADER: timestamp,
        NONCE_HEADER: nonce,
        SIGNATURE_HEADER: signature(secret, method, target, body, timestamp, nonce),
    }


class Verifier:
    """Checks the signature on calls to the runner. A call is accepted once: a copy of it is refused."""

    def __init__(self, secret: str) -> None:
        self._secret = secret
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(
        self, method: str, target: str, body: bytes, headers: Mapping[str, str], now: float | None = None
    ) -> None:
        moment = now if now is not None else time.time()
        timestamp = headers.get(TIMESTAMP_HEADER, "")
        nonce = headers.get(NONCE_HEADER, "")
        supplied = headers.get(SIGNATURE_HEADER, "")
        if not (timestamp.isdigit() and 8 <= len(nonce) <= 64 and 32 <= len(supplied) <= 128):
            raise NotAllowed("This call isn't signed.")
        if abs(moment - int(timestamp)) > MAX_SKEW_SECONDS:
            raise NotAllowed("This call is too old, or the clocks of the site and the runner disagree.")
        expected = signature(self._secret, method, target, body, timestamp, nonce)
        if not hmac.compare_digest(expected.encode("ascii"), supplied.encode("ascii", "replace")):
            raise NotAllowed("The signature doesn't match. The site and the runner need the same RUNNER_SECRET.")
        with self._lock:
            if nonce in self._seen:
                raise NotAllowed("This call was already made.")
            self._seen[nonce] = moment + 2 * MAX_SKEW_SECONDS
            if len(self._seen) > 5000:
                self._seen = {key: until for key, until in self._seen.items() if until > moment}
