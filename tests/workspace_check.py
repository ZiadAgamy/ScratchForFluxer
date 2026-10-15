"""An independent statement of what the editor can open.

``problems(workspace)`` lists everything wrong with a stored workspace. It is written from what Blockly needs (the
block definitions the editor registers, which connections fit, what each field holds), not from the code that cleans
workspaces, so the two can be compared: whatever ``clean_workspace`` returns must come back from here with no problems.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from scratch_fluxer import catalogue, embeds
from scratch_fluxer.compiler import MAX_NUMBER, clean_text
from scratch_fluxer.workspace import MAX_JSON_DEPTH, MAX_NODES, MAX_VARIABLES

BLOCK_KEYS = {"type", "id", "fields", "inputs", "next", "x", "y", "disabledReasons", "collapsed", "deletable"}
DEFINITIONS = {type_: catalogue.blockly_definition(spec) for type_, spec in catalogue.BLOCKS.items()}


def json_depth(value: Any) -> int:
    """How many brackets are nested inside each other at the deepest point (no recursion: it must cope with anything)."""
    deepest = 0
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, dict):
            deepest = max(deepest, depth + 1)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            deepest = max(deepest, depth + 1)
            pending.extend((child, depth + 1) for child in item)
    return deepest


def arguments(definition: dict[str, Any]) -> list[dict[str, Any]]:
    return [arg for key, args in definition.items() if key.startswith("args") for arg in args]


def compatible(output: list[str] | None, wanted: list[str] | None) -> bool:
    """Blockly's rule: two connections fit if either accepts anything, or they have a kind in common."""
    return output is None or wanted is None or bool(set(output) & set(wanted))


