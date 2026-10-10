"""Files a person uploads for their bot ("assets"): which ones are allowed and how a file is recognised.

Nothing here trusts the browser. A name is accepted only in a plain form (letters, digits and a few marks, with one of
the extensions below), and the bytes are checked against what the extension promises: an image must start with the
right magic bytes, JSON must parse, text must be UTF-8. SVG is left out on purpose, because an SVG can carry script.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass

MAX_NAME = 80

#: extension -> (kind, content type used when the file is served back)
TYPES: dict[str, tuple[str, str]] = {
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".gif": ("image", "image/gif"),
    ".webp": ("image", "image/webp"),
    ".json": ("json", "application/json"),
    ".txt": ("text", "text/plain; charset=utf-8"),
    ".md": ("text", "text/plain; charset=utf-8"),
    ".csv": ("text", "text/plain; charset=utf-8"),
}

#: The kinds, as the blocks name them (``catalogue.ASSET_KINDS`` is the same list).
KINDS = ("image", "json", "text")

#: What a person sees when asked which files can be uploaded.
ACCEPTED_LABEL = "PNG, JPG, GIF, WebP, JSON, TXT, MD or CSV"

_BOM = b"\xef\xbb\xbf"
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}
)


def extension(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def clean_name(raw: object) -> str | None:
    """The file name in its plain form, or None if it can't be used.

    Plain means 1 to 80 characters: letters and digits of any alphabet, spaces, dots, dashes, underscores and
    brackets. It starts with a letter or digit, has no ``..`` and ends in an extension from ``TYPES``.
    """
    if not isinstance(raw, str):
        return None
    name = unicodedata.normalize("NFC", raw).strip()
    if not 1 <= len(name) <= MAX_NAME or ".." in name or not name[0].isalnum():
        return None
    if not all(ch.isalnum() or ch in " ._()-" for ch in name):
        return None
    if extension(name) not in TYPES or name.rsplit(".", 1)[0].strip().lower() in _WINDOWS_RESERVED:
        return None
    return name


def is_safe_name(name: object) -> bool:
    """True if ``name`` is already in its plain form (the compiler checks every file name it writes into code)."""
    return isinstance(name, str) and clean_name(name) == name


def kind_of_name(name: str) -> str | None:
    entry = TYPES.get(extension(name))
    return entry[0] if entry else None


@dataclass(frozen=True)
class Upload:
    kind: str
    content_type: str
    data: bytes  # the bytes to store (a leading byte-order mark is removed from text and JSON)


def _is_image(ext: str, data: bytes) -> bool:
    if ext == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    if ext == ".gif":
        return data[:6] in (b"GIF87a", b"GIF89a")
    if ext == ".webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def inspect(name: str, data: bytes) -> tuple[Upload | None, str]:
    """Check an upload. Returns the file to store, or ``None`` and a sentence that says what is wrong."""
    ext = extension(name)
    kind, content_type = TYPES[ext]
    if not data:
        return None, "That file is empty."
    if kind == "image":
        if not _is_image(ext, data):
            return None, f"That doesn't look like a real {ext[1:].upper()} image."
        return Upload(kind, content_type, data), ""
    if data.startswith(_BOM):
        data = data[len(_BOM) :]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "That file isn't UTF-8 text. Save it as UTF-8 and try again."
    if "\x00" in text:
        return None, "That file has binary data inside, so it can't be used as text."
    if kind == "json":
        try:
            json.loads(text)
        except (ValueError, RecursionError):
            return None, "That JSON file has a mistake in it. Check it with a JSON validator and try again."
    return Upload(kind, content_type, data), ""
