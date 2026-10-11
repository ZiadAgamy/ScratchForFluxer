"""Turns a Blockly workspace (the JSON the editor saves) into a fluxer.py script.

The compiler is a whitelist. It knows each block in ``catalogue.py`` and writes a fixed piece of Python for it.
Whatever a person types only ever ends up in one of four places, and each one is made safe on the way in:

* a string literal (written with ``json.dumps``, so quotes, backslashes and new lines can't break out of it),
* a number literal (parsed and checked to be a finite number),
* a name (variables and commands are reduced to plain identifiers and never clash with the names the file uses),
* a menu choice (it must be one of the options the block offers).

There is no block that carries raw code, so there is nothing to inject. ``compile_workspace`` never raises: problems
come back as ``Diagnostic`` entries, and the result also says which lines each block wrote (for the code panel).
"""

from __future__ import annotations

import builtins
import json
import keyword
import logging
import math
import re
import unicodedata
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import assets as assetlib
from . import catalogue, codecheck

log = logging.getLogger("scratch_fluxer.compiler")

# --------------------------------------------------------------------------------------------- limits
MAX_BLOCKS = 4000
MAX_DEPTH = 60  # how deeply blocks may sit inside other blocks (sockets and C-shaped blocks together)
MAX_TEXT = 2000
MAX_NAME = 40
MAX_PREFIX = 8
MAX_COMMAND = 24
MAX_CODE = 400_000
MAX_NUMBER = 10**15  # the biggest number a number box may hold (bigger ones can't be written back exactly anyway)
MAX_LOOP_NESTING = 15  # Python refuses a file with more than 20 loops inside each other
MAX_MESSAGES = 200  # errors and warnings kept (each kind) so a damaged workspace can't produce a huge reply
JOIN_AS_LIST_FROM = 12  # a join of more pieces than this is written as "".join([...]) so the code stays flat

# --------------------------------------------------------------------------------------------- precedence
P_TERNARY, P_OR, P_AND, P_NOT, P_CMP, P_ADD, P_MUL, P_UNARY, P_ATOM = 1, 2, 3, 4, 5, 7, 8, 9, 11

#: Everything the generated file defines or imports at module level, plus the local names its functions use.
GENERATED_NAMES = frozenset(
    {
        "bot",
        "fluxer",
        "os",
        "asyncio",
        "json",
        "logging",
        "math",
        "random",
        "datetime",
        "as_id",
        "moderate",
        "has_role",
        "reaction_matches",
        "first_mention",
        "event",
        "Path",
        "ASSETS",
        "log",
        "presence",
        "update_presence",
        "to_number",
        "same",
        "has_item",
        "word",
        "json_get",
        "item_of",
        "delete_item",
        "random_item",
        "random_number",
        "ctx",
        "message",
        "member",
        "text",
        "channel",
        "embed",
        "user",
        "guild",
        "joined",
        "data",
        "x",
    }
)

#: Names a variable or command function must not take, or it would shadow something the file relies on.
RESERVED_NAMES = frozenset(set(keyword.kwlist) | set(keyword.softkwlist) | set(dir(builtins)) | GENERATED_NAMES)

HAT_LABELS = {
    "ready": "when the bot is ready",
    "message": "when a message is received",
    "command": "when the command is used",
    "member_join": "when someone joins a community",
    "member_leave": "when someone leaves a community",
    "reaction_add": "when someone adds a reaction",
    "reaction_remove": "when someone removes a reaction",
}

# --------------------------------------------------------------------------------------------- diagnostics


@dataclass(frozen=True)
class Diagnostic:
    level: str  # "error" stops the bot from running, "warning" is only a heads-up
    message: str
    block: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "message": self.message, "block": self.block}


@dataclass
class CompileResult:
    code: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    #: block id -> [first line, last line] (1-based, inclusive) of the Python that block wrote, nested blocks included
    lines: dict[str, list[int]] = field(default_factory=dict)
    #: names of the uploaded files the blocks use
    assets: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.level == "error" for d in self.diagnostics)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "ok": self.ok,
            "diagnostics": [d.as_dict() for d in self.diagnostics],
            "lines": self.lines,
            "assets": self.assets,
        }


# --------------------------------------------------------------------------------------------- helpers a file may need
#: Small functions the generated file defines when a block needs them. A helper's own ``needs`` are written first.
HELPERS: dict[str, dict[str, Any]] = {
    "to_number": {
        "imports": (),
        "needs": (),
        "source": '''def to_number(value):
    """Turn text like "12" into a number. Numbers stay as they are. Anything else becomes 0."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value if value == value and abs(value) != float("inf") else 0
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0
        return number if number == number and abs(number) != float("inf") else 0
''',
    },
    "same": {
        "imports": (),
        "needs": (),
        "source": '''def same(a, b):
    """True if two values are equal. Text that looks like a number equals that number, so "5" equals 5."""
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False
''',
    },
    "has_item": {
        "imports": (),
        "needs": ("same",),
        "source": '''def has_item(items, value):
    """True if the list has this item. Text that looks like a number matches that number."""
    return isinstance(items, list) and any(same(item, value) for item in items)
''',
    },
    "word": {
        "imports": (),
        "needs": ("to_number",),
        "source": '''def word(text, number):
    """The Nth word of some text (counting from 1), or "" if there isn't one."""
    words = str(text).split()
    index = int(to_number(number))
    return words[index - 1] if 0 < index <= len(words) else ""
''',
    },
    "json_get": {
        "imports": (),
        "needs": (),
        "source": '''def json_get(data, path):
    """Dig into JSON data, for example json_get(data, "users.0.name"). Missing keys give ""."""
    for key in str(path).split("."):
        if key == "":
            continue
        try:
            data = data[int(key)] if isinstance(data, list) else data[key]
        except (KeyError, IndexError, ValueError, TypeError):
            return ""
    return "" if data is None else data
''',
    },
    "item_of": {
        "imports": (),
        "needs": ("to_number",),
        "source": '''def item_of(items, number):
    """Item number N of a list (counting from 1), or "" if there isn't one."""
    index = int(to_number(number))
    return items[index - 1] if isinstance(items, list) and 0 < index <= len(items) else ""
''',
    },
    "delete_item": {
        "imports": (),
        "needs": ("to_number",),
        "source": '''def delete_item(items, number):
    """Delete item number N from a list (counting from 1). A number with no item does nothing."""
    index = int(to_number(number))
    if isinstance(items, list) and 0 < index <= len(items):
        del items[index - 1]
''',
    },
    "random_item": {
        "imports": ("random",),
        "needs": (),
        "source": '''def random_item(items):
    """A random item from a list, or "" if the list is empty."""
    return random.choice(items) if isinstance(items, list) and items else ""
''',
    },
    "random_number": {
        "imports": ("random",),
        "needs": (),
        "source": '''def random_number(low, high):
    """A random whole number from low to high, both included. The order of the two numbers doesn't matter."""
    low, high = sorted((int(low), int(high)))
    return random.randint(low, high)
''',
    },
    "as_id": {
        "imports": (),
        "needs": (),
        "source": '''def as_id(value):
    """A user or role ID from text. A mention like <@123> works too. Anything else gives 0."""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else 0
''',
    },
    "first_mention": {
        "imports": (),
        "needs": (),
        "source": '''def first_mention(message):
    """The ID of the first person mentioned in the message, or "" if nobody was."""
    return str(message.mentions[0].id) if message.mentions else ""
''',
    },
    "reaction_matches": {
        "imports": (),
        "needs": (),
        "source": '''def reaction_matches(event, message_id, emoji):
    """True if the reaction is on the chosen message and is the chosen emoji. A blank choice accepts anything."""
    if message_id.strip() and str(event.message_id) != message_id.strip():
        return False
    wanted = emoji.strip().replace("\\ufe0f", "")
    if not wanted:
        return True
    name = str(event.emoji.name or "").strip(":")
    options = [str(event.emoji.unicode or "").replace("\\ufe0f", ""), name, ":" + name + ":", str(event.emoji.id or "")]
    return wanted in options
''',
    },
    "has_role": {
        "imports": (),
        "needs": ("as_id",),
        "source": '''async def has_role(guild_id, user, role):
    """True if the person has the role. False when anything is missing or goes wrong."""
    if not guild_id or not as_id(user) or not as_id(role):
        return False
    try:
        guild = await bot.fetch_guild(str(guild_id))
        member = await guild.fetch_member(as_id(user))
        return as_id(role) in member.roles
    except Exception:
        return False
''',
    },
    "moderate": {
        "imports": ("datetime",),
        "needs": ("as_id",),
        "source": '''async def moderate(guild_id, action, user, extra=0, reason=""):
    """Run one moderation block. A problem (no community, a bad ID, a missing permission) is printed, never a crash."""
    target = as_id(user)
    if not guild_id or not target:
        print("Moderation skipped: it needs a community and a valid user ID.")
        return
    if target == bot.user.id:
        print("Moderation skipped: the bot will not do this to itself.")
        return
    why = str(reason).strip() or None
    try:
        guild = await bot.fetch_guild(str(guild_id))
        if action == "kick":
            await guild.kick(target, reason=why)
        elif action == "ban":
            await guild.ban(target, reason=why)
        elif action == "unban":
            await guild.unban(target, reason=why)
        else:
            member = await guild.fetch_member(target)
            if action == "timeout":
                minutes = int(extra)
                minutes = 1 if minutes < 1 else minutes
                minutes = 40320 if minutes > 40320 else minutes
                until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
                await member.timeout(until=until.isoformat(), reason=why)
            elif action == "untimeout":
                await member.timeout(until=None, reason=why)
            elif action == "add_role":
                await member.add_role(as_id(extra), reason=why)
            elif action == "remove_role":
                await member.remove_role(as_id(extra), reason=why)
    except Exception:
        print("Moderation failed (" + action + "). Check the bot's permissions and that its role is above the person's.")
''',
    },
    "update_presence": {
        "imports": (),
        "needs": (),
        "source": '''presence = {"status": "online", "custom_text": ""}


async def update_presence(**changes):
    """Tell Fluxer what to show next to the bot's name.

    fluxer.py has no helper for a custom status yet, so the presence message is sent straight to the gateway.
    """
    presence.update(changes)
    custom = {"text": presence["custom_text"]} if presence["custom_text"] else None
    payload = fluxer.gateway.GatewayPayload(
        op=fluxer.GatewayOpcode.PRESENCE_UPDATE,
        d={"since": None, "activities": [], "status": presence["status"], "afk": False, "custom_status": custom},
    )
    await bot._gateway._send(payload)
''',
    },
}
#: Helpers are written in this order.
HELPER_ORDER = [
    "to_number",
    "same",
    "has_item",
    "word",
    "json_get",
    "item_of",
    "delete_item",
    "random_item",
    "random_number",
    "as_id",
    "first_mention",
    "reaction_matches",
    "has_role",
    "moderate",
    "update_presence",
]