class _Check:
    def __init__(self) -> None:
        self.found: list[str] = []
        self.ids: Counter[str] = Counter()
        self.variables: dict[str, dict[str, Any]] = {}
        self.nodes = 0

    def problem(self, text: str) -> None:
        self.found.append(text)

    # ---- the whole thing
    def run(self, ws: Any) -> None:
        if not isinstance(ws, dict) or set(ws) != {"blocks", "variables"}:
            return self.problem("a workspace holds exactly 'blocks' and 'variables'")
        outer = ws["blocks"]
        if not isinstance(outer, dict) or set(outer) != {"languageVersion", "blocks"} or outer["languageVersion"] != 0:
            return self.problem("'blocks' must be {languageVersion: 0, blocks: [...]}")
        if not isinstance(outer["blocks"], list):
            return self.problem("'blocks.blocks' must be a list")
        self.declare(ws["variables"])
        try:
            json.dumps(ws, allow_nan=False)
        except (ValueError, TypeError) as exc:
            self.problem(f"not valid JSON: {exc}")
        if json_depth(ws) > MAX_JSON_DEPTH:
            self.problem(f"nested {json_depth(ws)} deep")
        for item in outer["blocks"]:
            self.stack(item, top=True)
        tops = Counter(item.get("type") for item in outer["blocks"] if isinstance(item, dict))
        for type_ in catalogue.LOCKED_TYPES:
            if tops[type_] != 1:
                self.problem(f"{tops[type_]} of {type_} (there must be exactly one)")
        if self.nodes > MAX_NODES:
            self.problem("too many blocks")

    def declare(self, raw: Any) -> None:
        if not isinstance(raw, list):
            return self.problem("'variables' must be a list")
        if len(raw) > MAX_VARIABLES:
            self.problem("too many variables")
        names: set[str] = set()
        for item in raw:
            if not isinstance(item, dict) or not set(item) <= {"name", "id", "type"} or not {"name", "id"} <= set(item):
                self.problem(f"bad variable {item!r}")
                continue
            name, var_id = item["name"], item["id"]
            if not isinstance(name, str) or not name or name != " ".join(name.split()) or len(name) > 60:
                self.problem(f"bad variable name {name!r}")
            elif name.lower() in names:
                self.problem(f"two variables are called {name!r}")
            elif clean_text(name, 60) != name:
                self.problem(f"variable name {name!r} has characters that are cleaned from text")
            names.add(str(name).lower())
            if "type" in item and item["type"] != "List":
                self.problem(f"bad variable type {item['type']!r}")
            self.use_id(var_id)
            self.variables[var_id] = item

    def use_id(self, value: Any) -> None:
        if not isinstance(value, str) or not 0 < len(value) <= 120 or not value.isprintable():
            self.problem(f"bad id {value!r}")
            return
        self.ids[value] += 1
        if self.ids[value] > 1:
            self.problem(f"the id {value!r} is used twice")

    # ---- blocks
    def stack(self, first: Any, *, top: bool) -> None:
        """A block and the ones after it."""
        node = first
        above: dict[str, Any] | None = None
        while node is not None:
            definition = self.block(node, top=top and above is None)
            if definition is None:
                return
            if above is not None and "previousStatement" not in definition:
                self.problem(f"{node['type']} cannot hang from the block above it")
            if above is None and not top and "previousStatement" not in definition:
                self.problem(f"{node['type']} cannot start the inside of a block")
            following = node.get("next")
            if following is None:
                return
            if "nextStatement" not in definition:
                self.problem(f"{node['type']} has nothing below it but 'next' is set")
                return
            if not isinstance(following, dict) or set(following) != {"block"}:
                self.problem(f"bad 'next' on {node['type']}")
                return
            above, node, top = definition, following["block"], False

    def block(self, node: Any, *, top: bool) -> dict[str, Any] | None:
        self.nodes += 1
        if not isinstance(node, dict) or not set(node) <= BLOCK_KEYS:
            self.problem(f"bad block {node!r:.120}")
            return None
        type_ = node.get("type")
        if type_ not in catalogue.BLOCKS:
            self.problem(f"unknown block type {type_!r}")
            return None
        spec, definition = catalogue.BLOCKS[type_], DEFINITIONS[type_]
        self.use_id(node.get("id"))
        for axis in ("x", "y"):
            if top != (axis in node):
                self.problem(f"{type_}: {axis} {'missing' if top else 'is only for the first block of a stack'}")
            if axis in node and not (
                isinstance(node[axis], int | float)
                and not isinstance(node[axis], bool)
                and math.isfinite(node[axis])
                and abs(node[axis]) <= 1_000_000
            ):
                self.problem(f"{type_}: bad {axis}: {node[axis]!r}")
        self.fields(type_, definition, node)
        self.inputs(type_, definition, node)
        if "disabledReasons" in node and node["disabledReasons"] != ["MANUALLY_DISABLED"]:
            self.problem(f"{type_}: bad disabledReasons {node['disabledReasons']!r}")
        if "collapsed" in node and node["collapsed"] is not True:
            self.problem(f"{type_}: bad collapsed {node['collapsed']!r}")
        if type_ in catalogue.LOCKED_TYPES:
            if node.get("deletable") is not False:
                self.problem(f"{type_} must say deletable: false")
            if not top:
                self.problem(f"{type_} must lie on its own")
        elif "deletable" in node:
            self.problem(f"{type_} must not say anything about deletable")
        if spec["shape"] in ("value", "boolean", "literal") and "output" not in definition:
            self.problem(f"{type_} has no output")
        return definition

    def fields(self, type_: str, definition: dict[str, Any], node: dict[str, Any]) -> None:
        wanted = {arg["name"]: arg for arg in arguments(definition) if arg["type"].startswith("field_")}
        have = node.get("fields")
        if not wanted:
            if have is not None:
                self.problem(f"{type_} has no fields but 'fields' is set")
            return
        if not isinstance(have, dict) or set(have) != set(wanted):
            self.problem(
                f"{type_}: fields {sorted(have) if isinstance(have, dict) else have!r}, wanted {sorted(wanted)}"
            )
            return
        for name, arg in wanted.items():
            value, kind = have[name], arg["type"]
            where = f"{type_}.{name}"
            if kind in ("field_input", "field_sff_asset"):
                limit = arg.get("maxLength", 80)
                if not isinstance(value, str) or len(value) > limit or clean_text(value, limit) != value:
                    self.problem(f"{where}: bad text {value!r:.80}")
            elif kind == "field_sff_embed":
                if not isinstance(value, str) or embeds.clean(value, files=arg["files"]) != value:
                    self.problem(f"{where}: embed options that are not in their plain form: {value!r:.80}")
            elif kind == "field_number":
                ok = isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
                if not ok or abs(value) > MAX_NUMBER or (isinstance(value, float) and value.is_integer()):
                    self.problem(f"{where}: bad number {value!r}")
            elif kind == "field_dropdown":
                if value not in [option[1] for option in arg["options"]]:
                    self.problem(f"{where}: {value!r} is not one of the options")
            elif kind == "field_variable":
                ok = isinstance(value, dict) and set(value) == {"id"} and value["id"] in self.variables
                if not ok:
                    self.problem(f"{where}: bad variable {value!r}")
                elif self.variables[value["id"]].get("type", "") not in arg["variableTypes"]:
                    self.problem(f"{where}: the variable's kind is not one this block takes")
            else:
                self.problem(f"{where}: unexpected kind of field {kind}")

    def inputs(self, type_: str, definition: dict[str, Any], node: dict[str, Any]) -> None:
        wanted = {arg["name"]: arg for arg in arguments(definition) if arg["type"].startswith("input_")}
        have = node.get("inputs")
        if have is None:
            return
        if not wanted:
            self.problem(f"{type_} has no inputs but 'inputs' is set")
            return
        if not isinstance(have, dict) or not have or not set(have) <= set(wanted):
            self.problem(f"{type_}: bad inputs {have!r:.120}")
            return
        for name, slot in have.items():
            arg = wanted[name]
            if not isinstance(slot, dict):
                self.problem(f"{type_}.{name}: the socket must be an object")
            elif arg["type"] == "input_statement":
                if set(slot) != {"block"}:
                    self.problem(f"{type_}.{name}: a slot holds exactly one 'block'")
                else:
                    self.stack(slot["block"], top=False)
            else:
                self.socket(f"{type_}.{name}", arg, slot)

    def socket(self, where: str, arg: dict[str, Any], slot: dict[str, Any]) -> None:
        if not slot or not set(slot) <= {"shadow", "block"}:
            self.problem(f"{where}: bad socket {slot!r:.120}")
            return
        for kind, child in slot.items():
            definition = self.block(child, top=False)
            if definition is None:
                continue
            if "output" not in definition:
                self.problem(f"{where}: {child['type']} has no output to plug in")
            elif not compatible(definition["output"], arg.get("check")):
                self.problem(f"{where}: {child['type']} does not fit ({definition['output']} into {arg.get('check')})")
            if kind == "shadow" and catalogue.BLOCKS[child["type"]]["shape"] != "literal":
                self.problem(f"{where}: a shadow must be a plain box, not {child['type']}")
            if "next" in child:
                self.problem(f"{where}: a block in a socket cannot have 'next'")


def problems(ws: Any) -> list[str]:
    check = _Check()
    check.run(ws)
    return check.found


def assert_valid(ws: Any) -> None:
    found = problems(ws)
    assert not found, "\n".join(found[:12])


# --------------------------------------------------------------------------------------------- looking inside


def every_block(ws: dict[str, Any]) -> list[dict[str, Any]]:
    """All the blocks, however deep (no recursion)."""
    found: list[dict[str, Any]] = []
    pending = list(ws["blocks"]["blocks"])
    while pending:
        node = pending.pop()
        found.append(node)
        if "next" in node:
            pending.append(node["next"]["block"])
        for slot in node.get("inputs", {}).values():
            pending.extend(slot.values())
    return found


def by_id(ws: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in every_block(ws)}


def chain(node: dict[str, Any] | None) -> list[str]:
    """The types of a block and the ones after it."""
    types = []
    while node is not None:
        types.append(node["type"])
        node = node.get("next", {}).get("block")
    return types
