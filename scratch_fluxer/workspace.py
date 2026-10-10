"""What a bot's blocks may look like when they are stored and when they are handed back to the editor.

The editor saves its blocks as JSON and the browser is not trusted, so anything at all can arrive. Before a workspace
is stored, and again before it is shown, it is rebuilt by ``clean_workspace`` from what the catalogue allows:

* only blocks the catalogue knows, with only the fields and sockets those blocks have, holding values of the right
  kind (a text box holds text, a menu holds one of its options, a number box holds a finite number),
* only connections the editor could make (a number block in a number socket, a statement under a statement; the editor
  refuses to open a workspace that joins things it would not let a person join, so those are removed here),
* ids that are unique, variables that are declared (with names that differ and a kind the block accepts), and exactly
  one of each locked block,
* nothing nested so deeply that reading it back could fail (see ``MAX_JSON_DEPTH``).

The result opens in the editor without a warning, saving it again gives the same thing back, and for a workspace that
was well formed to begin with ``compile_workspace`` writes the same Python as it did for the original.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from . import catalogue
from .compiler import MAX_NUMBER, clean_text, default_workspace

MAX_NODES = 20_000  # a hard stop while reading; the compiler has its own, friendlier limit well below this
MAX_NESTING = 120  # blocks inside blocks
MAX_JSON_DEPTH = 600  # brackets inside brackets in the saved text (each block in a row adds two, see below)
MAX_VARIABLES = 500
MAX_VARIABLE_NAME = 60
MAX_ID = 120
MAX_COORDINATE = 1_000_000
MAX_ASSET_NAME = 80

#: How many blocks one script may have in a row (the number the person is told). Blockly writes a row of blocks as
#: blocks inside blocks, so every block adds two brackets, and Python's JSON reader and writer give up when brackets are
#: nested about a thousand deep (on older Pythons). A row this long fits whenever the blocks in it hold plain boxes.
MAX_IN_A_ROW = 290

_NEXT = {"hat", "statement"}  # shapes with a connection below them
_PREVIOUS = {"statement", "cap"}  # shapes with a connection above them
_OUTPUT = {"value", "boolean", "literal"}
MANUALLY_DISABLED = "MANUALLY_DISABLED"  # how Blockly writes down that a person turned a block off


class WorkspaceTooBig(ValueError):
    """The workspace holds more blocks, or is nested deeper, than anything the editor could have made."""


@dataclass
class Cleaned:
    workspace: dict[str, Any]
    dropped: int  # blocks removed because the editor could not have held them


# --------------------------------------------------------------------------------------------- small pieces


def _spec(raw: Any) -> dict[str, Any] | None:
    type_ = raw.get("type") if isinstance(raw, dict) else None
    return catalogue.BLOCKS.get(type_) if isinstance(type_, str) else None


def _parts(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [part for line in spec["lines"] for part in line if isinstance(part, dict)]


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_ID and value.isprintable()


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _coordinate(value: Any) -> int | float:
    if not _is_number(value) or value != value:  # not a number at all, or NaN
        return 0
    number = max(-MAX_COORDINATE, min(MAX_COORDINATE, value))
    if isinstance(number, float):
        number = round(number, 2)
    return int(number) if float(number).is_integer() else number


def _number(value: Any, default: int | float) -> int | float:
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return default
    if not _is_number(value):
        return default
    if isinstance(value, float) and not float("-inf") < value < float("inf"):
        return default
    if abs(value) > MAX_NUMBER:
        return default
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _output_check(spec: dict[str, Any]) -> set[str] | None:
    """The kinds of value a block can be plugged in as (None: it fits any socket)."""
    kind = "boolean" if spec["shape"] == "boolean" else spec["returns"]
    return {"text": {"String"}, "number": {"Number"}, "boolean": {"Boolean"}, "list": {"List"}}.get(kind or "")


def _input_check(part: dict[str, Any]) -> set[str] | None:
    return {"number": {"Number"}, "boolean": {"Boolean"}, "list": {"List"}}.get(part["type"])


def _fits(part: dict[str, Any], spec: dict[str, Any]) -> bool:
    """True if a block of this kind may be plugged into this socket (Blockly checks the same thing)."""
    wanted, given = _input_check(part), _output_check(spec)
    return spec["shape"] in _OUTPUT and (wanted is None or given is None or bool(wanted & given))


class _Names:
    """The variable names in use (capital letters do not matter) and, for each name, the number to try next when it is
    wanted again ("score", "score 2", "score 3"). Remembering that number keeps a long run of equal names fast."""

    def __init__(self, names: Iterable[str] = ()) -> None:
        self.taken = {name.lower() for name in names}
        self._next: dict[str, int] = {}

    def make(self, raw: Any) -> str:
        name = " ".join(clean_text(raw, MAX_VARIABLE_NAME).split()) or "variable"
        key = name.lower()
        candidate, number = name, self._next.get(key, 2)
        while candidate.lower() in self.taken:
            suffix = f" {number}"  # "score 2": the name is cut to leave room for it
            candidate, number = name[: MAX_VARIABLE_NAME - len(suffix)].rstrip() + suffix, number + 1
        self._next[key] = number
        self.taken.add(candidate.lower())
        return candidate


def _too_long() -> WorkspaceTooBig:
    return WorkspaceTooBig(
        f"This script is too long or too tangled to save: a script can have about {MAX_IN_A_ROW} blocks in a row "
        f"(fewer if they hold other blocks), and blocks can sit inside each other about {MAX_NESTING} levels deep. "
        "Split it into smaller scripts, for example one “when the command … is used” block for each command."
    )


# --------------------------------------------------------------------------------------------- the cleaner


class _Cleaner:
    def __init__(self, variables: dict[str, dict[str, str]]) -> None:
        self.variables = variables
        self.names = _Names(info["name"] for info in variables.values())
        self.ids: set[str] = set(variables)
        self.replacements: dict[tuple[tuple[str, str], str], str] = {}
        self.nodes = 0
        self.dropped = 0
        self.serial = 0

    # ---- ids and variables
    def new_id(self, wanted: Any = None) -> str:
        if _is_id(wanted) and wanted not in self.ids:
            self.ids.add(wanted)
            return wanted
        while True:
            self.serial += 1
            candidate = f"fix-{self.serial}"
            if candidate not in self.ids:
                self.ids.add(candidate)
                return candidate

    def variable_for(self, part: dict[str, Any], wanted: Any = None) -> str:
        """The id of the variable a block uses.

        A variable the workspace does not list, or one of a kind this block cannot take (a list where a plain variable
        goes), is replaced by a new one: left alone, the editor would invent one itself and the block would silently
        point somewhere else.
        """
        known = self.variables.get(wanted) if _is_id(wanted) else None
        if known is not None and known["type"] in part["kinds"]:
            return known["id"]
        kind = part["kinds"][0]
        # Blocks that point at the same missing (or wrong) variable share the one that replaces it.
        memo = (("id", wanted) if _is_id(wanted) else ("default", part["default"]), kind)
        if memo in self.replacements:
            return self.replacements[memo]
        if len(self.variables) >= MAX_VARIABLES:  # every replacement is one more: no editor made this many
            raise WorkspaceTooBig(f"A bot can have {MAX_VARIABLES} variables and lists at most.")
        var_id = wanted if known is None and _is_id(wanted) and wanted not in self.ids else None
        var_id = self.new_id(var_id)
        name = self.names.make(known["name"] if known is not None else part["default"])
        self.variables[var_id] = {"name": name, "id": var_id, "type": kind}
        self.replacements[memo] = var_id
        return var_id

    # ---- blocks
    def count(self, nesting: int, levels: int) -> None:
        self.nodes += 1
        # A block's own fields can reach two brackets deeper than the block (a variable is {"id": ...} inside "fields").
        if self.nodes > MAX_NODES or nesting > MAX_NESTING or levels + 2 > MAX_JSON_DEPTH:
            raise _too_long()

    def fields(self, spec: dict[str, Any], raw: Any) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        out: dict[str, Any] = {}
        for part in _parts(spec):
            if "field" in part:
                value = raw.get(part["field"])
                scalar = isinstance(value, str | int | float | bool)
                out[part["field"]] = clean_text(value, part["max"]) if scalar else part["default"]
            elif "number" in part:
                out[part["number"]] = _number(raw.get(part["number"]), part["default"])
            elif "menu" in part:
                value = raw.get(part["menu"])
                options = [option[1] for option in part["options"]]
                out[part["menu"]] = value if isinstance(value, str) and value in options else part["default"]
            elif "var" in part:
                state = raw.get(part["var"])
                out[part["var"]] = {"id": self.variable_for(part, state.get("id") if isinstance(state, dict) else None)}
            elif "asset" in part:
                value = raw.get(part["asset"])
                out[part["asset"]] = clean_text(value, MAX_ASSET_NAME) if isinstance(value, str) else ""
        return out

    def block(self, raw: dict[str, Any], nesting: int, levels: int) -> dict[str, Any]:
        """One block (not the ones after it), with its sockets filled in from what is allowed in them."""
        self.count(nesting, levels)
        spec = catalogue.BLOCKS[raw["type"]]
        node: dict[str, Any] = {"type": raw["type"], "id": self.new_id(raw.get("id"))}
        fields = self.fields(spec, raw.get("fields"))
        if fields:
            node["fields"] = fields
        inputs = self.inputs(spec, raw.get("inputs"), nesting, levels)
        if inputs:
            node["inputs"] = inputs
        # Blockly writes a turned-off block as "disabledReasons" (older versions wrote "enabled": false).
        if raw.get("enabled") is False or raw.get("disabled") is True or raw.get("disabledReasons"):
            node["disabledReasons"] = [MANUALLY_DISABLED]
        if raw.get("collapsed") is True:
            node["collapsed"] = True
        if raw["type"] in catalogue.LOCKED_TYPES:
            node["deletable"] = False
        return node

    def inputs(self, spec: dict[str, Any], raw: Any, nesting: int, levels: int) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        out: dict[str, Any] = {}
        for part in _parts(spec):
            name = part.get("statements") or part.get("input")
            slot = raw.get(name) if name else None
            if not isinstance(slot, dict):
                continue
            below = levels + 3  # the block's brackets, then "inputs", then the socket, then the block inside it
            if "statements" in part:
                stack = self.stack(slot.get("block"), nesting + 1, below, top=False)
                if stack:
                    out[name] = {"block": stack[0]}
                continue
            entry: dict[str, Any] = {}
            for kind in ("shadow", "block"):
                child = slot.get(kind)
                child_spec = _spec(child)
                if (
                    child_spec is None
                    or not _fits(part, child_spec)
                    or (kind == "shadow" and child_spec["shape"] != "literal")
                ):
                    self.dropped += isinstance(child, dict)
                    continue
                entry[kind] = self.block(child, nesting + 1, below)  # a value block has no blocks after it
            if entry:
                out[name] = entry
        return out

    def stack(self, raw: Any, nesting: int, levels: int, *, top: bool) -> list[dict[str, Any]]:
        """A block and the ones after it, linked up. Blocks that cannot sit where they are (and the ones after) go."""
        nodes: list[dict[str, Any]] = []
        above: str | None = None  # the shape of the block this one would hang from
        while isinstance(raw, dict):
            spec = _spec(raw)
            if spec is None:
                self.dropped += 1  # an unknown block: leave it out and carry on with the blocks below it
            else:
                shape = spec["shape"]
                if above is None:
                    allowed = top or shape in _PREVIOUS  # anything can lie on its own; only a statement fits in a slot
                else:
                    allowed = shape in _PREVIOUS and above in _NEXT
                if not allowed:
                    self.dropped += 1
                    break
                nodes.append(self.block(raw, nesting, levels + 2 * len(nodes)))
                above = shape
            following = raw.get("next")
            raw = following.get("block") if isinstance(following, dict) else None
        for upper, lower in zip(nodes, nodes[1:], strict=False):
            upper["next"] = {"block": lower}
        return nodes


# --------------------------------------------------------------------------------------------- the whole workspace


def _clean_variables(raw: Any) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    names = _Names()
    if isinstance(raw, list):
        for item in raw[:MAX_VARIABLES]:
            if not isinstance(item, dict) or not _is_id(item.get("id")) or item["id"] in found:
                continue
            kind = "List" if item.get("type") == "List" else ""
            found[item["id"]] = {"name": names.make(item.get("name")), "id": item["id"], "type": kind}
    return found


def clean_workspace(raw: Any) -> Cleaned:
    """The workspace rebuilt from what is allowed. Raises ``WorkspaceTooBig`` for something no editor could have made."""
    if not isinstance(raw, dict):
        raise WorkspaceTooBig("A workspace must be an object.")
    cleaner = _Cleaner(_clean_variables(raw.get("variables")))
    blocks = raw.get("blocks")
    raw_top = blocks.get("blocks") if isinstance(blocks, dict) else None

    heads: list[dict[str, Any]] = []
    locked: set[str] = set()
    for item in raw_top if isinstance(raw_top, list) else []:
        stack = cleaner.stack(item, 0, 4, top=True)  # the workspace, "blocks", the list, then the first block
        if not stack:
            continue
        head = stack[0]
        if head["type"] in catalogue.LOCKED_TYPES:
            if head["type"] in locked:  # one of each
                cleaner.dropped += 1
                continue
            locked.add(head["type"])
        position = item if isinstance(item, dict) else {}
        head["x"], head["y"] = _coordinate(position.get("x")), _coordinate(position.get("y"))
        heads.append(head)

    lowest = max((head["y"] for head in heads), default=0)
    for type_ in catalogue.LOCKED_TYPES:
        if type_ in locked:
            continue
        node: dict[str, Any] = {"type": type_, "id": cleaner.new_id(type_.removeprefix("sff_bot_"))}
        fields = cleaner.fields(catalogue.BLOCKS[type_], {})
        if fields:
            node["fields"] = fields
        node["deletable"] = False
        node["x"] = 40
        if type_ == "sff_bot_setup":
            node["y"] = 40
            heads.insert(0, node)
        else:
            node["y"] = min(lowest + 160, MAX_COORDINATE)
            heads.append(node)

    workspace = {
        "blocks": {"languageVersion": 0, "blocks": heads},
        "variables": [
            {"name": v["name"], "id": v["id"], **({"type": "List"} if v["type"] == "List" else {})}
            for v in _grouped_by_type(list(cleaner.variables.values()))
        ],
    }
    return Cleaned(workspace, cleaner.dropped)


def _grouped_by_type(variables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Variables the way Blockly lists them: grouped by type, the types in the order they first appear.

    Blockly keeps one table per type, so it hands its variables back grouped. Giving them in that order here means a
    workspace the editor loads and saves again comes back exactly as it was sent."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for variable in variables:
        groups.setdefault(variable["type"], []).append(variable)
    return [variable for group in groups.values() for variable in group]


def dumps(workspace: dict[str, Any]) -> str:
    return json.dumps(workspace, ensure_ascii=False, separators=(",", ":"))


def _no_constants(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in JSON")


def loads(text: str | bytes) -> Any:
    """Parses stored or posted JSON strictly (no NaN or Infinity).

    Raises ``ValueError`` for text that is not JSON and for numbers with thousands of digits, and ``WorkspaceTooBig``
    (also a ``ValueError``) when the brackets are nested too deeply to read.
    """
    try:
        return json.loads(text, parse_constant=_no_constants)
    except RecursionError:
        raise _too_long() from None


def from_text(text: str | None) -> tuple[dict[str, Any], int, bool]:
    """(workspace, blocks removed, damaged) for what is stored. Nothing stored yet gives the starting workspace."""
    if not text:
        return default_workspace(), 0, False
    try:
        cleaned = clean_workspace(loads(text))
    except ValueError:
        return default_workspace(), 0, True
    return cleaned.workspace, cleaned.dropped, False


def files_used(workspace: dict[str, Any]) -> Counter[str]:
    """How many blocks name each uploaded file. Expects a workspace that ``clean_workspace`` returned."""
    used: Counter[str] = Counter()
    pending = list(workspace["blocks"]["blocks"])
    while pending:
        node = pending.pop()
        for part in _parts(catalogue.BLOCKS[node["type"]]):
            name = node.get("fields", {}).get(part.get("asset", ""))
            if name:
                used[name] += 1
        if "next" in node:
            pending.append(node["next"]["block"])
        for slot in node.get("inputs", {}).values():
            pending.extend(slot.values())
    return used
