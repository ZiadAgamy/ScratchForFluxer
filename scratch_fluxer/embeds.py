"""The options of an embed (the card with a coloured edge), as the embed blocks keep them.

A block keeps everything the pop-up edits except the title and the text (those stay sockets, so a block can fill
them) in one field, as a small JSON object::

    {"color": "#ff8800", "url": "https://example.com", "image": "file:banner.png", "time": true}

Only what differs from the default is stored. Everything that goes in or comes out passes through ``parse``, so a
hand-made or damaged value can never put anything but a colour, a plain web address, an uploaded file's name or a short
piece of text into the generated code.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .assets import is_safe_name, kind_of_name

#: ``[label, name, rgb]``: the colours the pop-up offers first.
EMBED_COLORS = [
    ["blue", "blue", 0x176DFF],
    ["green", "green", 0x52C987],
    ["yellow", "yellow", 0xF5B335],
    ["orange", "orange", 0xF59A4D],
    ["red", "red", 0xF2735A],
    ["pink", "pink", 0xEE6A93],
    ["purple", "purple", 0xA98BFF],
    ["teal", "teal", 0x2EC4B0],
    ["grey", "grey", 0x8B8998],
]
EMBED_COLOR_OPTIONS = [[label, value] for label, value, _rgb in EMBED_COLORS]
PRESETS = {name: rgb for _label, name, rgb in EMBED_COLORS}
DEFAULT_COLOR = "blue"

#: Longest value of each option (the pop-up and the server use the same numbers).
LIMITS = {
    "title": 256,
    "text": 2000,
    "url": 500,
    "image": 500,
    "thumbnail": 500,
    "author": 100,
    "author_icon": 500,
    "footer": 200,
    "footer_icon": 500,
}

#: The most the field holds once written out (every option at its limit would be longer, which is never needed).
MAX_JSON = 3000

FILE_PREFIX = "file:"

_URL = re.compile(r"^https?://[^\s\"'<>\\`]{1,%d}$" % LIMITS["url"], re.IGNORECASE)
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_UNWANTED = re.compile("[\x00-\x1f\x7f‪-‮⁦-⁩  \ud800-\udfff]")

#: The options that hold a web address, a web address or an uploaded file, a short text.
_ADDRESSES = ("url", "author_icon", "footer_icon")
_PICTURES = ("image", "thumbnail")
_TEXTS = ("author", "footer")


def _short_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return _UNWANTED.sub(" ", value).strip()[:limit].strip()


def _address(value: Any) -> str:
    return value if isinstance(value, str) and _URL.match(value) else ""


def _picture(value: Any, *, files: bool) -> str:
    """A web address, or ``file:name`` for one of the person's uploaded pictures (when the block can send one)."""
    if not isinstance(value, str):
        return ""
    if _URL.match(value):
        return value
    if files and value.startswith(FILE_PREFIX):
        name = value[len(FILE_PREFIX) :]
        if is_safe_name(name) and kind_of_name(name) == "image":
            return value
    return ""


def _color(value: Any) -> str:
    if isinstance(value, str):
        if value in PRESETS:
            return value
        if _HEX.match(value):
            return value.lower()
    return DEFAULT_COLOR


def parse(raw: Any, *, files: bool = True) -> dict[str, Any]:
    """The options in ``raw`` (the field's text), with anything that isn't allowed left out. Never raises."""
    data: Any = None
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str) and raw.strip() and len(raw) <= MAX_JSON * 2:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        data = {}
    out: dict[str, Any] = {}
    color = _color(data.get("color"))
    if color != DEFAULT_COLOR:
        out["color"] = color
    for key in _ADDRESSES:
        if value := _address(data.get(key)):
            out[key] = value
    for key in _PICTURES:
        if value := _picture(data.get(key), files=files):
            out[key] = value
    for key in _TEXTS:
        if value := _short_text(data.get(key), LIMITS[key]):
            out[key] = value
    # An icon belongs to a name or a text: on its own it would be sent as an author or footer with nothing in it.
    if "author" not in out:
        out.pop("author_icon", None)
    if "footer" not in out:
        out.pop("footer_icon", None)
    if data.get("time") is True:
        out["time"] = True
    return out


def dumps(options: dict[str, Any]) -> str:
    return json.dumps(options, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def clean(raw: Any, *, files: bool = True) -> str:
    """The text a block's options field is kept as: the canonical form of what ``parse`` accepts."""
    return dumps(parse(raw, files=files))


def from_legacy_color(name: Any) -> str:
    """Blocks made before the pop-up chose the colour from a menu; this is the same choice as options."""
    if isinstance(name, str) and name in PRESETS and name != DEFAULT_COLOR:
        return dumps({"color": name})
    return "{}"


def rgb(options: dict[str, Any]) -> int:
    color = options.get("color", DEFAULT_COLOR)
    return PRESETS[color] if color in PRESETS else int(color[1:], 16)


def file_name(value: str) -> str | None:
    """The uploaded file a picture option names, or None when it is a web address."""
    return value[len(FILE_PREFIX) :] if value.startswith(FILE_PREFIX) else None


def files_in(options: dict[str, Any]) -> list[str]:
    """The uploaded files the options use (the same file once)."""
    names: list[str] = []
    for key in _PICTURES:
        name = file_name(options.get(key, ""))
        if name and name not in names:
            names.append(name)
    return names


def browser_config() -> dict[str, Any]:
    """What the pop-up needs to know. The same for everyone."""
    return {
        "colors": [{"name": name, "label": label, "rgb": f"#{value:06x}"} for label, name, value in EMBED_COLORS],
        "default_color": DEFAULT_COLOR,
        "limits": LIMITS,
        "file_prefix": FILE_PREFIX,
    }
