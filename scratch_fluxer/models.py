"""Database tables.

* ``User``  — a Fluxer account that logged in (identity only; no OAuth tokens are kept).
* ``Bot``   — one bot project: encrypted bot token + the block workspace.
* ``Asset`` — a file (image, JSON, text…) attached to a bot, usable from blocks.
* ``BotInvite`` — a link the owner can send so somebody else can work on a bot (only a hash of the link is kept).
* ``BotMember`` — a person who joined through an invite, with the things the owner let them do.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    """A stored time as text that browsers read the same everywhere ("2026-01-31T09:00:00.000+00:00").

    SQLite gives times back without a zone. They were all saved as UTC, so that is what a missing zone means.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    fluxer_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64))
    global_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    avatar_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Bump to invalidate every session cookie this user holds ("log out everywhere").
    session_epoch: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    bots: Mapped[list[Bot]] = relationship(back_populates="owner", cascade="all, delete-orphan", passive_deletes=True)
    memberships: Mapped[list[BotMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Bot(Base):
    __tablename__ = "bots"
    __table_args__ = (UniqueConstraint("user_id", "bot_user_id", name="uq_bots_user_botuser"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))

    #: Fernet ciphertext of the bot token. Never returned to the browser.
    token_ciphertext: Mapped[str] = mapped_column(Text)
    token_last4: Mapped[str] = mapped_column(String(4), default="")

    application_id: Mapped[str] = mapped_column(String(32), default="")
    application_name: Mapped[str] = mapped_column(String(128), default="")
    bot_user_id: Mapped[str] = mapped_column(String(32))
    bot_username: Mapped[str] = mapped_column(String(64), default="")
    bot_avatar_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Blockly workspace serialisation (JSON text) and an optimistic-concurrency counter.
    workspace_json: Mapped[str] = mapped_column(Text, default="")
    workspace_version: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped[User] = relationship(back_populates="bots")
    assets: Mapped[list[Asset]] = relationship(back_populates="bot", cascade="all, delete-orphan", passive_deletes=True)
    invites: Mapped[list[BotInvite]] = relationship(
        back_populates="bot", cascade="all, delete-orphan", passive_deletes=True
    )
    members: Mapped[list[BotMember]] = relationship(
        back_populates="bot", cascade="all, delete-orphan", passive_deletes=True
    )


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("bot_id", "name", name="uq_assets_bot_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    bot_id: Mapped[str] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    mime: Mapped[str] = mapped_column(String(100))
    #: "image" | "json" | "text"
    kind: Mapped[str] = mapped_column(String(16))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    bot: Mapped[Bot] = relationship(back_populates="assets")


class BotInvite(Base):
    """An invite link. The link itself is never stored: only its SHA-256, so a database leak shows nobody's link."""

    __tablename__ = "bot_invites"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    bot_id: Mapped[str] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    #: The last few characters of the link, so the owner can tell links apart in a list.
    code_hint: Mapped[str] = mapped_column(String(8), default="")
    #: What a person who joins may do, besides looking: comma-separated words from ``access.PERMISSIONS``.
    perms: Mapped[str] = mapped_column(String(100), default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: ``None`` means any number of people.
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    bot: Mapped[Bot] = relationship(back_populates="invites")


class BotMember(Base):
    """A person (not the owner) who may work on a bot."""

    __tablename__ = "bot_members"
    __table_args__ = (UniqueConstraint("bot_id", "user_id", name="uq_bot_members_bot_user"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    bot_id: Mapped[str] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    perms: Mapped[str] = mapped_column(String(100), default="")
    invite_id: Mapped[str | None] = mapped_column(ForeignKey("bot_invites.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    bot: Mapped[Bot] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")
