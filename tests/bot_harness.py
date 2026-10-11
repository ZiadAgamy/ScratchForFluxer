"""Runs the Python the compiler wrote against the real fluxer.py, with the network replaced by recorders.

``start(code, tmp_path)`` runs the generated file (``bot.run`` is replaced so nothing connects) and hands back a
``Running`` object. Tests then feed it gateway events (``await running.message("!ping")``) and look at what the bot
sent through the fake HTTP client. Messages and members are built by the real fluxer.py classes, so a block that
uses an attribute fluxer.py doesn't have fails here instead of in somebody's console.
"""

from __future__ import annotations

import itertools
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

import fluxer

_ids = itertools.count(1000)
BOT_USER = {"id": "900", "username": "testbot", "discriminator": "0", "bot": True}


def user_data(user_id: int = 200, username: str = "alice", *, bot: bool = False, global_name: str | None = None):
    return {"id": str(user_id), "username": username, "discriminator": "0", "bot": bot, "global_name": global_name}


class FakeHTTP:
    """Stands in for fluxer.py's HTTP client. Every call is recorded; a few return what Fluxer would."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.member_roles: dict[int, list[str]] = {}  # user id -> role ids that Fluxer says the person has

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)

        async def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return self._answer(name, args, kwargs)

        return call

    def _answer(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if name == "send_message":
            return {
                "id": str(next(_ids)),
                "channel_id": str(args[0]),
                "content": kwargs.get("content") or "",
                "author": BOT_USER,
                "timestamp": "2026-10-10T10:00:00+00:00",
            }
        if name == "get_channel":
            return {"id": str(args[0]), "type": 0, "guild_id": "700"}
        if name == "get_user":
            return user_data(int(args[0]), "member")
        if name == "create_dm":
            return {"id": "5555", "type": 1}
        if name == "get_guild":
            return {"id": str(args[0]), "name": "Test community"}
        if name == "get_guild_member":
            return {"user": user_data(int(args[1]), "member"), "roles": self.member_roles.get(int(args[1]), [])}
        return None

    def named(self, name: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        return [(args, kwargs) for call, args, kwargs in self.calls if call == name]


class FakeGateway:
    def __init__(self) -> None:
        self.payloads: list[Any] = []

    async def _send(self, payload: Any) -> None:
        self.payloads.append(payload)


@dataclass
class Running:
    bot: fluxer.Bot
    namespace: dict[str, Any]
    http: FakeHTTP
    gateway: FakeGateway
    token: str | None
    directory: Path
    ready_called: bool = field(default=False)

    # ---- events
    async def ready(self, guilds: list[dict[str, Any]] | None = None) -> None:
        await self.bot._dispatch("READY", {"user": BOT_USER, "guilds": guilds or []})

    async def guild(self, guild_id: int = 700, channel_id: int = 300) -> None:
        data = {
            "id": str(guild_id),
            "name": "Test community",
            "channels": [{"id": str(channel_id), "type": 0, "guild_id": str(guild_id)}],
        }
        await self.bot._dispatch("GUILD_CREATE", data)

    async def message(
        self,
        content: str,
        *,
        author: str = "alice",
        author_id: int = 200,
        is_bot: bool = False,
        channel_id: int = 300,
        global_name: str | None = None,
    ) -> None:
        data = {
            "id": str(next(_ids)),
            "channel_id": str(channel_id),
            "content": content,
            "author": user_data(author_id, author, bot=is_bot, global_name=global_name),
            "timestamp": "2026-10-10T10:00:00+00:00",
            "embeds": [],
            "attachments": [],
            "mentions": [],
        }
        await self.bot._dispatch("MESSAGE_CREATE", data)

    async def member_joins(self, name: str = "newbie", user_id: int = 300, guild_id: int = 700) -> None:
        await self.bot._dispatch(
            "GUILD_MEMBER_ADD", {"user": user_data(user_id, name), "guild_id": str(guild_id), "roles": []}
        )

    async def member_leaves(self, name: str = "oldtimer", user_id: int = 301, guild_id: int = 700) -> None:
        await self.bot._dispatch("GUILD_MEMBER_REMOVE", {"user": user_data(user_id, name), "guild_id": str(guild_id)})

    async def reaction(
        self,
        emoji: str = "✅",
        *,
        added: bool = True,
        user_id: int = 200,
        message_id: int = 555,
        guild_id: int | None = 700,
        custom_id: int | None = None,
    ) -> None:
        emoji_data = {"id": str(custom_id), "name": emoji} if custom_id else {"id": None, "name": emoji}
        data: dict[str, Any] = {
            "message_id": str(message_id),
            "channel_id": "300",
            "user_id": str(user_id),
            "emoji": emoji_data,
        }
        if guild_id:
            data["guild_id"] = str(guild_id)
        await self.bot._dispatch("MESSAGE_REACTION_ADD" if added else "MESSAGE_REACTION_REMOVE", data)

    # ---- what the bot did
    @property
    def sent(self) -> list[dict[str, Any]]:
        """Every message the bot sent: its text, whether it was a reply, its embeds and files, and the channel."""
        out = []
        for args, kwargs in self.http.named("send_message"):
            out.append(
                {
                    "content": kwargs.get("content"),
                    "reply": bool(kwargs.get("message_reference")),
                    "embeds": kwargs.get("embeds"),
                    "files": kwargs.get("files"),
                    "channel": str(args[0]),
                }
            )
        return out

    @property
    def texts(self) -> list[str | None]:
        return [item["content"] for item in self.sent]


def start(code: str, directory: Path, assets: dict[str, bytes] | None = None) -> Running:
    """Runs the generated file as ``main.py`` in ``directory`` (with its ``assets`` folder) up to ``bot.run``."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "main.py").write_text(code, encoding="utf-8")
    for name, data in (assets or {}).items():
        (directory / "assets").mkdir(exist_ok=True)
        (directory / "assets" / name).write_bytes(data)

    started: list[tuple[fluxer.Bot, str]] = []

    def fake_run(self: fluxer.Bot, token: str) -> None:
        started.append((self, token))

    path = directory / "main.py"
    namespace: dict[str, Any] = {"__name__": "__main__", "__file__": str(path)}  # a live dict, like a script's globals
    with mock.patch.object(fluxer.Bot, "run", fake_run), mock.patch.dict(os.environ, {"FLUXER_TOKEN": "test-token"}):
        exec(compile(code, str(path), "exec"), namespace)  # noqa: S102 - the code under test is the point
    assert len(started) == 1, "the file should call bot.run exactly once"
    bot, token = started[0]
    http, gateway = FakeHTTP(), FakeGateway()
    bot._http = http  # type: ignore[assignment]
    bot._gateway = gateway  # type: ignore[assignment]
    return Running(bot, namespace, http, gateway, token, directory)
