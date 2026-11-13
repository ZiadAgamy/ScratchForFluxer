"""Who may do what with a bot.

The owner may do everything. A person who joined through an invite (a *member*) may always look at the bot's blocks and
files, plus whatever the owner ticked for them. A few things are for the owner alone and never appear here: renaming,
replacing the token, removing the bot, and managing who it is shared with. Nobody but the owner can ever read the token.

Everything that reads or changes a bot goes through :func:`get_access`, so there is one place to get this right.
A stranger gets the same 404 as for a bot that doesn't exist; a member who lacks a permission gets a 403 that says so.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Bot, BotMember

BOT_ID_RE = re.compile(r"^[0-9a-f]{32}$")

#: What the owner can allow, besides looking (looking is always allowed): ``[key, short name, longer explanation]``.
PERMISSION_LIST: list[tuple[str, str, str]] = [
    ("edit", "Change the blocks", "Add, move and remove blocks. This changes what the bot does."),
    ("files", "Manage files", "Upload and delete the pictures and files the blocks use."),
    ("run", "Run the bot", "Start, restart and stop the bot (it runs with your saved token)."),
    ("console", "See the console", "Read what the bot writes while it runs."),
    ("export", "Download the code", "Download main.py or the folder with the files."),
]
PERMISSIONS = tuple(key for key, _name, _what in PERMISSION_LIST)
EVERYTHING = frozenset(PERMISSIONS)
NO_ACCESS = "That bot doesn't exist."
NOT_ALLOWED = "The owner of this bot hasn't allowed you to do that."


def parse_perms(text: str | None) -> frozenset[str]:
    """The permissions in a stored value. Unknown words are ignored."""
    return frozenset(word for word in (text or "").split(",") if word in EVERYTHING)


def join_perms(perms: Iterable[str]) -> str:
    """A stored value for some permissions, in a fixed order."""
    wanted = set(perms)
    return ",".join(key for key in PERMISSIONS if key in wanted)


def clean_perms(raw: object) -> frozenset[str]:
    """The permissions a request asks for, or a 422 if it isn't a list of known words."""
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise HTTPException(status_code=422, detail="Choose what the person may do.")
    unknown = [item for item in raw if item not in EVERYTHING]
    if unknown:
        raise HTTPException(status_code=422, detail="That isn't something a person can be allowed to do.")
    return frozenset(raw)


@dataclass(frozen=True)
class Access:
    bot: Bot
    owner: bool
    perms: frozenset[str]
    member: BotMember | None = None

    def can(self, permission: str) -> bool:
        return self.owner or permission in self.perms

    def view(self) -> dict[str, object]:
        """What the editor needs to know about its own rights."""
        return {
            "role": "owner" if self.owner else "member",
            "perms": [key for key in PERMISSIONS if self.can(key)],
        }


def get_access(
    db: Session, user_id: str, bot_id: str, need: str | tuple[str, ...] | None = None, *, lock: bool = False
) -> Access:
    """The person's rights on the bot. ``need`` is a permission they must have (or a tuple of which any one will do).

    ``lock`` keeps the bot's row locked until the transaction ends (two uploads can't both fit under a limit).
    """
    if not BOT_ID_RE.fullmatch(bot_id or ""):
        raise HTTPException(status_code=404, detail=NO_ACCESS)
    query = select(Bot).where(Bot.id == bot_id)
    if lock:
        query = query.with_for_update()
    bot = db.execute(query).scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail=NO_ACCESS)
    if bot.user_id == user_id:
        return Access(bot, True, EVERYTHING)
    member = db.execute(
        select(BotMember).where(BotMember.bot_id == bot.id, BotMember.user_id == user_id)
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail=NO_ACCESS)  # the same answer as for a bot that isn't there
    access = Access(bot, False, parse_perms(member.perms), member)
    if need is not None:
        wanted = (need,) if isinstance(need, str) else need
        if not any(access.can(key) for key in wanted):
            raise HTTPException(status_code=403, detail=NOT_ALLOWED)
    return access