STDLIB_IMPORT_ORDER = ["asyncio", "datetime", "json", "logging", "math", "os", "random"]


# --------------------------------------------------------------------------------------------- small utilities


def python_string(value: str) -> str:
    """A double-quoted Python string literal for ``value``. JSON string syntax is valid Python for everything we keep."""
    return json.dumps(value, ensure_ascii=False).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


_BIDI = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"


def clean_text(value: Any, limit: int = MAX_TEXT) -> str:
    """Text for a literal: no control characters (except new lines and tabs), no bidi overrides, no lone surrogates."""
    if value is None or isinstance(value, dict | list | tuple | set):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and math.isfinite(value) and value == int(value):
        value = int(value)
    try:
        text = value if isinstance(value, str) else str(value)
    except ValueError:  # a whole number with thousands of digits
        return ""
    kept = (ch for ch in text[: limit * 2] if ch not in _BIDI and (ch in "\n\t" or unicodedata.category(ch) != "Cc"))
    return "".join("\ufffd" if "\ud800" <= ch <= "\udfff" else ch for ch in kept)[:limit]  # lone surrogates -> U+FFFD


_SIMPLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")


def is_simple_name(code: str) -> bool:
    """A plain name, or names joined by dots (``score``, ``message.author.display_name``)."""
    return _SIMPLE_NAME.fullmatch(code) is not None


_NUMERIC_TEXT = re.compile(r"-?(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,10})?")


def numeric_literal(text: str) -> int | float | None:
    """The number a piece of text spells in its plainest form ("5", "-2.5"), else None.

    "007", "1e3", "2.50" and IDs with many digits stay text, so they come out exactly as they were typed.
    """
    if _NUMERIC_TEXT.fullmatch(text) is None:
        return None
    number = float(text) if "." in text else int(text)
    if repr(number) != text or (number == 0 and text.startswith("-")):
        return None
    return number


def _identifier_safe(text: str) -> str:
    """Replaces every character that can't be part of a Python name with an underscore."""
    if text.isidentifier():
        return text
    return "".join(ch if ("x" + ch).isidentifier() else "_" for ch in text)


def clean_command_name(raw: Any) -> str:
    """What a person typed as a command: letters, digits, underscores, hyphens and single spaces."""
    text = re.sub(r"\s+", " ", clean_text(raw, 200))
    kept = "".join(ch for ch in text if ch in "_- " or unicodedata.category(ch)[0] in "LNM")
    return re.sub(r" +", " ", kept).strip()[:MAX_COMMAND].strip()


def command_function_name(name: str) -> tuple[str, bool]:
    """The Python function name for a command, and whether the command needs ``name="..."`` to keep its exact text.

    The landing page's demo follows the same rules (it gets ``RESERVED_NAMES`` from here).
    """
    ident = re.sub(r"[\s\-]+", "_", unicodedata.normalize("NFKC", name))
    if not ident or not ident[0].isalpha():  # digits and underscores can't start the name (nor can "__")
        ident = "cmd_" + ident
    if ident in RESERVED_NAMES or ident.startswith("on_"):
        ident += "_cmd"
    ident = _identifier_safe(ident)
    return ident, ident != name


def clean_prefix(raw: Any) -> str | None:
    text = clean_text(raw, 200).replace("\n", "").replace("\t", " ")[:MAX_PREFIX]
    return text if text.strip() else None


def variable_identifier(name: str) -> str:
    """A safe Python name for a variable: lower-case words joined by underscores, never one the file already uses."""
    text = unicodedata.normalize("NFKC", clean_text(name, 200)).strip().lower()
    text = re.sub(r"[\s\-]+", "_", text)
    text = "".join(ch if (ch == "_" or ch.isalnum()) else "_" for ch in text)
    text = re.sub(r"_+", "_", text).strip("_")[:MAX_NAME]
    if not text:
        return "variable"
    if not (text[0].isalpha() or text[0] == "_"):
        text = "v_" + text
    text = _identifier_safe(text)
    if text in RESERVED_NAMES or text.startswith("on_"):
        text += "_"
    return text


# --------------------------------------------------------------------------------------------- reading the workspace


@dataclass
class Node:
    type: str
    id: str
    fields: dict[str, Any]
    inputs: dict[str, Node | None]
    next: Node | None = None
    enabled: bool = True
    x: float = 0.0
    y: float = 0.0


class _TooBig(Exception):
    pass


def is_number(value: Any) -> bool:
    """True for a real, finite number (not a bool). Works on whole numbers of any size."""
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and math.isfinite(value))


def _number(value: Any) -> float:
    """A block's position on the page: any number is kept, anything else (or anything absurd) is 0."""
    if not is_number(value) or abs(value) > MAX_NUMBER:
        return 0.0
    return float(value)


class _Reader:
    """Reads Blockly's saved JSON into ``Node`` objects, refusing anything that is the wrong shape or too large."""

    def __init__(self) -> None:
        self.count = 0

    def block(self, raw: Any, depth: int = 0) -> Node | None:
        """A block and the stack that follows it (the chain is read in a loop, so a long stack can't overflow)."""
        head: Node | None = None
        tail: Node | None = None
        while isinstance(raw, dict):
            node = self._one(raw, depth)
            if node is None:
                break
            if tail is None:
                head = node
            else:
                tail.next = node
            tail = node
            following = raw.get("next")
            raw = following.get("block") if isinstance(following, dict) else None
        return head

    def _one(self, raw: dict[str, Any], depth: int) -> Node | None:
        if depth > MAX_DEPTH:
            raise _TooBig("Blocks are nested too deeply.")
        self.count += 1
        if self.count > MAX_BLOCKS:
            raise _TooBig(f"There are more than {MAX_BLOCKS} blocks. Remove some to continue.")
        type_ = raw.get("type")
        if not isinstance(type_, str) or len(type_) > 80:
            return None
        block_id = raw.get("id")
        block_id = block_id if isinstance(block_id, str) and 0 < len(block_id) <= 120 else f"auto-{self.count}"
        fields = raw.get("fields")
        inputs: dict[str, Node | None] = {}
        raw_inputs = raw.get("inputs")
        if isinstance(raw_inputs, dict):
            for name, slot in raw_inputs.items():
                if not isinstance(name, str) or not isinstance(slot, dict):
                    continue
                chosen = slot.get("block") if isinstance(slot.get("block"), dict) else slot.get("shadow")
                inputs[name] = self.block(chosen, depth + 1) if isinstance(chosen, dict) else None
        enabled = not (raw.get("enabled") is False or raw.get("disabled") is True or raw.get("disabledReasons"))
        return Node(
            type=type_,
            id=block_id,
            fields=fields if isinstance(fields, dict) else {},
            inputs=inputs,
            enabled=enabled,
            x=_number(raw.get("x")),
            y=_number(raw.get("y")),
        )


