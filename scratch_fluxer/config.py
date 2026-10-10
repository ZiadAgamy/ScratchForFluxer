"""Environment-driven settings plus a configuration self-check.

Everything the site needs is configured through environment variables so the
same code runs locally (``.env``) and on Vercel (Project Settings → Environment
Variables). See ``.env.example`` for the full list.
"""

from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
DATA_DIR = BASE_DIR / "data"

#: True when running as a Vercel Function (Vercel sets VERCEL=1).
ON_VERCEL = bool(os.environ.get("VERCEL"))

SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14 days
MIN_SECRET_LENGTH = 32
MIN_UID_BASE = 1000  # the first user ids; the ones below belong to the system
MAX_UID_BASE = 4_000_000_000
#: The community server of this project on Fluxer. COMMUNITY_URL= (empty) hides the link everywhere.
DEFAULT_COMMUNITY_URL = "https://fluxer.gg/oHy1OzcO"
#: Where Fluxer sends people back to after they log in, relative to the site's address.
CALLBACK_PATH = "/auth/callback"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

_dev_secret_lock = threading.Lock()
_dev_secret_cache: str | None = None


@dataclass(frozen=True)
class Problem:
    """One thing that is wrong with the current configuration."""

    key: str
    message: str
    fatal: bool = True


def web_link(value: str | None, *, allow_http_and_mail: bool = False) -> str | None:
    """``value`` if it is safe to put in an ``href``, otherwise ``None``.

    Only ``https://`` links (and, with ``allow_http_and_mail``, ``http://`` and ``mailto:``) are accepted: never
    ``javascript:``, ``data:`` or a link without a scheme. Spaces, control characters, quotes and backslashes are
    refused too, so a value can't break out of the attribute it is printed in or be read differently by a browser.
    """
    if not value:
        return None
    if any(ch.isspace() or ord(ch) < 32 or ch in '"<>\\' for ch in value):
        return None
    lowered = value.lower()
    if lowered.startswith("mailto:"):
        return value if allow_http_and_mail and len(value) > len("mailto:") else None
    if lowered.startswith("https://") or (allow_http_and_mail and lowered.startswith("http://")):
        try:
            return value if urlsplit(value).hostname else None
        except ValueError:
            return None
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- general -----------------------------------------------------------
    app_name: str = "Scratch For Fluxer"
    app_env: Literal["development", "production", "test"] = "development"
    #: Used to sign sessions and to derive the key that encrypts bot tokens.
    secret_key: str | None = None
    #: Comma-separated older keys that can still *decrypt* (for key rotation).
    secret_key_previous: str = ""
    #: Public origin of the site, e.g. https://scratch-for-fluxer.vercel.app
    app_base_url: str | None = None
    #: Set to 1 when behind a reverse proxy that adds the visitor's address to X-Forwarded-For (not needed on Vercel).
    trust_proxy: bool = False
    #: How many proxies in a row add an entry to X-Forwarded-For (a CDN in front of your load balancer is 2).
    trusted_proxy_hops: int = Field(1, ge=1, le=10)
    #: Where people can reach the person running this site (shown on the privacy page): https://..., http://... or mailto:...
    contact_url: str | None = None
    #: A community server for questions and ideas (linked in the footer, on the home page and on the privacy page).
    community_url: str | None = DEFAULT_COMMUNITY_URL

    # --- database ------------------------------------------------------------
    database_url: str | None = None
    postgres_url: str | None = None  # set by the Vercel/Neon integration

    # --- Fluxer --------------------------------------------------------------
    fluxer_client_id: str | None = None
    fluxer_client_secret: str | None = None
    fluxer_oauth_authorize_url: str = "https://api.fluxer.app/v1/oauth2/authorize"
    fluxer_oauth_token_url: str = "https://api.fluxer.app/v1/oauth2/token"  # noqa: S105 - a URL, not a secret
    fluxer_oauth_userinfo_url: str = "https://api.fluxer.app/v1/oauth2/userinfo"
    fluxer_oauth_revoke_url: str = "https://api.fluxer.app/v1/oauth2/token/revoke"
    fluxer_oauth_scope: str = "identify"
    fluxer_oauth_pkce: bool = True
    fluxer_api_url: str = "https://api.fluxer.app/v1"
    fluxer_web_url: str = "https://web.fluxer.app"
    fluxer_media_url: str = "https://fluxerusercontent.com"
    fluxer_static_url: str = "https://fluxerstatic.com"

    # --- bot runner ------------------------------------------------------------
    #: auto = remote when RUNNER_URL is set, local when not on Vercel, else off.
    runner_mode: Literal["auto", "local", "remote", "off"] = "auto"
    runner_url: str | None = None
    runner_secret: str | None = None
    runner_data_dir: str | None = None
    runner_max_concurrent: int = 8
    runner_max_per_user: int = 2
    runner_max_run_seconds: int = 6 * 60 * 60
    runner_memory_mb: int = 384
    #: When the runner is started as root, each running bot gets its own user id (this number plus a small slot number).
    runner_uid_base: int | None = None

    # --- limits ----------------------------------------------------------------
    max_bots_per_user: int = 10
    max_assets_per_bot: int = 40
    max_asset_bytes: int = 2 * 1024 * 1024
    max_assets_total_bytes: int = 8 * 1024 * 1024
    max_workspace_bytes: int = 1024 * 1024

    # ------------------------------------------------------------------ validators
    @field_validator(
        "secret_key",
        "app_base_url",
        "contact_url",
        "community_url",
        "database_url",
        "postgres_url",
        "fluxer_client_id",
        "fluxer_client_secret",
        "runner_url",
        "runner_secret",
        "runner_data_dir",
        "runner_uid_base",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("app_base_url", "runner_url", mode="after")
    @classmethod
    def _strip_slash(cls, value: str | None) -> str | None:
        return value.rstrip("/") if value else value

    @field_validator("runner_uid_base", mode="after")
    @classmethod
    def _uid_base_is_not_root(cls, value: int | None) -> int | None:
        """Bots get this number as their user id. A typo such as 0 would run them as root, so it is refused."""
        if value is not None and not MIN_UID_BASE <= value <= MAX_UID_BASE:
            raise ValueError(
                f"RUNNER_UID_BASE must be a number from {MIN_UID_BASE} to {MAX_UID_BASE} "
                "(a user id no real user has), or empty. Never 0: that is root."
            )
        return value

    @field_validator("contact_url", mode="after")
    @classmethod
    def _contact_scheme(cls, value: str | None) -> str | None:
        """It ends up in an href, so only ever allow web and mail links (never javascript: and friends)."""
        return web_link(value, allow_http_and_mail=True)

    @field_validator("community_url", mode="after")
    @classmethod
    def _community_scheme(cls, value: str | None) -> str | None:
        """It ends up in an href too, and leads people away from the site, so only an https:// link is kept."""
        return web_link(value)

    @field_validator("fluxer_api_url", "fluxer_web_url", "fluxer_media_url", "fluxer_static_url", mode="after")
    @classmethod
    def _strip_slash_required(cls, value: str) -> str:
        return value.rstrip("/")

    # ------------------------------------------------------------------ derived
    @property
    def is_production(self) -> bool:
        return self.app_env == "production" or ON_VERCEL

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    @property
    def public_base_url(self) -> str | None:
        """The site's public origin, or None (then derived from the request in dev)."""
        if self.app_base_url:
            return self.app_base_url
        prod_host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
        if prod_host:
            return f"https://{prod_host.strip().rstrip('/')}"
        return None

    @property
    def cookie_secure(self) -> bool:
        base = self.public_base_url
        return self.is_production or bool(base and base.startswith("https://"))

    @property
    def effective_database_url(self) -> str:
        raw = self.database_url or self.postgres_url
        if not raw:
            return f"sqlite:///{DATA_DIR / 'sff.db'}"
        return normalize_database_url(raw)

    @property
    def database_is_ephemeral(self) -> bool:
        return self.effective_database_url.startswith("sqlite") and ON_VERCEL

    @property
    def effective_secret(self) -> str:
        if self.secret_key:
            return self.secret_key
        if self.is_production:
            raise RuntimeError("SECRET_KEY is not set")
        return _load_dev_secret()

    @property
    def previous_secrets(self) -> list[str]:
        return [s.strip() for s in self.secret_key_previous.split(",") if s.strip()]

    @property
    def runner_effective_mode(self) -> Literal["local", "remote", "off"]:
        mode = self.runner_mode
        if mode == "auto":
            if not self.runner_url:
                return "off" if ON_VERCEL else "local"
            mode = "remote"
        if mode == "remote":
            return "off" if self.runner_setup_problem() else "remote"
        if mode == "local" and ON_VERCEL:
            return "off"  # a function that lasts a few seconds can't keep a bot connected
        return mode  # type: ignore[return-value]

    def runner_setup_problem(self) -> str | None:
        """What is wrong with the settings of a remote runner (``None`` if they are fine, or none is wanted)."""
        wanted = self.runner_mode == "remote" or (self.runner_mode == "auto" and bool(self.runner_url))
        if not wanted:
            return None
        if not self.runner_url:
            return "RUNNER_MODE=remote needs RUNNER_URL, the address of your runner."
        parts = urlsplit(self.runner_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return "RUNNER_URL must be a web address such as https://runner.example.com."
        if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS:
            return "RUNNER_URL must start with https:// because bot tokens travel to the runner."
        if not self.runner_secret or len(self.runner_secret) < MIN_SECRET_LENGTH:
            return (
                f"RUNNER_SECRET must be a long random string (at least {MIN_SECRET_LENGTH} characters), "
                "the same on the site and on the runner."
            )
        return None

    @property
    def runner_data_path(self) -> Path:
        return Path(self.runner_data_dir) if self.runner_data_dir else DATA_DIR / "runs"

    @property
    def img_src_origins(self) -> list[str]:
        return [self.fluxer_media_url, self.fluxer_static_url]

    # ------------------------------------------------------------------ diagnostics
    def problems(self) -> list[Problem]:
        """Things that must be fixed before the site can work (production only)."""
        found: list[Problem] = []
        if not self.is_production:
            return found

        if not self.secret_key or len(self.secret_key) < MIN_SECRET_LENGTH:
            found.append(
                Problem(
                    "SECRET_KEY",
                    f"Set SECRET_KEY to a long random string (at least {MIN_SECRET_LENGTH} characters). "
                    'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"',
                )
            )
        if not self.fluxer_client_id:
            found.append(Problem("FLUXER_CLIENT_ID", "Set FLUXER_CLIENT_ID (your Fluxer application's client ID)."))
        if not self.fluxer_client_secret:
            found.append(
                Problem("FLUXER_CLIENT_SECRET", "Set FLUXER_CLIENT_SECRET (your Fluxer application's client secret).")
            )
        if not self.public_base_url:
            found.append(
                Problem(
                    "APP_BASE_URL",
                    "Set APP_BASE_URL to your site's public address, e.g. https://your-project.vercel.app "
                    "(it must match the redirect URI registered in your Fluxer application).",
                )
            )
        bad_address = self.base_url_problem()
        if bad_address:
            found.append(bad_address)
        if self.database_is_ephemeral:
            found.append(
                Problem(
                    "DATABASE_URL",
                    "Set DATABASE_URL to a Postgres connection string (for example the Neon integration "
                    "from the Vercel Marketplace). SQLite files do not persist on Vercel.",
                )
            )
        problem = self.runner_setup_problem()
        if problem:
            found.append(Problem("RUNNER_URL", problem, fatal=False))
        if ON_VERCEL and self.runner_mode == "local":
            found.append(
                Problem(
                    "RUNNER_MODE",
                    "RUNNER_MODE=local can't work on Vercel. Run the runner somewhere else and set RUNNER_URL.",
                    fatal=False,
                )
            )
        return found

    def fatal_problems(self) -> list[Problem]:
        return [p for p in self.problems() if p.fatal]

    def base_url_problem(self) -> Problem | None:
        """What is wrong with the address of the site (``None`` if it is fine, or not known yet)."""
        base = self.public_base_url
        if not base:
            return None
        try:
            parts = urlsplit(base)
            host = parts.hostname
        except ValueError:
            host, parts = None, urlsplit("")
        if parts.scheme not in ("http", "https") or not host:
            return Problem(
                "APP_BASE_URL", "APP_BASE_URL must be a web address such as https://your-project.vercel.app."
            )
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            return Problem(
                "APP_BASE_URL",
                "APP_BASE_URL must be only the address of the site, with nothing after the host name "
                "(for example https://your-project.vercel.app).",
            )
        if parts.scheme == "http" and host not in LOCAL_HOSTS and self.is_production:
            return Problem(
                "APP_BASE_URL",
                "APP_BASE_URL should start with https://. On a real site the login cookies are only sent over https, "
                "so logging in can't work over http.",
                fatal=False,
            )
        return None

    def warnings(self) -> list[Problem]:
        """Things worth a look that never stop the site. They are logged at start-up and shown by
        ``python -m scratch_fluxer check``. (Problems of a production site are in :meth:`problems`.)"""
        found: list[Problem] = []
        if self.is_test or self.is_production:
            return found
        if self.secret_key and len(self.secret_key) < MIN_SECRET_LENGTH:
            found.append(
                Problem(
                    "SECRET_KEY",
                    f"SECRET_KEY is shorter than {MIN_SECRET_LENGTH} characters. Use a long random string.",
                    fatal=False,
                )
            )
        if (self.public_base_url or "").startswith("https://"):
            found.append(
                Problem(
                    "APP_ENV",
                    "The site has a public https address but APP_ENV is not 'production'. Set APP_ENV=production, "
                    "so that the site refuses to start without its secrets instead of making up a key of its own.",
                    fatal=False,
                )
            )
        return found

    def oauth_redirect_uri(self, request_base: str | None = None) -> str:
        base = self.public_base_url or (request_base.rstrip("/") if request_base else None)
        if not base:
            raise RuntimeError("APP_BASE_URL is not configured")
        return f"{base}{CALLBACK_PATH}"


def normalize_database_url(url: str) -> str:
    """Accept postgres:// and postgresql:// URLs (Neon, Vercel, Heroku style) and pick psycopg 3."""
    url = url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _load_dev_secret() -> str:
    """Stable per-checkout secret for development so restarts keep sessions + stored tokens working."""
    global _dev_secret_cache
    with _dev_secret_lock:
        if _dev_secret_cache:
            return _dev_secret_cache
        path = DATA_DIR / ".dev-secret"
        try:
            if path.exists():
                value = path.read_text().strip()
                if len(value) >= MIN_SECRET_LENGTH:
                    _dev_secret_cache = value
                    return value
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            value = secrets.token_urlsafe(48)
            path.write_text(value)
            try:
                path.chmod(0o600)
            except OSError:
                pass
            _dev_secret_cache = value
            return value
        except OSError:
            _dev_secret_cache = secrets.token_urlsafe(48)  # read-only disk: ephemeral secret
            return _dev_secret_cache


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests after changing environment variables."""
    global _dev_secret_cache
    get_settings.cache_clear()
    _dev_secret_cache = None
