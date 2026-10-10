"""Database reads the editor routes share: a bot's stored blocks, its files, and what they compile to.

None of these touch a bot's token. Reading a file list never loads the files themselves (only their names and sizes).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import assets as assetlib
from . import workspace as ws_lib
from .compiler import CompileResult, compile_workspace
from .config import Settings
from .models import Asset, Bot, iso_utc


def asset_rows(db: Session, bot_id: str) -> list[Any]:
    """(name, kind, mime, size, created_at) for each file of the bot, A to Z."""
    return list(
        db.execute(
            select(Asset.name, Asset.kind, Asset.mime, Asset.size, Asset.created_at)
            .where(Asset.bot_id == bot_id)
            .order_by(func.lower(Asset.name), Asset.name)
        ).all()
    )


def file_view(row: Any, used: Counter[str]) -> dict[str, Any]:
    return {
        "name": row.name,
        "kind": row.kind,
        "mime": row.mime,
        "size": row.size,
        "created_at": iso_utc(row.created_at),
        "used_by": used.get(row.name, 0),
    }


def limits(settings: Settings) -> dict[str, Any]:
    """What the Files tab tells a person before they upload."""
    return {
        "max_files": settings.max_assets_per_bot,
        "max_file_bytes": settings.max_asset_bytes,
        "max_total_bytes": settings.max_assets_total_bytes,
        "accepted": assetlib.ACCEPTED_LABEL,
        "extensions": sorted(assetlib.TYPES),
    }


@dataclass
class Loaded:
    """A bot's blocks as the editor needs them: cleaned, with its files and what they compile to."""

    workspace: dict[str, Any]
    dropped: int
    damaged: bool
    files: list[dict[str, Any]]
    compiled: CompileResult


def compile_with_files(
    db: Session, bot_id: str, workspace: dict[str, Any]
) -> tuple[CompileResult, list[dict[str, Any]]]:
    """The Python for ``workspace`` (already cleaned), checked against the bot's uploaded files, and the file list."""
    rows = asset_rows(db, bot_id)
    kinds = {row.name: row.kind for row in rows}
    used = ws_lib.files_used(workspace)
    return compile_workspace(workspace, assets=kinds), [file_view(row, used) for row in rows]


def load(db: Session, bot: Bot) -> Loaded:
    workspace, dropped, damaged = ws_lib.from_text(bot.workspace_json)
    compiled, files = compile_with_files(db, bot.id, workspace)
    return Loaded(workspace, dropped, damaged, files, compiled)
