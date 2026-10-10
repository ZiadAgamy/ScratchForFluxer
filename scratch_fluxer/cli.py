"""The commands of the project.

    python -m scratch_fluxer              run the site on this computer (the same as "serve")
    python -m scratch_fluxer check        look over the settings and print the address to give your Fluxer application
    python -m scratch_fluxer reencrypt    after changing SECRET_KEY: save every bot token again under the new key

Nothing here prints a secret: not SECRET_KEY, not the client secret, not the database address and not a bot token.
The messages are plain ASCII, so they read the same in any terminal.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import CALLBACK_PATH, MIN_SECRET_LENGTH, Settings, get_settings
from .models import Bot
from .security import InvalidToken, TokenVault

LOCAL_SITE = "http://localhost:8000"
Say = Callable[[str], None]


# --------------------------------------------------------------------------- check
@dataclass(frozen=True)
class RedirectAddress:
    """An address the Fluxer application has to list, and when it is needed."""

    uri: str
    when: str


@dataclass(frozen=True)
class Finding:
    """One line of the report. ``kind`` is ``ok`` (fine), ``fix`` (login or the site won't work) or ``note``."""

    kind: str
    text: str


def redirect_addresses(settings: Settings) -> list[RedirectAddress]:
    """Where Fluxer sends a person back to after they log in. It has to be in the application's list of redirect
    addresses, written exactly the same (the site sends the same address both times it talks to Fluxer)."""
    found: list[RedirectAddress] = []
    base = settings.public_base_url
    if base:
        found.append(RedirectAddress(base + CALLBACK_PATH, "the live site (APP_BASE_URL)"))
    local = LOCAL_SITE + CALLBACK_PATH
    if all(item.uri != local for item in found):
        found.append(RedirectAddress(local, "running the site on your own computer"))
    return found


def review(settings: Settings) -> list[Finding]:
    """What is fine, what must be fixed and what is worth knowing, in the order a person would fix it."""
    items: list[Finding] = []

    def add(kind: str, text: str) -> None:
        if all(item.text != text for item in items):
            items.append(Finding(kind, text))

    production = settings.is_production
    problems = {p.key: p for p in settings.problems()}

    # The site's address, which decides the redirect address.
    base_problem = settings.base_url_problem()
    if base_problem:
        add("fix" if base_problem.fatal else "note", base_problem.message)
    elif settings.public_base_url:
        add("ok", f"APP_BASE_URL is {settings.public_base_url}")
    elif production:
        add("fix", problems["APP_BASE_URL"].message)
    else:
        add("note", "APP_BASE_URL is not set. That is fine on your own computer; on a real site it must be set.")

    # Keys and secrets (only whether they are there, never their value).
    secret = settings.secret_key
    if secret and len(secret) >= MIN_SECRET_LENGTH:
        add("ok", "SECRET_KEY is set.")
    elif secret:
        add("fix", f"SECRET_KEY is shorter than {MIN_SECRET_LENGTH} characters. Use a long random string.")
    elif production:
        add("fix", problems["SECRET_KEY"].message)
    else:
        add("note", "SECRET_KEY is not set. A key is made for you in data/.dev-secret: fine here, not for a real site.")

    for name, value in (
        ("FLUXER_CLIENT_ID", settings.fluxer_client_id),
        ("FLUXER_CLIENT_SECRET", settings.fluxer_client_secret),
    ):
        if value:
            add("ok", f"{name} is set.")
        else:
            add(
                "fix",
                f"{name} is missing. Logging in with Fluxer can't work without it (copy it from your Fluxer application).",
            )

    # Everything else the site itself would complain about in production, and what is worth a look elsewhere.
    for problem in (*problems.values(), *settings.warnings()):
        if problem.key not in ("APP_BASE_URL", "SECRET_KEY", "FLUXER_CLIENT_ID", "FLUXER_CLIENT_SECRET"):
            add("fix" if problem.fatal else "note", problem.message)
    if settings.runner_effective_mode == "off":
        add(
            "note",
            "Running bots is switched off here (see RUNNER_URL and deploy/runner/README.md). The editor still works.",
        )
    elif settings.runner_effective_mode == "remote":
        add("ok", "Bots run on your runner service (RUNNER_URL and RUNNER_SECRET are set).")
    else:
        add("ok", "Bots run on this computer (RUNNER_MODE=local).")
        if production:
            add(
                "note",
                "On a real site, bots that run on the same computer as the site share its user and files. "
                "A separate runner keeps them apart (deploy/runner/README.md).",
            )
    if settings.community_url:
        add("ok", f"Community link: {settings.community_url}")
    else:
        add("note", "COMMUNITY_URL is empty, so no community link is shown.")
    if not settings.contact_url:
        add("note", "CONTACT_URL is not set, so the privacy page doesn't say how to reach you. It is optional.")
    if production and not settings.trust_proxy and not os.environ.get("VERCEL"):
        add(
            "note",
            "TRUST_PROXY is off. If a reverse proxy (Caddy, nginx, a load balancer) sits in front of the site, "
            "set TRUST_PROXY=1, or every visitor looks like the proxy to the rate limits.",
        )
    return items


def report(settings: Settings) -> tuple[str, int]:
    """The text ``check`` prints, and its exit code (1 when something has to be fixed)."""
    lines = ["Scratch For Fluxer - setup check", ""]
    lines.append("Add these to the redirect addresses of your Fluxer application:")
    for address in redirect_addresses(settings):
        lines.append(f"    {address.uri}")
        lines.append(f"        for {address.when}")
    if not settings.public_base_url:
        lines += [
            "    (the address of your live site is not known yet: set APP_BASE_URL, for example",
            f"     https://your-project.vercel.app, and the address to add is that + {CALLBACK_PATH})",
        ]
    lines += [
        "Copy each one exactly: same http or https, same host name, no slash at the end.",
        "",
        "Settings:",
    ]
    findings = review(settings)
    labels = {"ok": "[ok]  ", "fix": "[fix] ", "note": "[note]"}
    lines += [f"  {labels[item.kind]} {item.text}" for item in findings]
    to_fix = sum(1 for item in findings if item.kind == "fix")
    lines += ["", "Everything needed is in place." if not to_fix else f"{to_fix} thing(s) to fix before it works."]
    return "\n".join(lines), 1 if to_fix else 0


def run_check(settings: Settings, say: Say = print) -> int:
    text, code = report(settings)
    say(text)
    return code


# --------------------------------------------------------------------------- reencrypt
@dataclass
class Tally:
    current: int = 0  # already under the newest key
    moved: int = 0  # saved again under the newest key (or would be, with --dry-run)
    unreadable: int = 0  # no key that was given opens these
    replaced: int = 0  # the owner pasted a new token while this was running

    @property
    def total(self) -> int:
        return self.current + self.moved + self.unreadable + self.replaced


def reencrypt_tokens(db: Session, vault: TokenVault, *, dry_run: bool = False, page: int = 200) -> Tally:
    """Save every bot token under the vault's newest key. Safe to run again, and while the site is in use: a token is
    only rewritten if it is still the one that was read, and ``updated_at`` (it orders the list of bots) is kept."""
    tally = Tally()
    last = ""
    while True:
        rows = db.execute(select(Bot.id, Bot.token_ciphertext).where(Bot.id > last).order_by(Bot.id).limit(page)).all()
        if not rows:
            return tally
        for bot_id, ciphertext in rows:
            try:
                newer = vault.refreshed(ciphertext)
            except InvalidToken:
                tally.unreadable += 1
                continue
            if newer is None:
                tally.current += 1
            elif dry_run:
                tally.moved += 1
            else:
                done = db.execute(
                    update(Bot)
                    .where(Bot.id == bot_id, Bot.token_ciphertext == ciphertext)
                    .values(token_ciphertext=newer, updated_at=Bot.updated_at)
                )
                if done.rowcount:  # type: ignore[attr-defined]
                    tally.moved += 1
                else:
                    tally.replaced += 1
        if not dry_run:
            db.commit()
        last = rows[-1][0]


def run_reencrypt(settings: Settings, *, dry_run: bool = False, say: Say = print) -> int:
    from .db import init_db, session_scope  # needs the database driver, which the other commands don't

    try:
        vault = TokenVault(settings.effective_secret, settings.previous_secrets)
    except RuntimeError:
        say(
            "SECRET_KEY is not set. Put the NEW key in SECRET_KEY and the old one in SECRET_KEY_PREVIOUS, then run this again."
        )
        return 2
    older = len(settings.previous_secrets)
    kind = "a SQLite file" if settings.effective_database_url.startswith("sqlite") else "Postgres"
    say(f"Database: {kind}. Keys: the one in SECRET_KEY and {older} older one(s) from SECRET_KEY_PREVIOUS.")
    try:
        init_db()
        with session_scope() as db:
            tally = reencrypt_tokens(db, vault, dry_run=dry_run)
    except Exception as error:  # noqa: BLE001 - say what kind of failure, never what the error text holds
        say(f"Could not use the database ({type(error).__name__}). Check DATABASE_URL, then run this again.")
        return 2

    verb = "would be moved" if dry_run else "moved"
    say(f"Saved bot tokens: {tally.total}")
    say(f"  already under the newest key: {tally.current}")
    say(f"  {verb} to the newest key: {tally.moved}")
    say(f"  not readable with these keys: {tally.unreadable}")
    if tally.replaced:
        say(f"  replaced by their owner meanwhile: {tally.replaced} (run this again to be sure)")
    if tally.unreadable:
        say(
            "Some tokens can't be opened with the keys you gave. Add the key that saved them to SECRET_KEY_PREVIOUS "
            "and run this again. Until then keep every older key; the owners can also just paste the token again."
        )
        return 1
    if dry_run:
        say("Nothing was changed (--dry-run). Run it without --dry-run to move them.")
    elif tally.replaced:
        say("Run this once more, then SECRET_KEY_PREVIOUS can be removed.")
    else:
        say("Every token is under the newest key now. SECRET_KEY_PREVIOUS can be removed.")
    return 0


# --------------------------------------------------------------------------- serve
def run_serve() -> int:
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("scratch_fluxer.main:app", host=host, port=port, reload=True)
    return 0


# --------------------------------------------------------------------------- entry point
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scratch_fluxer",
        description="Scratch For Fluxer. With no command the site is started on this computer.",
    )
    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.add_parser("serve", help="run the site on this computer, restarting when a file changes (the default)")
    commands.add_parser(
        "check", help="look over the settings and print the redirect address for your Fluxer application"
    )
    again = commands.add_parser("reencrypt", help="after changing SECRET_KEY, save every bot token under the new key")
    again.add_argument("--dry-run", action="store_true", help="only count what would change")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "serve"
    if command == "check":
        return run_check(get_settings())
    if command == "reencrypt":
        return run_reencrypt(get_settings(), dry_run=args.dry_run)
    return run_serve()


if __name__ == "__main__":
    sys.exit(main())