def _read_state(state: Any) -> tuple[list[Node], dict[str, dict[str, str]], list[Diagnostic]]:
    """The top-level blocks (each one starts a stack), the variables by id, and anything wrong with the shape."""
    top: list[Node] = []
    variables: dict[str, dict[str, str]] = {}
    if not isinstance(state, dict):
        return top, variables, [Diagnostic("error", "The workspace is empty or damaged.")]
    raw_vars = state.get("variables")
    if isinstance(raw_vars, list):
        for item in raw_vars[:500]:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("name"), str):
                kind = item.get("type") if isinstance(item.get("type"), str) else ""
                variables[item["id"]] = {"name": clean_text(item["name"], 200), "type": kind}
    blocks = state.get("blocks")
    raw_top = blocks.get("blocks") if isinstance(blocks, dict) else None
    reader = _Reader()
    if isinstance(raw_top, list):
        for raw in raw_top:
            node = reader.block(raw)
            if node is not None:
                top.append(node)
    return top, variables, []


# --------------------------------------------------------------------------------------------- expressions


@dataclass(frozen=True)
class Expr:
    """A piece of Python that has a value: its code, how tightly it binds, and what kind of value it is."""

    code: str
    prec: int
    kind: str  # text | number | boolean | list | any
    literal: Any = None  # the Python value, when the expression is a literal


def text_literal(value: str) -> Expr:
    cleaned = clean_text(value)
    return Expr(python_string(cleaned), P_ATOM, "text", cleaned)


def number_literal(value: int | float) -> Expr:
    if isinstance(value, float) and not math.isfinite(value):
        value = 0
    return Expr(repr(value), P_UNARY if value < 0 else P_ATOM, "number", value)


def wrap(expr: Expr, minimum: int) -> str:
    """The expression's code, in brackets if it binds more loosely than ``minimum``."""
    return expr.code if expr.prec >= minimum else f"({expr.code})"


def whole_number(expr: Expr) -> str:
    """Code for a whole number (loop counts, random ranges): a literal stays as it is, anything else goes through int()."""
    if isinstance(expr.literal, int | float) and not isinstance(expr.literal, bool):
        return repr(int(expr.literal))
    return f"int({expr.code})"


def plain_text_literal(expr: Expr) -> bool:
    """A text literal that can't be mistaken for a number ("hello", but not "5")."""
    return isinstance(expr.literal, str) and numeric_literal(expr.literal) is None


# --------------------------------------------------------------------------------------------- code rows


class Rows:
    """Lines of code with their indentation, and which lines each block wrote (rows, counting from 0)."""

    def __init__(self) -> None:
        self.items: list[tuple[int, str]] = []
        self.spans: dict[str, list[int]] = {}

    def __len__(self) -> int:
        return len(self.items)

    def add(self, indent: int, text: str) -> None:
        for line in text.split("\n"):
            self.items.append((indent, line))

    def mark(self, block_id: str, start: int) -> None:
        """The block wrote everything from row ``start`` to the end, nested blocks included."""
        self.spans[block_id] = [start, len(self.items) - 1]

    def extend(self, other: Rows) -> None:
        offset = len(self.items)
        self.items.extend(other.items)
        for block_id, (first, last) in other.spans.items():
            self.spans[block_id] = [first + offset, last + offset]


def _parts_of(block_type: str) -> dict[str, dict[str, Any]]:
    """The named parts of a block (inputs, fields, menus, variables, files) by name."""
    cached = _PARTS.get(block_type)
    if cached is None:
        cached = {}
        for line in catalogue.BLOCKS[block_type]["lines"]:
            for part in line:
                if isinstance(part, dict):
                    name = part.get("input") or part.get("field") or part.get("menu") or part.get("var")
                    name = name or part.get("asset") or part.get("number")
                    if name:
                        cached[name] = part
        _PARTS[block_type] = cached
    return cached


_PARTS: dict[str, dict[str, dict[str, Any]]] = {}


# --------------------------------------------------------------------------------------------- the generator


@dataclass
class Context:
    """What the script a block sits in can offer: a message (``ctx`` or ``message``), a member, command text."""

    kind: str  # ready | message | command | member_join | member_leave | reaction_add | reaction_remove
    hat: Node
    message_var: str | None = None
    member: bool = False
    command_text: bool = False
    moderation: Node | None = None  # the first block in this script that acts on other people
    assigned: set[str] = field(default_factory=set)  # variables this script gives a value (they need ``global``)
    loops: int = 0  # how many loops the block being written sits inside

    @property
    def provides(self) -> set[str]:
        out: set[str] = set()
        if self.message_var:
            out.add("message")
        if self.kind == "command":
            out.add("command")
        if self.member:
            out.add("member")
        if self.kind.startswith("reaction"):
            out.add("reaction")
        return out

    @property
    def guild_code(self) -> str:
        """Python for the ID of the community this script is running in."""
        if self.message_var:
            return f"{self.message_var}.guild_id"
        if self.member:
            return "member.guild_id"
        if self.kind.startswith("reaction"):
            return "event.guild_id"
        return "None"


class Generator:
    def __init__(self, assets: Mapping[str, str] | None) -> None:
        self.assets = assets
        self.diagnostics: list[Diagnostic] = []
        self.dropped = 0
        self.counts = {"error": 0, "warning": 0}
        self.variables: dict[str, dict[str, str]] = {}
        self.idents: dict[str, str] = {}  # variable id -> python name
        self.used_vars: dict[str, str] = {}  # python name -> kind, for the variables the blocks actually use
        self.imports: set[str] = set()
        self.helpers: set[str] = set()
        self.uses_assets = False
        self.uses_log = False
        self.asset_names: list[str] = []
        self.function_names: set[str] = set()
        self.variable_names: set[str] = set()
        self.name_counters: dict[str, int] = {}
        self.commands: dict[str, str] = {}
        self.event_counts: dict[str, int] = {}

    # ---- diagnostics
    def _add(self, level: str, message: str, node: Node | None) -> None:
        if self.counts[level] >= MAX_MESSAGES:
            self.dropped += 1  # a damaged workspace can't make the reply huge; the first ones are the useful ones
            return
        self.counts[level] += 1
        self.diagnostics.append(Diagnostic(level, message, node.id if node else None))

    def error(self, message: str, node: Node | None = None) -> None:
        self._add("error", message, node)

    def warn(self, message: str, node: Node | None = None) -> None:
        self._add("warning", message, node)

    def unknown_block(self, node: Node) -> None:
        self.error(f"This editor doesn't know a block called “{node.type}”.", node)

    def unique_function_name(self, base: str) -> str:
        """``base``, or ``base_2``, ``base_3``... when a function or a variable already has the name."""
        name, number = base, self.name_counters.get(base, 2)
        while name in self.function_names or name in self.variable_names:
            name, number = f"{base}_{number}", number + 1
        self.name_counters[base] = number
        self.function_names.add(name)
        return name

    # ---- variables
    def assign_variable_names(self, variables: dict[str, dict[str, str]]) -> None:
        self.variables = variables
        taken: set[str] = set()
        for var_id, info in variables.items():
            base = variable_identifier(info["name"])
            ident, number = base, 2
            while ident in taken:
                ident, number = f"{base}_{number}", number + 1
            taken.add(ident)
            self.idents[var_id] = ident
        self.variable_names = set(self.idents.values())

    def resolve_variable(self, node: Node, name: str, kinds: list[str]) -> tuple[str, str]:
        """(python name, kind) for a variable field, or a placeholder name if the field points at nothing."""
        raw = node.fields.get(name)
        var_id = raw.get("id") if isinstance(raw, dict) else None
        info = self.variables.get(var_id) if isinstance(var_id, str) else None
        if info is None and isinstance(raw, str):  # older saves keep the name instead of the id
            for key, candidate in self.variables.items():
                if candidate["name"] == raw:
                    var_id, info = key, candidate
                    break
        if info is None or not isinstance(var_id, str):
            self.error("Pick a variable in this block.", node)
            self.used_vars.setdefault("variable", "")  # a stand-in, so the code around the mistake stays readable
            return "variable", ""
        kind = info["type"]
        if kind not in kinds:
            wanted = "a list" if kinds == ["List"] else "a variable that isn't a list"
            self.warn(f"“{info['name']}” isn't {wanted}. It may not work here.", node)
        ident = self.idents[var_id]
        self.used_vars.setdefault(ident, kind)
        return ident, kind

    # ---- field readers
    def field_text(self, node: Node, name: str) -> str:
        part = _parts_of(node.type)[name]
        value = node.fields.get(name)
        if value is None or isinstance(value, dict | list):
            return part["default"]
        return clean_text(value, part.get("max", MAX_TEXT))

    def menu_value(self, node: Node, name: str) -> str:
        part = _parts_of(node.type)[name]
        value = node.fields.get(name)
        if isinstance(value, str) and any(value == option[1] for option in part["options"]):
            return value
        self.warn(
            f"“{clean_text(value, 40)}” isn't one of the choices in this block, so “{part['default']}” is used.", node
        )
        return part["default"]

    def number_value(self, node: Node, name: str) -> int | float:
        part = _parts_of(node.type)[name]
        value = node.fields.get(name)
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except ValueError:
                value = None
        if not is_number(value):
            return part["default"]
        if abs(value) > MAX_NUMBER:
            self.warn(f"That number is too big, so {part['default']} is used.", node)
            return part["default"]
        return int(value) if isinstance(value, float) and value == int(value) else value

    def asset_value(self, node: Node, name: str) -> str | None:
        part = _parts_of(node.type)[name]
        self.uses_assets = True  # even when the choice is wrong, the file defines ASSETS so it stays readable
        self.imports.add("pathlib")
        raw = node.fields.get(name)
        file_name = raw if isinstance(raw, str) else ""
        if not file_name:
            self.error("Choose a file in this block. Upload one on the Files tab first.", node)
            return None
        if not assetlib.is_safe_name(file_name):
            self.error("That isn't a file name this editor can use.", node)
            return None
        if self.assets is not None:
            kind = self.assets.get(file_name)
            if kind is None:
                self.error(f"The file “{file_name}” isn't uploaded. Add it on the Files tab.", node)
                return None
            if kind not in part["kinds"]:
                self.error(f"“{file_name}” is a {kind} file, but this block needs {' or '.join(part['kinds'])}.", node)
                return None
        if file_name not in self.asset_names:
            self.asset_names.append(file_name)
        return file_name

    # ---- blocks as values
    def value(self, node: Node | None, want: str, ctx: Context, parent: Node, *, quiet: bool = False) -> Expr:
        """The block in a socket, as an expression of the kind the socket wants.

        ``want`` is text, number, boolean, list or any (a number-looking text literal becomes a number), or raw
        (the block exactly as it is).
        """
        if node is None or not node.enabled:
            if not quiet and want == "boolean":
                self.warn("A condition is empty, so it counts as false.", parent)
            elif not quiet and want == "list":
                self.warn("A list socket is empty, so an empty list is used.", parent)
            return _EMPTY[want]
        spec = catalogue.BLOCKS.get(node.type)
        if spec is None:
            self.unknown_block(node)
            return _EMPTY[want]
        if spec["shape"] not in ("value", "boolean", "literal"):
            self.error("This block can't be used inside another block.", node)
            return _EMPTY[want]
        if not self.check_needs(node, spec, ctx):
            return _EMPTY[want]
        return self.coerce(VALUES[node.type](self, node, ctx), want, node)

    def check_needs(self, node: Node, spec: dict[str, Any], ctx: Context) -> bool:
        needs = spec["needs"]
        if not needs or any(need in ctx.provides for need in needs):
            return True
        options = " or ".join(_NEEDS_LABELS[need] for need in needs)
        label = catalogue.flat_label(node.type)
        self.error(f"“{label}” needs {options}, but “{HAT_LABELS[ctx.kind]}” doesn't have one.", node)
        return False

    def coerce(self, expr: Expr, want: str, node: Node) -> Expr:
        kind = expr.kind
        if want == "raw" or want == kind:
            return expr
        if want == "any":
            if isinstance(expr.literal, str):
                number = numeric_literal(expr.literal)
                if number is not None:
                    return number_literal(number)
            return expr
        if want == "text":
            if isinstance(expr.literal, bool):
                return text_literal("True" if expr.literal else "False")
            if kind == "number" and expr.literal is not None:
                return text_literal(str(expr.literal))
            return Expr(f"str({expr.code})", P_ATOM, "text")
        if want == "number":
            if kind == "any":
                return Expr(expr.code, expr.prec, "number")
            if kind == "text":
                if isinstance(expr.literal, str):
                    number = numeric_literal(expr.literal)
                    if number is not None:
                        return number_literal(number)
                    self.warn(f"“{clean_text(expr.literal, 30)}” isn't a number, so 0 is used.", node)
                    return number_literal(0)
                self.helpers.add("to_number")
                return Expr(f"to_number({expr.code})", P_ATOM, "number")
            if kind == "boolean":
                return Expr(f"int({expr.code})", P_ATOM, "number")
            self.warn("A list can't be used as a number, so 0 is used.", node)
            return number_literal(0)
        if want == "boolean":
            if kind == "any":
                return Expr(expr.code, expr.prec, "boolean")
            return Expr(f"bool({expr.code})", P_ATOM, "boolean")
        if want == "list":
            if kind == "any":
                return Expr(expr.code, expr.prec, "list")
            self.warn(f"A {kind} can't be used as a list, so an empty list is used.", node)
            return _EMPTY["list"]
        return expr

    def input(self, node: Node, name: str, ctx: Context) -> Expr:
        """What is in a socket, turned into the kind the socket is declared to hold."""
        return self.value(node.inputs.get(name), _parts_of(node.type)[name]["type"], ctx, node)

    def raw(self, node: Node, name: str, ctx: Context) -> Expr:
        """What is in a socket, exactly as the block (or the box in the socket) says."""
        return self.value(node.inputs.get(name), "raw", ctx, node, quiet=True)

    # ---- the pieces of a script
    def statements(self, first: Node | None, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
        """Writes a stack of blocks."""
        node = first
        while node is not None:
            if node.enabled:
                spec = catalogue.BLOCKS.get(node.type)
                start = len(rows)
                if spec is None:
                    self.unknown_block(node)
                elif spec["shape"] not in ("statement", "cap"):
                    self.error("This block can't be used as a step in a script.", node)
                elif self.check_needs(node, spec, ctx):
                    STATEMENTS[node.type](self, node, ctx, rows, indent, depth)
                    if len(rows) > start:
                        rows.mark(node.id, start)
                    if spec["shape"] == "cap" and node.next is not None:
                        self.warn("The blocks below this one never run, so they are left out.", node.next)
                        return
            node = node.next

    def body(
        self,
        first: Node | None,
        ctx: Context,
        rows: Rows,
        indent: int,
        depth: int,
        *,
        loop: bool = False,
        owner: Node | None = None,
    ) -> None:
        """The inside of a C-shaped block or a script. A loop yields to other tasks after every round."""
        if depth > MAX_DEPTH:
            raise _TooBig("Blocks are nested too deeply.")
        start = len(rows)
        if loop:
            if ctx.loops >= MAX_LOOP_NESTING:
                self.error("These loops are nested too deeply. Try fewer loops inside loops.", owner)
                rows.add(indent, "pass")
                return
            ctx.loops += 1
        self.statements(first, ctx, rows, indent, depth + 1)
        if loop:
            ctx.loops -= 1
            self.imports.add("asyncio")
            rows.add(indent, "await asyncio.sleep(0)  # let other things run")
        elif len(rows) == start:
            rows.add(indent, "pass")

    # ---- templates
    _PLACEHOLDER = re.compile(r"\{([A-Za-z_]+)(\^|!)?\}")

    def render(self, node: Node, ctx: Context, template: str) -> str:
        """Fills a template: ``{TEXT}`` is a socket, ``{TEXT^}`` a socket used as the start of a method call, ``{MENU!}``
        a menu choice written bare, ``{msg}`` the message variable and ``{VAR}`` / ``{FILE}`` the variable or file."""
        parts = _parts_of(node.type)

        def fill(match: re.Match[str]) -> str:
            name, mode = match.group(1), match.group(2)
            if name == "msg":
                return ctx.message_var or "message"
            if name == "guild":
                return ctx.guild_code
            part = parts[name]
            if "input" in part:
                expr = self.input(node, name, ctx)
                return wrap(expr, P_ATOM) if mode == "^" else expr.code
            if "menu" in part:
                chosen = self.menu_value(node, name)
                return chosen if mode == "!" else python_string(chosen)
            if "field" in part:
                return python_string(self.field_text(node, name))
            if "var" in part:
                return self.resolve_variable(node, name, part["kinds"])[0]
            if "asset" in part:
                return python_string(self.asset_value(node, name) or "")
            raise KeyError(name)

        return self._PLACEHOLDER.sub(fill, template)

    def need(self, *names: str) -> None:
        for name in names:
            if name in HELPERS:
                self.helpers.add(name)
            else:
                self.imports.add(name)


_NEEDS_LABELS = {
    "message": "a message",
    "command": "a command",
    "member": "a member who joined or left",
    "reaction": "a reaction",
}
_EMPTY = {
    "text": text_literal(""),
    "number": number_literal(0),
    "boolean": Expr("False", P_ATOM, "boolean", False),
    "list": Expr("[]", P_ATOM, "list"),
    "any": text_literal(""),
    "raw": text_literal(""),
}

# --------------------------------------------------------------------------------------------- statements

StatementFn = Callable[[Generator, Node, Context, Rows, int, int], None]
ValueFn = Callable[[Generator, Node, Context], Expr]
STATEMENTS: dict[str, StatementFn] = {}
VALUES: dict[str, ValueFn] = {}


def statement_template(block_type: str, template: str, *needs: str) -> None:
    def write(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
        gen.need(*needs)
        rows.add(indent, gen.render(node, ctx, template))

    STATEMENTS[block_type] = write


def value_template(block_type: str, template: str, *needs: str) -> None:
    kind = catalogue.BLOCKS[block_type]["returns"]

    def make(gen: Generator, node: Node, ctx: Context) -> Expr:
        gen.need(*needs)
        return Expr(gen.render(node, ctx, template), P_ATOM, kind)

    VALUES[block_type] = make


statement_template("sff_set_status", "await update_presence(status={STATUS})", "update_presence")
statement_template("sff_set_custom_status", "await update_presence(custom_text={TEXT})", "update_presence")
statement_template("sff_console_print", "print({TEXT})")
statement_template("sff_reply", "await {msg}.reply({TEXT})")
statement_template("sff_send_here", "await {msg}.send({TEXT})")
statement_template("sff_send_channel", "channel = await bot.fetch_channel({CHANNEL})\nawait channel.send({TEXT})")
statement_template("sff_react", "await {msg}.add_reaction({EMOJI})")
statement_template("sff_delete_message", "await {msg}.delete()")
statement_template("sff_dm_author", "await {msg}.author.send({TEXT})")
statement_template("sff_dm_member", "user = await bot.fetch_user(str(member.user.id))\nawait user.send({TEXT})")
statement_template(
    "sff_give_role",
    # The member of a join event carries no connection to Fluxer, so it is fetched again through the community.
    "guild = await bot.fetch_guild(str(member.guild_id))\n"
    "joined = await guild.fetch_member(member.user.id)\n"
    "await joined.add_role(int({ROLE}))",
)
ACTS_ON_PEOPLE = frozenset(
    {
        "sff_timeout_member",
        "sff_untimeout_member",
        "sff_kick_member",
        "sff_ban_member",
        "sff_unban_member",
        "sff_give_role_to",
        "sff_take_role_from",
    }
)


def moderation_template(block_type: str, template: str) -> None:
    """A block that acts on other people. Remembers the first one in the script, so the compiler can warn if
    nobody is checked for permission."""

    def write(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
        gen.need("moderate", "as_id")
        if ctx.moderation is None:
            ctx.moderation = node
        rows.add(indent, gen.render(node, ctx, template))

    STATEMENTS[block_type] = write


moderation_template("sff_timeout_member", 'await moderate({guild}, "timeout", {USER}, {MINUTES}, {REASON})')
moderation_template("sff_untimeout_member", 'await moderate({guild}, "untimeout", {USER}, 0, {REASON})')
moderation_template("sff_kick_member", 'await moderate({guild}, "kick", {USER}, 0, {REASON})')
moderation_template("sff_ban_member", 'await moderate({guild}, "ban", {USER}, 0, {REASON})')
moderation_template("sff_unban_member", 'await moderate({guild}, "unban", {USER}, 0, {REASON})')
moderation_template("sff_give_role_to", 'await moderate({guild}, "add_role", {USER}, {ROLE})')
moderation_template("sff_take_role_from", 'await moderate({guild}, "remove_role", {USER}, {ROLE})')
statement_template(
    "sff_reaction_give_role", 'await moderate({guild}, "add_role", event.user_id, {ROLE})', "moderate", "as_id"
)
statement_template(
    "sff_reaction_take_role", 'await moderate({guild}, "remove_role", event.user_id, {ROLE})', "moderate", "as_id"
)
statement_template("sff_wait", "await asyncio.sleep({SECONDS})", "asyncio")
statement_template("sff_stop_script", "return")
statement_template("sff_list_add", "{LIST}.append({ITEM})")
statement_template("sff_list_delete", "delete_item({LIST}, {N})", "delete_item")
statement_template("sff_list_clear", "{LIST}.clear()")
statement_template("sff_reply_image", "await {msg}.reply(file=fluxer.File(ASSETS / {FILE}))")
statement_template("sff_send_image", "await {msg}.send(file=fluxer.File(ASSETS / {FILE}))")
statement_template("sff_reply_file", "await {msg}.reply(file=fluxer.File(ASSETS / {FILE}))")

value_template("sff_bot_name", "bot.user.username")
value_template("sff_bot_community_count", "len(bot.guilds)")
value_template("sff_msg_text", "{msg}.content")
value_template("sff_msg_author_name", "{msg}.author.display_name")
value_template("sff_msg_author_mention", "{msg}.author.mention")
value_template("sff_msg_author_id", "str({msg}.author.id)")
value_template("sff_msg_channel_id", "str({msg}.channel_id)")
value_template("sff_msg_id", "str({msg}.id)")
value_template("sff_msg_author_is_bot", "{msg}.author.bot")
value_template("sff_reactor_id", "str(event.user_id)")
value_template("sff_first_mention_id", "first_mention({msg})", "first_mention")
value_template("sff_author_has_role", "(await has_role({guild}, {msg}.author.id, {ROLE}))", "has_role", "as_id")
value_template("sff_member_name", "member.display_name")
value_template("sff_member_mention", "member.mention")
value_template("sff_member_id", "str(member.user.id)")
value_template("sff_text_length", "len({TEXT})")
value_template("sff_word", "word({TEXT}, {N})", "word")
value_template("sff_replace", "{TEXT^}.replace({A}, {B})")
value_template("sff_to_number", "to_number({TEXT})", "to_number")
value_template("sff_list_item", "item_of({LIST}, {N})", "item_of")
value_template("sff_list_length", "len({LIST})")
value_template("sff_list_random", "random_item({LIST})", "random_item")
value_template("sff_list_join", "{SEP^}.join(str(x) for x in {LIST})")
value_template("sff_json_get", "json_get({VAR}, {KEY})", "json_get")
value_template("sff_file_text", '(ASSETS / {FILE}).read_text(encoding="utf-8")')


def _console_log(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
    gen.uses_log = True
    gen.imports.add("logging")
    rows.add(indent, gen.render(node, ctx, "log.{LEVEL!}({TEXT})"))


def _embed_statement(verb: str) -> StatementFn:
    """An embed (a card with a title, text and coloured edge), sent as a new message or as a reply."""

    def write(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
        chosen = gen.menu_value(node, "COLOR")
        rgb = next(rgb for _label, name, rgb in catalogue.EMBED_COLORS if name == chosen)
        title = gen.input(node, "TITLE", ctx).code
        text = gen.input(node, "TEXT", ctx).code
        rows.add(indent, f"embed = fluxer.Embed(title={title}, description={text}, color=0x{rgb:06X})")
        rows.add(indent, f"await {ctx.message_var}.{verb}(embed=embed)")

    return write


STATEMENTS["sff_console_log"] = _console_log
STATEMENTS["sff_send_embed"] = _embed_statement("send")
STATEMENTS["sff_reply_embed"] = _embed_statement("reply")


def _set_variable(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
    ident = gen.resolve_variable(node, "VAR", [""])[0]
    ctx.assigned.add(ident)
    rows.add(indent, f"{ident} = {gen.input(node, 'VALUE', ctx).code}")


def _change_variable(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
    ident = gen.resolve_variable(node, "VAR", [""])[0]
    ctx.assigned.add(ident)
    rows.add(indent, f"{ident} += {gen.input(node, 'BY', ctx).code}")


def _list_set(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
    ident = gen.resolve_variable(node, "LIST", ["List"])[0]
    ctx.assigned.add(ident)
    source = node.inputs.get("VALUE")
    value = gen.input(node, "VALUE", ctx).code
    # Two lists must not end up being the same list (changing one would change the other), so anything that isn't
    # a brand-new list is copied.
    fresh = source is None or not source.enabled or source.type == "sff_list_split"
    rows.add(indent, f"{ident} = {value}" if fresh else f"{ident} = list({value})")


def _json_load(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
    ident = gen.resolve_variable(node, "VAR", ["", "List"])[0]
    file_name = gen.asset_value(node, "FILE")
    if file_name is None:
        return
    gen.imports.add("json")
    ctx.assigned.add(ident)
    rows.add(indent, f'{ident} = json.loads((ASSETS / {python_string(file_name)}).read_text(encoding="utf-8"))')


def _if(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
    rows.add(indent, f"if {gen.input(node, 'COND', ctx).code}:")
    gen.body(node.inputs.get("DO"), ctx, rows, indent + 1, depth)


def _if_else(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
    rows.add(indent, f"if {gen.input(node, 'COND', ctx).code}:")
    gen.body(node.inputs.get("DO"), ctx, rows, indent + 1, depth)
    rows.add(indent, "else:")
    gen.body(node.inputs.get("ELSE"), ctx, rows, indent + 1, depth)


def _has_wait(first: Node | None) -> bool:
    """True if a wait block sits anywhere in this stack (inside other blocks too)."""
    node = first
    while node is not None:
        if node.enabled and node.type == "sff_wait":
            return True
        if any(_has_wait(inner) for name, inner in node.inputs.items() if name in ("DO", "ELSE")):
            return True
        node = node.next
    return False


def _warn_without_wait(gen: Generator, node: Node) -> None:
    if not _has_wait(node.inputs.get("DO")):
        gen.warn(
            "This loop has no wait block inside, so it runs as fast as it can. Add a “wait” to slow it down.", node
        )


def _repeat(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
    rows.add(indent, f"for _ in range({whole_number(gen.input(node, 'TIMES', ctx))}):")
    gen.body(node.inputs.get("DO"), ctx, rows, indent + 1, depth, loop=True, owner=node)


def _repeat_until(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
    _warn_without_wait(gen, node)
    rows.add(indent, f"while not {wrap(gen.input(node, 'COND', ctx), P_ATOM)}:")
    gen.body(node.inputs.get("DO"), ctx, rows, indent + 1, depth, loop=True, owner=node)


def _forever(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
    _warn_without_wait(gen, node)
    rows.add(indent, "while True:")
    gen.body(node.inputs.get("DO"), ctx, rows, indent + 1, depth, loop=True, owner=node)


def _for_each(gen: Generator, node: Node, ctx: Context, rows: Rows, indent: int, depth: int) -> None:
    ident = gen.resolve_variable(node, "VAR", [""])[0]
    ctx.assigned.add(ident)
    rows.add(indent, f"for {ident} in {gen.input(node, 'LIST', ctx).code}:")
    gen.body(node.inputs.get("DO"), ctx, rows, indent + 1, depth, loop=True, owner=node)


STATEMENTS.update(
    {
        "sff_set_variable": _set_variable,
        "sff_change_variable": _change_variable,
        "sff_list_set": _list_set,
        "sff_json_load": _json_load,
        "sff_if": _if,
        "sff_if_else": _if_else,
        "sff_repeat": _repeat,
        "sff_repeat_until": _repeat_until,
        "sff_forever": _forever,
        "sff_for_each": _for_each,
    }
)

# --------------------------------------------------------------------------------------------- values that need more than a template


def _text_literal_block(gen: Generator, node: Node, ctx: Context) -> Expr:
    return text_literal(gen.field_text(node, "TEXT"))


def _number_literal_block(gen: Generator, node: Node, ctx: Context) -> Expr:
    return number_literal(gen.number_value(node, "NUM"))


def _boolean_block(gen: Generator, node: Node, ctx: Context) -> Expr:
    choice = gen.menu_value(node, "BOOL") == "true"
    return Expr("True" if choice else "False", P_ATOM, "boolean", choice)


def _command_text(gen: Generator, node: Node, ctx: Context) -> Expr:
    ctx.command_text = True
    return Expr("text", P_ATOM, "text")


def _community_id(gen: Generator, node: Node, ctx: Context) -> Expr:
    source = f"{ctx.message_var}.guild_id" if ctx.message_var else "member.guild_id"
    return Expr(f'str({source} or "")', P_ATOM, "text")


def _variable_value(gen: Generator, node: Node, ctx: Context) -> Expr:
    return Expr(gen.resolve_variable(node, "VAR", [""])[0], P_ATOM, "any")


def _list_variable_value(gen: Generator, node: Node, ctx: Context) -> Expr:
    return Expr(gen.resolve_variable(node, "LIST", ["List"])[0], P_ATOM, "list")


def _join_pieces(
    gen: Generator, node: Node | None, ctx: Context, parent: Node, out: list[Expr], depth: int = 0
) -> None:
    """The pieces of a join, with joins inside joins flattened into one list."""
    if node is not None and node.enabled and node.type == "sff_join" and depth < MAX_DEPTH:
        _join_pieces(gen, node.inputs.get("A"), ctx, node, out, depth + 1)
        _join_pieces(gen, node.inputs.get("B"), ctx, node, out, depth + 1)
        return
    out.append(gen.value(node, "raw", ctx, parent, quiet=True))


def _literal_as_text(expr: Expr) -> str | None:
    """The text a literal piece contributes to a join, or None if the piece isn't a literal."""
    if isinstance(expr.literal, bool):
        return "True" if expr.literal else "False"
    if isinstance(expr.literal, str):
        return expr.literal
    if isinstance(expr.literal, int | float):
        return str(expr.literal)
    return None


def _join(gen: Generator, node: Node, ctx: Context) -> Expr:
    pieces: list[Expr] = []
    _join_pieces(gen, node, ctx, node, pieces)
    merged: list[str | Expr] = []  # literal text (as str) and everything else (as Expr)
    for piece in pieces:
        literal = _literal_as_text(piece)
        if literal is None:
            merged.append(piece)
        elif merged and isinstance(merged[-1], str):
            merged[-1] += literal  # neighbouring literals become one piece of text
        else:
            merged.append(literal)
    merged = [piece for piece in merged if piece != ""] or [""]
    if len(merged) == 1:
        only = merged[0]
        if isinstance(only, Expr):
            return gen.coerce(only, "text", node)
        return Expr(python_string(only), P_ATOM, "text", only)  # joined boxes may add up to more than one box holds
    if all(isinstance(piece, str) or is_simple_name(piece.code) for piece in merged):
        body = ""
        for piece in merged:
            if isinstance(piece, str):
                body += python_string(piece)[1:-1].replace("{", "{{").replace("}", "}}")
            else:
                body += "{" + piece.code + "}"
        return Expr(f'f"{body}"', P_ATOM, "text")
    if len(merged) > JOIN_AS_LIST_FROM:  # one flat list, so hundreds of pieces don't make Python's parser recurse
        items = [
            python_string(piece)
            if isinstance(piece, str)
            else (piece.code if piece.kind == "text" else f"str({piece.code})")
            for piece in merged
        ]
        return Expr('"".join([' + ", ".join(items) + "])", P_ATOM, "text")
    terms = []
    for piece in merged:
        if isinstance(piece, str):
            terms.append(python_string(piece))
        elif piece.kind == "text":
            terms.append(wrap(piece, P_ADD))
        else:
            terms.append(f"str({piece.code})")
    return Expr(" + ".join(terms), P_ADD, "text")


_MATH = {
    "add": ("+", P_ADD),
    "subtract": ("-", P_ADD),
    "multiply": ("*", P_MUL),
    "divide": ("/", P_MUL),
    "mod": ("%", P_MUL),
}
_COMPARE = {"eq": "==", "ne": "!=", "lt": "<", "gt": ">", "le": "<=", "ge": ">="}


def _math(gen: Generator, node: Node, ctx: Context) -> Expr:
    symbol, prec = _MATH[gen.menu_value(node, "OP")]
    left, right = gen.input(node, "A", ctx), gen.input(node, "B", ctx)
    return Expr(f"{wrap(left, prec)} {symbol} {wrap(right, prec + 1)}", prec, "number")


def _typed_number(expr: Expr, other: Expr) -> Expr:
    """A "5" typed into a box that sits next to something other than text is the number 5."""
    if isinstance(expr.literal, str) and other.kind != "text":
        number = numeric_literal(expr.literal)
        if number is not None:
            return number_literal(number)
    return expr


def _compare(gen: Generator, node: Node, ctx: Context) -> Expr:
    op = gen.menu_value(node, "OP")
    left, right = gen.raw(node, "A", ctx), gen.raw(node, "B", ctx)
    if op in ("eq", "ne"):
        left, right = _typed_number(left, right), _typed_number(right, left)
        # Plain == when it can't go wrong; otherwise `same`, which lets the text "5" equal the number 5.
        plain = plain_text_literal(left) or plain_text_literal(right)
        plain = plain or (left.kind == right.kind and left.kind in ("text", "number", "boolean"))
        if plain:
            return Expr(f"{wrap(left, P_CMP + 1)} {_COMPARE[op]} {wrap(right, P_CMP + 1)}", P_CMP, "boolean")
        gen.helpers.add("same")
        call = f"same({left.code}, {right.code})"
        return Expr(call, P_ATOM, "boolean") if op == "eq" else Expr(f"not {call}", P_NOT, "boolean")
    left, right = gen.coerce(left, "number", node), gen.coerce(right, "number", node)
    return Expr(f"{wrap(left, P_CMP + 1)} {_COMPARE[op]} {wrap(right, P_CMP + 1)}", P_CMP, "boolean")


def _logic(gen: Generator, node: Node, ctx: Context) -> Expr:
    op = gen.menu_value(node, "OP")
    prec = P_AND if op == "and" else P_OR
    left, right = gen.input(node, "A", ctx), gen.input(node, "B", ctx)
    return Expr(f"{wrap(left, prec + 1)} {op} {wrap(right, prec + 1)}", prec, "boolean")


def _not(gen: Generator, node: Node, ctx: Context) -> Expr:
    return Expr(f"not {wrap(gen.input(node, 'A', ctx), P_ATOM)}", P_NOT, "boolean")


def _lowered(expr: Expr) -> str:
    if isinstance(expr.literal, str):
        return python_string(expr.literal.lower())
    return f"{wrap(expr, P_ATOM)}.lower()"


def _contains(gen: Generator, node: Node, ctx: Context) -> Expr:
    haystack, needle = gen.input(node, "A", ctx), gen.input(node, "B", ctx)
    return Expr(f"{_lowered(needle)} in {_lowered(haystack)}", P_CMP, "boolean")


def _text_case(gen: Generator, node: Node, ctx: Context) -> Expr:
    method = gen.menu_value(node, "OP")
    return Expr(f"{wrap(gen.input(node, 'TEXT', ctx), P_ATOM)}.{method}()", P_ATOM, "text")


def _round(gen: Generator, node: Node, ctx: Context) -> Expr:
    op = gen.menu_value(node, "OP")
    number = gen.input(node, "N", ctx).code
    if op in ("floor", "ceil"):
        gen.imports.add("math")
        return Expr(f"math.{op}({number})", P_ATOM, "number")
    return Expr(f"{op}({number})", P_ATOM, "number")


def _random(gen: Generator, node: Node, ctx: Context) -> Expr:
    low, high = gen.input(node, "A", ctx), gen.input(node, "B", ctx)
    if isinstance(low.literal, int | float) and isinstance(high.literal, int | float):
        first, last = sorted((int(low.literal), int(high.literal)))
        gen.imports.add("random")
        return Expr(f"random.randint({first!r}, {last!r})", P_ATOM, "number")
    gen.helpers.add("random_number")
    return Expr(f"random_number({low.code}, {high.code})", P_ATOM, "number")


def _list_contains(gen: Generator, node: Node, ctx: Context) -> Expr:
    ident = gen.resolve_variable(node, "LIST", ["List"])[0]
    item = gen.input(node, "ITEM", ctx)
    if plain_text_literal(item):
        return Expr(f"{wrap(item, P_CMP + 1)} in {ident}", P_CMP, "boolean")
    gen.helpers.add("has_item")
    return Expr(f"has_item({ident}, {item.code})", P_ATOM, "boolean")


def _list_split(gen: Generator, node: Node, ctx: Context) -> Expr:
    receiver = wrap(gen.input(node, "TEXT", ctx), P_ATOM)
    separator = gen.input(node, "SEP", ctx)
    if isinstance(separator.literal, str):
        return Expr(f"{receiver}.split({separator.code if separator.literal else ''})", P_ATOM, "list")
    return Expr(f"{receiver}.split({wrap(separator, P_OR)} or None)", P_ATOM, "list")


VALUES.update(
    {
        "sff_text": _text_literal_block,
        "sff_number": _number_literal_block,
        "sff_boolean": _boolean_block,
        "sff_command_text": _command_text,
        "sff_community_id": _community_id,
        "sff_variable": _variable_value,
        "sff_list_variable": _list_variable_value,
        "sff_join": _join,
        "sff_math": _math,
        "sff_compare": _compare,
        "sff_logic": _logic,
        "sff_not": _not,
        "sff_contains": _contains,
        "sff_text_case": _text_case,
        "sff_round": _round,
        "sff_random": _random,
        "sff_list_contains": _list_contains,
        "sff_list_split": _list_split,
    }
)

# --------------------------------------------------------------------------------------------- scripts (the hat blocks)

#: event hat -> (event name for fluxer.py, function name, parameter, kind of script)
_EVENTS = {
    "sff_event_ready": ("ready", "on_ready", None, "ready"),
    "sff_event_message": ("message", "on_message", "message", "message"),
    "sff_event_member_join": ("member_join", "on_member_join", "data", "member_join"),
    "sff_event_member_leave": ("member_remove", "on_member_remove", "data", "member_leave"),
    "sff_event_reaction_add": ("raw_reaction_add", "on_raw_reaction_add", "event", "reaction_add"),
    "sff_event_reaction_remove": ("raw_reaction_remove", "on_raw_reaction_remove", "event", "reaction_remove"),
}
HAT_TYPES = frozenset(_EVENTS) | {"sff_event_command"}


def _contains(first: Node | None, block_type: str, depth: int = 0) -> bool:
    """True if a block of this type sits anywhere in the stack, inside other blocks and sockets too."""
    node = first
    while node is not None and depth < MAX_DEPTH:
        if node.enabled and node.type == block_type:
            return True
        if any(_contains(inner, block_type, depth + 1) for inner in node.inputs.values()):
            return True
        node = node.next
    return False


def _script(gen: Generator, hat: Node) -> Rows | None:
    """One hat block and the stack under it, as a function."""
    decorators: list[str] = []
    preamble: list[tuple[int, str]] = []  # lines that come first inside the function
    if hat.type == "sff_event_command":
        name = clean_command_name(hat.fields.get("NAME"))
        if not name:
            gen.error("Give this command a name.", hat)
            return None
        func, renamed = command_function_name(name)
        unique = gen.unique_function_name(func)
        renamed = renamed or unique != func
        func = unique
        if name in gen.commands:
            gen.warn(f"Another script already uses the command “{name}”. Only one of them will answer.", hat)
        gen.commands[name] = hat.id
        ctx = Context("command", hat, message_var="ctx")
        decorators.append(f"@bot.command(name={python_string(name)})" if renamed else "@bot.command()")
        param = "ctx"
    else:
        event, func, param_name, kind = _EVENTS[hat.type]
        gen.event_counts[event] = gen.event_counts.get(event, 0) + 1
        if gen.event_counts[event] > 1:  # a second script for the same event gets its own function name
            func = f"{func}_{gen.event_counts[event]}"
            decorators.append(f"@bot.on({python_string(event)})")
        else:
            decorators.append("@bot.event")
        ctx = Context(kind, hat, message_var="message" if kind == "message" else None, member=kind.startswith("member"))
        param = param_name or ""
        if kind == "message":
            preamble += [
                (1, "# Ignore bots (including this one), so replies can't trigger each other forever."),
                (1, "if message.author.bot:"),
                (2, "return"),
            ]
        elif ctx.member:
            preamble.append((1, "member = fluxer.GuildMember.from_data(data)"))
        elif kind.startswith("reaction"):
            message_id = gen.field_text(hat, "MESSAGE").strip()
            emoji = gen.field_text(hat, "EMOJI").strip()
            if message_id and not message_id.isdigit():
                gen.warn("The message ID is only digits, so no message will match. Copy it from Fluxer.", hat)
            gen.need("reaction_matches")
            preamble += [
                (1, "# Ignore the bot's own reactions."),
                (1, "if event.user_id == bot.user.id:"),
                (2, "return"),
                (1, f"if not reaction_matches(event, {python_string(message_id)}, {python_string(emoji)}):"),
                (2, "return"),
            ]

    body = Rows()
    gen.body(hat.next, ctx, body, 1, 0)

    if (
        ctx.moderation is not None
        and ctx.kind in ("message", "command")
        and not _contains(hat.next, "sff_author_has_role")
    ):
        gen.warn(
            "Anyone who can type this could use it on other people. Put it inside an “if” with "
            "“author has the role …” so only your staff can.",
            ctx.moderation,
        )

    if ctx.command_text:  # the text after the command arrives as a keyword argument
        param = 'ctx, *, text: str = ""'
    rows = Rows()
    for decorator in decorators:
        rows.add(0, decorator)
    rows.add(0, f"async def {func}({param}):")
    if ctx.assigned:  # a function must say it means the file's variables, not new ones of its own
        rows.add(1, "global " + ", ".join(sorted(ctx.assigned)))
    for indent, line in preamble:
        rows.add(indent, line)
    rows.extend(body)
    rows.spans[hat.id] = [0, len(rows) - 1]
    return rows


# --------------------------------------------------------------------------------------------- putting the file together


class _Document:
    """The finished file, chunk by chunk: two blank lines around functions, one between other chunks."""

    def __init__(self) -> None:
        self.rows = Rows()
        self.previous: str | None = None

    def add(self, kind: str, rows: Rows) -> None:
        if len(self.rows):
            for _ in range(2 if "def" in (kind, self.previous) else 1):
                self.rows.add(0, "")
        self.rows.extend(rows)
        self.previous = kind


def _lines(lines: list[str], block_id: str | None = None) -> Rows:
    rows = Rows()
    for line in lines:
        rows.add(0, line)
    if block_id is not None and len(rows):
        rows.mark(block_id, 0)
    return rows


def _import_lines(gen: Generator) -> list[str]:
    lines = [f"import {name}" for name in STDLIB_IMPORT_ORDER if name == "os" or name in gen.imports]
    if "pathlib" in gen.imports:
        lines.append("from pathlib import Path")
    return [*lines, "", "import fluxer"]


def compile_workspace(state: Any, *, assets: Mapping[str, str] | None = None) -> CompileResult:
    """The Python for a saved workspace. ``assets`` maps each uploaded file's name to its kind (image/json/text).

    With ``assets=None`` the file names in blocks are not checked against uploads (only against the safe-name rule).
    """
    try:
        return _compile(state, assets)
    except _TooBig as exc:
        return CompileResult("", [Diagnostic("error", str(exc))])
    except Exception:  # a damaged workspace must never turn into a server error
        log.exception("The compiler failed on a workspace")
        return CompileResult("", [Diagnostic("error", "Something in this workspace couldn't be turned into code.")])


def _compile(state: Any, assets: Mapping[str, str] | None) -> CompileResult:
    top, variables, problems = _read_state(state)
    gen = Generator(assets)
    gen.diagnostics.extend(problems)
    gen.assign_variable_names(variables)

    setups = [node for node in top if node.enabled and node.type == "sff_bot_setup"]
    logins = [node for node in top if node.enabled and node.type == "sff_bot_login"]
    hats = [node for node in top if node.enabled and node.type in HAT_TYPES]
    loose = [node for node in top if node.enabled and node.type not in HAT_TYPES | set(catalogue.LOCKED_TYPES)]
    for extra in setups[1:]:
        gen.warn("Only one “create the bot” block is used. This one is ignored.", extra)
    for extra in logins[1:]:
        gen.warn("Only one “log in” block is used. This one is ignored.", extra)
    for node in loose:
        spec = catalogue.BLOCKS.get(node.type)
        if spec is None:
            gen.unknown_block(node)
        elif spec["shape"] in ("value", "boolean"):
            gen.warn("This block isn't plugged into anything, so it does nothing.", node)
        elif spec["shape"] != "literal":  # a stray box of text or a number is harmless
            gen.warn(
                "This block isn't under an event, so it will never run. Attach it below a yellow event block.", node
            )
    if not hats:
        gen.warn("There is no event block yet. Add a yellow one to tell your bot when to do something.")

    prefix = "!"
    if setups:
        chosen = clean_prefix(setups[0].fields.get("PREFIX"))
        if chosen is None:
            gen.warn("The command prefix was empty, so “!” is used.", setups[0])
        else:
            prefix = chosen

    scripts: list[Rows] = []
    for hat in sorted(hats, key=lambda n: (n.y, n.x, n.id)):  # top to bottom, as they are laid out in the editor
        rows = _script(gen, hat)
        if rows is not None:
            scripts.append(rows)

    needed = set(gen.helpers)
    for name in list(needed):
        needed.update(HELPERS[name]["needs"])
    for name in needed:
        gen.imports.update(HELPERS[name]["imports"])

    doc = _Document()
    doc.add("stmt", _lines(_import_lines(gen)))
    doc.add(
        "stmt",
        _lines(
            [
                "bot = fluxer.Bot(",
                f"    command_prefix={python_string(prefix)},",
                "    intents=fluxer.Intents.default(),",
                ")",
            ],
            setups[0].id if setups else None,
        ),
    )
    if gen.uses_log:
        doc.add(
            "stmt",
            _lines(
                [
                    'logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")',
                    'log = logging.getLogger("bot")',
                ]
            ),
        )
    if gen.uses_assets:
        doc.add("stmt", _lines(['ASSETS = Path(__file__).parent / "assets"']))
    if gen.used_vars:
        doc.add("stmt", _lines([f"{name} = {'[]' if kind == 'List' else '0'}" for name, kind in gen.used_vars.items()]))
    for name in HELPER_ORDER:
        if name in needed:
            doc.add("def", _lines(HELPERS[name]["source"].rstrip("\n").split("\n")))
    for rows in scripts:
        doc.add("def", rows)
    doc.add("stmt", _lines(['bot.run(os.environ["FLUXER_TOKEN"])'], logins[0].id if logins else None))

    lines = [("    " * indent + text) if text else "" for indent, text in doc.rows.items]
    code = "\n".join(lines) + "\n"
    spans = {block_id: [first + 1, last + 1] for block_id, (first, last) in doc.rows.spans.items()}
    if gen.dropped:
        gen.diagnostics.append(Diagnostic("warning", f"{gen.dropped} more messages are not shown."))
    result = CompileResult(code, gen.diagnostics, spans, gen.asset_names)
    if len(code) > MAX_CODE:
        result.diagnostics.append(Diagnostic("error", "The code is too long. Remove some blocks to continue."))
        return result
    try:
        codecheck.check(code)
    except codecheck.UnsafeCode as exc:
        # Code that comes with errors is never run (and may hold what the errors are about, like a file left unchosen),
        # so only a refusal of code that looked fine is a bug in the compiler: say so instead of hiding it.
        if result.ok:
            log.error("The compiler wrote code that failed the safety check: %s", exc)
            message = "The compiler wrote code that failed its safety check. Please report this."
            result.diagnostics.append(Diagnostic("error", message))
        return result
    if result.ok and not _python_reads(code):
        message = "These blocks are too complicated to turn into code. Try fewer blocks inside other blocks."
        result.diagnostics.append(Diagnostic("error", message))
    return result


def _python_reads(code: str) -> bool:
    """True if Python can compile the file (it can fail on very deep nesting, or on a compiler bug)."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile(code, "<bot>", "exec", dont_inherit=True)
    except (SyntaxError, ValueError, RecursionError, MemoryError, OverflowError) as exc:
        log.error("Python could not compile the generated file: %s", exc)
        return False
    return True


# --------------------------------------------------------------------------------------------- the starting workspace


def default_workspace(
    prefix: str = "!", ready: str = "Bot is online!", command: str = "ping", reply: str = "Pong!"
) -> dict[str, Any]:
    """What a new bot starts with. It compiles to the same file the landing page's demo shows."""

    def shadow(block_id: str, text: str) -> dict[str, Any]:
        return {"shadow": {"type": "sff_text", "id": block_id, "fields": {"TEXT": text}}}

    return {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "sff_bot_setup",
                    "id": "setup",
                    "x": 40,
                    "y": 40,
                    "deletable": False,
                    "fields": {"PREFIX": prefix},
                },
                {
                    "type": "sff_event_ready",
                    "id": "ready",
                    "x": 40,
                    "y": 150,
                    "next": {
                        "block": {
                            "type": "sff_console_print",
                            "id": "print",
                            "inputs": {"TEXT": shadow("print-text", ready)},
                        }
                    },
                },
                {
                    "type": "sff_event_command",
                    "id": "command",
                    "x": 40,
                    "y": 310,
                    "fields": {"NAME": command},
                    "next": {
                        "block": {
                            "type": "sff_reply",
                            "id": "reply",
                            "inputs": {"TEXT": shadow("reply-text", reply)},
                        }
                    },
                },
                {"type": "sff_bot_login", "id": "login", "x": 40, "y": 470, "deletable": False},
            ],
        },
        "variables": [],
    }
