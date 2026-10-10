"""Workspaces built from the block catalogue, for tests that need many of them.

Four things live here:

* ``filled`` and ``placed``: any block with every part filled in, sitting in a script where it can compile;
* ``Randomizer``: random workspaces of every block, for tests that only look for crashes and unsafe code;
* ``mutate``: damages a workspace's JSON in random ways (wrong types, missing parts, absurd numbers);
* ``Programs``: random bot programs that come with the output they should produce. The expected output is worked
  out while the program is built, by plain Python closures that never look at the compiler's code, so it is an
  independent check on what the compiler writes (precedence, quoting, number and text conversions, loops).
"""

from __future__ import annotations

import ast
import copy
import json
import math
import operator
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from blocks import (
    block,
    boolean,
    change_var,
    command,
    compare,
    hat,
    if_,
    if_else,
    join,
    list_variable,
    logic,
    math_op,
    not_,
    number,
    on_message,
    on_ready,
    repeat,
    reply,
    say,
    set_var,
    show,
    var_id,
    variable,
    wait,
    workspace,
)

from scratch_fluxer import catalogue, codecheck
from scratch_fluxer.compiler import MAX_NUMBER, CompileResult, clean_text

PART_KEYS = ("input", "field", "number", "menu", "var", "asset", "statements")
PLAIN_VARIABLE, LIST_VARIABLE = "score", "fruits"
VARIABLES = (PLAIN_VARIABLE, (LIST_VARIABLE, "List"))
FILE_FOR_KIND = {"image": "cat.png", "json": "data.json", "text": "notes.txt"}
UPLOADED = {name: kind for kind, name in FILE_FOR_KIND.items()}

#: Text that tries to be something else. Everything here must come out of the compiler as plain text.
HOSTILE_TEXT = [
    '"); import os; os.system("id"); ("',
    "'); import os; os.system('id'); ('",
    '"""',
    "'''",
    '"',
    "'",
    "\\",
    "\\\\",
    '\\"',
    "\\'",
    "\\\n",
    "\\N{DIGIT ONE}",
    "\\u0041",
    "\\x41",
    "\\0",
    "{",
    "}",
    "{}",
    "{{}}",
    "{0}",
    "{x}",
    "{__import__('os').system('id')}",
    "{message.author.__class__}",
    "{0.__class__.__mro__}",
    "f'{1+1}'",
    "%s %d %(x)s",
    "${jndi:ldap://x}",
    "{{7*7}}",
    "<script>alert(1)</script>",
    "# not a comment",
    "-- not a comment",
    "/* not a comment */",
    "a\nb",
    "a\r\nb",
    "a\tb",
    " ",
    " ",
    "\u0085",
    " ",
    "​",
    "﻿",
    "😀",
    "👨‍👩‍👧",
    "مرحبا بالعالم",
    "日本語のテキスト",
    "naïve café",
    "é",
    "`backticks`",
    "__import__('os').system('id')",
    "os.environ['FLUXER_TOKEN']",
    "FLUXER_TOKEN",
    "bot.run(os.environ['FLUXER_TOKEN'])",
    "x" * 2500,  # longer than a text box may hold
    "",
    " ",
]

#: Text with characters the compiler removes (control characters, direction overrides, lone surrogates).
STRIPPED_TEXT = ["a\x00b", "a\x1b[31mb", "a\x7fb", "‮evil", "a⁦b⁩", "a\ud800b", "\x08\x0b\x0c"]


def parts_of(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [part for line in spec["lines"] for part in line if isinstance(part, dict)]


def name_of(part: dict[str, Any]) -> str:
    return next(part[key] for key in PART_KEYS if key in part)


# --------------------------------------------------------------------------------------------- what every result must be


def string_literals(code: str) -> set[str]:
    """Every piece of text written into the code (the parts of f-strings included)."""
    return {n.value for n in ast.walk(ast.parse(code)) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def assert_sound(result: CompileResult) -> None:
    """Whatever went into the compiler, what came out is a plain result that is safe to use."""
    assert isinstance(result.code, str)
    messages = [d.message for d in result.diagnostics]
    assert not any("safety check" in m or "too complicated" in m for m in messages), messages
    json.dumps(result.as_dict())  # it all goes to the browser as JSON
    if not result.ok:
        return
    codecheck.check(result.code)
    compile(result.code, "<bot>", "exec")
    tree = ast.parse(result.code)
    token_reads = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "environ"]
    assert len(token_reads) == 1, "the token may be read in exactly one place"
    assert result.code.rstrip().endswith('bot.run(os.environ["FLUXER_TOKEN"])')
    total = len(result.code.splitlines())
    for block_id, (first, last) in result.lines.items():
        assert isinstance(block_id, str) and 1 <= first <= last <= total, (block_id, first, last, total)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool):
            assert math.isfinite(node.value) and abs(node.value) <= MAX_NUMBER, node.value


# --------------------------------------------------------------------------------------------- every block, filled in


def filled(type_: str, *, words: str = "Hi", count: int | float = 1, file: str | None = None) -> dict[str, Any]:
    """The block with every part filled in: ``words`` in text boxes and sockets, ``count`` in number boxes."""
    fields: dict[str, Any] = {}
    inputs: dict[str, Any] = {}
    for part in parts_of(catalogue.BLOCKS[type_]):
        name = name_of(part)
        if "input" in part:
            kind = part["type"]
            if kind == "number":
                inputs[name] = count
            elif kind == "boolean":
                inputs[name] = boolean(True)
            elif kind == "list":
                inputs[name] = list_variable(LIST_VARIABLE)
            else:
                inputs[name] = words
        elif "field" in part:
            fields[name] = words
        elif "number" in part:
            fields[name] = count
        elif "menu" in part:
            fields[name] = part["default"]
        elif "var" in part:
            fields[name] = {"id": var_id(LIST_VARIABLE if part["kinds"] == ["List"] else PLAIN_VARIABLE)}
        elif "asset" in part:
            fields[name] = file or FILE_FOR_KIND[part["kinds"][0]]
        elif "statements" in part:
            inputs[name] = [show(words)]
    return block(type_, fields=fields, inputs=inputs)


def script_for(needs: tuple[str, ...] | None) -> Callable[..., dict[str, Any]]:
    """The kind of script that offers what a block needs (a message, a command's text, a member)."""
    wanted = (needs or ("",))[0]
    if wanted == "message":
        return on_message
    if wanted == "command":
        return lambda *steps: command("go", *steps)
    if wanted == "member":
        return lambda *steps: hat("sff_event_member_join", *steps)
    return on_ready


def placed(type_: str, node: dict[str, Any]) -> dict[str, Any]:
    """A whole workspace with ``node`` where it can be compiled (a value is shown on the console)."""
    spec = catalogue.BLOCKS[type_]
    shape = spec["shape"]
    if shape == "config":
        return workspace(variables=VARIABLES)
    if shape == "hat":
        return workspace(node, variables=VARIABLES)
    make = script_for(spec["needs"])
    if shape in ("statement", "cap"):
        return workspace(make(node), variables=VARIABLES)
    return workspace(make(show(node)), variables=VARIABLES)


# --------------------------------------------------------------------------------------------- random workspaces

NUMBER_FIELDS: list[Any] = [
    0,
    1,
    -1,
    2,
    7,
    10,
    100,
    0.5,
    -2.5,
    1e3,
    99999,
    10**15,
    10**15 + 1,
    10**30,
    10**400,
    10**5000,
    1e300,
    float("nan"),
    float("inf"),
    float("-inf"),
    "5",
    " 5 ",
    "abc",
    "",
    "1e999",
    "nan",
    True,
    False,
    None,
    [],
    {},
    [1],
    {"a": 1},
]


#: What each event offers the blocks under it (a message, the text of a command, a member).
HAT_PROVIDES = {
    "sff_event_ready": set(),
    "sff_event_message": {"message"},
    "sff_event_command": {"message", "command"},
    "sff_event_member_join": {"member"},
    "sff_event_member_leave": {"member"},
}


class Randomizer:
    """Random workspaces made from the catalogue: mostly sensible, sometimes wrong in the ways people get things wrong."""

    def __init__(self, rng: random.Random, *, mistakes: float = 0.1, files: dict[str, str] | None = None) -> None:
        self.rng = rng
        self.mistakes = mistakes
        self.files = files if files is not None else dict(UPLOADED)
        self.values = [t for t, s in catalogue.BLOCKS.items() if s["shape"] in ("value", "boolean")]
        self.statements = [t for t, s in catalogue.BLOCKS.items() if s["shape"] in ("statement", "cap")]
        self.hats = list(catalogue.HATS)
        self.variables = [("a", ""), ("b", ""), ("total", ""), ("names", "List"), ("more", "List")]
        self.provides: set[str] = set()  # what the script being built offers

    def choose(self, pool: list[str]) -> str:
        """A block type from the pool: one the script under construction can use, unless this is a mistake."""
        if not self.chance(self.mistakes):
            fitting = [t for t in pool if self.fits(t)]
            pool = fitting or pool
        return self.rng.choice(pool)

    def fits(self, type_: str) -> bool:
        needs = catalogue.BLOCKS[type_]["needs"]
        return not needs or any(need in self.provides for need in needs)

    # ---- helpers
    def chance(self, p: float) -> bool:
        return self.rng.random() < p

    def text(self) -> str:
        pool = HOSTILE_TEXT[:-1] if self.chance(0.9) else HOSTILE_TEXT + STRIPPED_TEXT
        return self.rng.choice(pool)

    def variable_field(self, kinds: list[str]) -> Any:
        if self.chance(self.mistakes / 2):
            return self.rng.choice([None, {}, {"id": "nope"}, "a", 5, {"id": 5}, ["a"]])
        wrong = self.chance(self.mistakes)
        pool = [name for name, kind in self.variables if (kind in kinds) != wrong] or [n for n, _k in self.variables]
        return {"id": var_id(self.rng.choice(pool))}

    # ---- parts
    def value_for(self, kind: str, depth: int) -> Any:
        """What goes in a value socket: a text or number box, a block, or nothing."""
        r = self.rng
        if depth <= 0 or self.chance(0.45):
            if self.chance(0.15):
                return None
            if kind == "boolean":
                return boolean(self.chance(0.5)) if self.chance(0.7) else None
            if kind == "list":
                return list_variable(r.choice(["names", "more"])) if self.chance(0.8) else None
            if kind == "number":
                return r.choice([0, 1, 2, 3, 10, -4, 0.5, "7", "abc"])
            return self.text() if self.chance(0.7) else r.choice([0, 1, 2.5, "12"])
        if kind == "list" and self.chance(0.6):
            return list_variable(r.choice(["names", "more"]))
        pool = self.values if self.chance(self.mistakes) else self.values_returning(kind)
        return self.make(self.choose(pool), depth - 1)

    def values_returning(self, kind: str) -> list[str]:
        ok = {"text": {"text", "number", "boolean", "list", "any"}, "any": {"text", "number", "boolean", "list", "any"}}
        wanted = ok.get(kind, {kind, "any"})
        found = [t for t in self.values if catalogue.BLOCKS[t]["returns"] in wanted]
        return found or self.values

    def part(self, part: dict[str, Any], depth: int, fields: dict[str, Any], inputs: dict[str, Any]) -> None:
        name = name_of(part)
        r = self.rng
        if "input" in part:
            inputs[name] = self.value_for(part["type"], depth)
        elif "field" in part:
            fields[name] = self.text() if self.chance(0.8) else r.choice(NUMBER_FIELDS)
        elif "number" in part:
            fields[name] = r.choice(NUMBER_FIELDS) if self.chance(0.4) else r.choice([0, 1, 2, 5, -3, 0.5])
        elif "menu" in part:
            options = [option[1] for option in part["options"]]
            fields[name] = (
                r.choice(options) if self.chance(1 - self.mistakes) else r.choice(["zzz", "", 5, None, ["x"]])
            )
        elif "var" in part:
            fields[name] = self.variable_field(part["kinds"])
        elif "asset" in part:
            if self.chance(self.mistakes):
                fields[name] = r.choice(["../secret.txt", "", "nope.png", "a/b.json", 5, None, "x" * 200])
            else:
                kinds = part["kinds"] if self.chance(0.9) else list(FILE_FOR_KIND)
                fields[name] = FILE_FOR_KIND[r.choice(kinds)]
        elif "statements" in part:
            inputs[name] = self.stack(depth, r.randint(0, 3)) or None  # nothing inside is an empty slot

    def make(self, type_: str, depth: int) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        inputs: dict[str, Any] = {}
        for part in parts_of(catalogue.BLOCKS[type_]):
            self.part(part, depth, fields, inputs)
        node = block(type_, fields=fields, inputs=inputs)
        if self.chance(0.03):
            node["enabled"] = False
        return node

    def stack(self, depth: int, count: int) -> list[dict[str, Any]]:
        steps = []
        for _ in range(count):
            type_ = self.choose(self.statements)
            steps.append(self.make(type_, depth - 1))
            if catalogue.BLOCKS[type_]["shape"] == "cap":
                break  # nothing can follow a block that ends the script
        return steps

    def script(self, depth: int = 4) -> dict[str, Any]:
        type_ = self.rng.choice(self.hats)
        names = ["ping", "say hi", "x1", "ping", "hello-there"]
        fields = {"NAME": self.rng.choice(names + [self.text()[:24]] if self.chance(self.mistakes) else names)}
        self.provides = HAT_PROVIDES[type_]
        steps = self.stack(depth, self.rng.randint(0, 5))
        self.provides = set()
        return hat(type_, *steps, fields=fields if type_ == "sff_event_command" else None)

    def workspace(self, *, scripts: int | None = None, depth: int = 4) -> dict[str, Any]:
        count = self.rng.randint(0, 5) if scripts is None else scripts
        top = [self.script(depth) for _ in range(count)]
        if self.chance(self.mistakes * 1.5):  # blocks lying around loose
            top.append(self.make(self.rng.choice(self.statements + self.values), 2))
        ws = workspace(*top, variables=tuple(self.variables), setup=self.chance(0.97), login=self.chance(0.97))
        if self.chance(0.1):  # a second pair of locked blocks
            ws["blocks"]["blocks"].append(block("sff_bot_setup", fields={"PREFIX": "?"}, x=0, y=1))
        return ws


# --------------------------------------------------------------------------------------------- damaging workspaces


def chaos_value(rng: random.Random, depth: int = 0) -> Any:
    """Any JSON-like value at all (and a few that JSON can't hold but Python can: nan, infinity)."""
    kinds = ["none", "bool", "int", "float", "big", "str", "str", "list", "dict", "nan", "inf", "type", "block"]
    kind = rng.choice(kinds if depth < 3 else kinds[:7])
    if kind == "none":
        return None
    if kind == "bool":
        return rng.choice([True, False])
    if kind == "int":
        return rng.choice([0, 1, -1, 2**31, -(2**31), 2**63, 10**15, 10**15 + 1])
    if kind == "float":
        return rng.choice([0.0, -0.0, 0.5, 1e-9, 1e300, -1e300])
    if kind == "big":
        return rng.choice([10**30, 10**400, -(10**400), 10**5000])
    if kind == "str":
        return rng.choice(HOSTILE_TEXT + STRIPPED_TEXT + ["sff_reply", "sff_text", "true", "online", "TEXT", "next"])
    if kind == "nan":
        return float("nan")
    if kind == "inf":
        return rng.choice([float("inf"), float("-inf")])
    if kind == "type":
        return rng.choice(list(catalogue.BLOCKS))
    if kind == "list":
        return [chaos_value(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    if kind == "block":
        return {"type": rng.choice(list(catalogue.BLOCKS)), "id": f"c{rng.randint(0, 9)}", "fields": {}, "inputs": {}}
    return {
        rng.choice(
            ["type", "id", "fields", "inputs", "next", "block", "shadow", "x", "y", "TEXT", "A", "DO", "VAR"]
        ): chaos_value(rng, depth + 1)
        for _ in range(rng.randint(0, 3))
    }


def _containers(node: Any, found: list[Any]) -> None:
    if isinstance(node, dict):
        found.append(node)
        for value in list(node.values()):
            _containers(value, found)
    elif isinstance(node, list):
        found.append(node)
        for value in node:
            _containers(value, found)


def mutate(rng: random.Random, state: Any, changes: int) -> Any:
    """A copy of ``state`` with ``changes`` random damages: values swapped for others, keys removed or added."""
    state = copy.deepcopy(state)
    for _ in range(changes):
        found: list[Any] = []
        _containers(state, found)
        if not found:
            break
        target = rng.choice(found)
        action = rng.choice(["replace", "replace", "delete", "add", "graft", "retype"])
        if isinstance(target, dict) and target:
            key = rng.choice(list(target))
            if action == "delete":
                del target[key]
            elif action == "graft":
                target[key] = copy.deepcopy(rng.choice(found))  # a piece of the workspace pasted somewhere else
            elif action == "retype" and "type" in target:
                target["type"] = rng.choice(list(catalogue.BLOCKS))
            else:
                target[key] = chaos_value(rng)
        elif isinstance(target, dict):
            target[rng.choice(["type", "fields", "inputs", "next", "x"])] = chaos_value(rng)
        elif isinstance(target, list) and target:
            index = rng.randrange(len(target))
            if action == "delete":
                del target[index]
            elif action == "graft":
                target[index] = copy.deepcopy(rng.choice(found))
            else:
                target[index] = chaos_value(rng)
        elif isinstance(target, list):
            target.append(chaos_value(rng))
    return state


# --------------------------------------------------------------------------------------------- programs with a known result

NUMBER_POOL: list[int | float] = [
    0,
    1,
    2,
    3,
    5,
    7,
    10,
    42,
    100,
    1000,
    -1,
    -3,
    -10,
    0.5,
    1.5,
    2.5,
    0.1,
    0.25,
    -0.5,
    123456,
]
NUMBER_TEXTS: list[tuple[str, int | float]] = [("7", 7), ("2.5", 2.5), ("-3", -3), ("0", 0), ("abc", 0), ("", 0)]
PARSE_TEXTS = ["12", "2.5", "-3", "0", "abc", "", " 7 ", "1e2", "12abc", "٣", "1_000", "nan", "inf"]
#: Words that can never be read as numbers, so "equals" means the same thing however the compiler writes it.
PLAIN_WORDS = ["abc", "ABC", "hello", "Hello", "héllo", "", "a b", "x", "😀"]
#: Text the compiler keeps exactly as it is (it removes control characters; the safety tests cover that).
TEXT_POOL = [text_ for text_ in HOSTILE_TEXT if text_ and len(text_) < 200 and clean_text(text_) == text_] + [
    "Hello World",
    "  padded  ",
    "UPPER lower",
    "one two three four",
    "5",
    "5.0",
    "007",
    "1e3",
    "-0",
    "12",
]

OPERATORS = {
    "add": operator.add,
    "subtract": operator.sub,
    "multiply": operator.mul,
    "divide": operator.truediv,
    "mod": operator.mod,
}
COMPARERS = {
    "lt": operator.lt,
    "gt": operator.gt,
    "le": operator.le,
    "ge": operator.ge,
    "eq": operator.eq,
    "ne": operator.ne,
}
ROUNDERS = {"round": round, "floor": math.floor, "ceil": math.ceil, "abs": abs}
CASERS = {"upper": str.upper, "lower": str.lower, "title": str.title, "strip": str.strip}


def shown(value: Any) -> str:
    """What the console and a join make of any value."""
    return str(value)


def parse_number(text_: str) -> int | float:
    text_ = text_.strip()
    try:
        return int(text_)
    except ValueError:
        pass
    try:
        value = float(text_)
    except ValueError:
        return 0
    return value if math.isfinite(value) else 0


def same(a: Any, b: Any) -> bool:
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


class Stop(Exception):
    """A "stop this script" block ran."""


@dataclass
class Model:
    """The state of the program the oracle is running: its variables and everything it wrote."""

    variables: dict[str, Any]
    command_text: str = ""
    printed: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)


Eval = Callable[[Model], Any]
Piece = tuple[Any, Eval]  # (a block, or a plain value for a text/number box; how to work out its value)
Step = tuple[dict[str, Any], Callable[[Model], None]]


def const(value: Any) -> Eval:
    return lambda m: value


@dataclass
class Program:
    seed: int
    ws: dict[str, Any]
    trigger: str  # "ready", or the text of the message that starts the script
    printed: list[str]
    replies: list[str]
    error: str | None  # the exception the script should end with, if any

    @property
    def stdout(self) -> str:
        return "".join(f"{line}\n" for line in self.printed)


class Programs:
    """Builds one random program and works out what it should do."""

    NUMBERS = ("n1", "n2", "n3")
    TEXTS = ("t1", "t2")
    LISTS = ("l1", "l2")

    def __init__(self, seed: int, *, command_script: bool | None = None, loops: bool = True) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.command_script = self.rng.random() < 0.4 if command_script is None else command_script
        self.loops = loops
        self.counters = 0
        self.item_in_scope = False
        self.lists_locked = False

    # ---- pieces: numbers
    def num(self, depth: int, *, typed: bool = True) -> Piece:
        """A number. ``typed`` is True for a socket that turns text and yes/no values into numbers (math, "change
        by"), False for one that keeps what it is given (what a variable is set to, a list item, the console)."""
        r = self.rng
        options = ["lit", "lit", "var", "var", "numtext"]
        if depth > 0:
            options += ["math"] * 4 + ["round", "length", "parse", "list_length"]
            if typed:
                options.append("from_bool")
        pick = r.choice(options)
        if pick == "lit":
            value = r.choice(NUMBER_POOL)
            return (number(value) if r.random() < 0.5 else value), const(value)
        if pick == "numtext":  # a number typed into a text box; only a socket that wants a number reads "abc" as 0
            written, value = r.choice([entry for entry in NUMBER_TEXTS if typed or entry[1] != 0 or entry[0] == "0"])
            return written, const(value)
        if pick == "var":
            name = r.choice(self.NUMBERS)
            return variable(name), lambda m: m.variables[name]
        if pick == "math":
            op = r.choice(list(OPERATORS))
            a, b = self.num(depth - 1), self.num(depth - 1)
            if op == "multiply":  # keep the numbers small: one side is always a little literal
                small = r.choice([-3, -1, 0, 1, 2, 3, 10])
                a, b = (a, (small, const(small))) if r.random() < 0.5 else ((small, const(small)), b)
            elif op in ("divide", "mod") and r.random() < 0.8:  # mostly something that can be divided by
                divisor = r.choice([1, 2, 3, 4, 5, -2, 0.5, 7, 10])
                b = (divisor, const(divisor))
            return math_op(a[0], op, b[0]), lambda m, f=OPERATORS[op], a=a[1], b=b[1]: f(a(m), b(m))
        if pick == "round":
            op = r.choice(list(ROUNDERS))
            x = self.num(depth - 1)
            node = block("sff_round", fields={"OP": op}, inputs={"N": x[0]})
            return node, lambda m, f=ROUNDERS[op], x=x[1]: f(x(m))
        if pick == "length":
            x = self.anything(depth - 1)
            node = block("sff_text_length", inputs={"TEXT": x[0]})
            return node, lambda m, x=x[1]: len(shown(x(m)))
        if pick == "parse":
            if r.random() < 0.5:
                piece = self.txt(depth - 1)
            else:
                written = r.choice(PARSE_TEXTS)
                piece = (written, const(written))
            node = block("sff_to_number", inputs={"TEXT": piece[0]})
            return node, lambda m, x=piece[1]: parse_number(shown(x(m)))
        if pick == "list_length":
            name = r.choice(self.LISTS)
            node = block("sff_list_length", fields={"LIST": {"id": var_id(name)}})
            return node, lambda m: len(m.variables[name])
        cond = self.cond(depth - 1)
        return cond[0], lambda m, c=cond[1]: int(c(m))

    # ---- pieces: text
    def txt(self, depth: int) -> Piece:
        r = self.rng
        options = ["lit", "lit", "var"]
        if self.item_in_scope:
            options.append("item")
        if self.command_script:
            options.append("command_text")
        if depth > 0:
            options += ["join"] * 3 + ["case", "replace", "word", "list_join", "list_item"]
        pick = r.choice(options)
        if pick == "lit":
            value = r.choice(TEXT_POOL)
            return value, const(value)
        if pick == "var":
            name = r.choice(self.TEXTS)
            return variable(name), lambda m: m.variables[name]
        if pick == "item":
            return variable("item"), lambda m: m.variables["item"]
        if pick == "command_text":
            return block("sff_command_text"), lambda m: m.command_text
        if pick == "join":
            a, b = self.anything(depth - 1), self.anything(depth - 1)
            return join(a[0], b[0]), lambda m, a=a[1], b=b[1]: shown(a(m)) + shown(b(m))
        if pick == "case":
            op = r.choice(list(CASERS))
            x = self.anything(depth - 1)
            node = block("sff_text_case", fields={"OP": op}, inputs={"TEXT": x[0]})
            return node, lambda m, f=CASERS[op], x=x[1]: f(shown(x(m)))
        if pick == "replace":
            x, a, b = self.anything(depth - 1), self.anything(depth - 1), self.anything(depth - 1)
            node = block("sff_replace", inputs={"TEXT": x[0], "A": a[0], "B": b[0]})
            return node, lambda m, x=x[1], a=a[1], b=b[1]: shown(x(m)).replace(shown(a(m)), shown(b(m)))
        if pick == "word":
            x, n = self.anything(depth - 1), self.num(depth - 1)
            node = block("sff_word", inputs={"TEXT": x[0], "N": n[0]})

            def nth_word(m: Model, x: Eval = x[1], n: Eval = n[1]) -> str:
                words, index = shown(x(m)).split(), int(n(m))
                return words[index - 1] if 0 < index <= len(words) else ""

            return node, nth_word
        if pick == "list_join":
            name, sep = r.choice(self.LISTS), self.anything(depth - 1)
            node = block("sff_list_join", fields={"LIST": {"id": var_id(name)}}, inputs={"SEP": sep[0]})
            return node, lambda m, sep=sep[1]: shown(sep(m)).join(shown(item) for item in m.variables[name])
        name, n = r.choice(self.LISTS), self.num(depth - 1)
        node = block("sff_list_item", fields={"LIST": {"id": var_id(name)}}, inputs={"N": n[0]})

        def nth_item(m: Model, n: Eval = n[1]) -> Any:
            items, index = m.variables[name], int(n(m))
            return items[index - 1] if 0 < index <= len(items) else ""

        return node, nth_item

    # ---- pieces: anything, and yes/no
    def anything(self, depth: int) -> Piece:
        """Any value, for a socket that keeps what it is given."""
        pick = self.rng.choice(["txt", "txt", "num", "cond"] if depth > 0 else ["txt", "num"])
        if pick == "num":
            return self.num(depth, typed=False)
        return self.txt(depth) if pick == "txt" else self.cond(depth)

    def plain(self) -> Piece:
        if self.rng.random() < 0.5:
            value = self.rng.choice(PLAIN_WORDS)
            return value, const(value)
        return variable("p1"), lambda m: m.variables["p1"]

    def cond(self, depth: int) -> Piece:
        r = self.rng
        options = ["lit", "compare", "compare", "same_text"]
        if depth > 0:
            options += ["logic", "logic", "not", "contains", "list_has"]
        pick = r.choice(options)
        if pick == "lit":
            value = r.random() < 0.5
            return boolean(value), const(value)
        if pick == "compare":
            op = r.choice(list(COMPARERS))
            typed = op not in ("eq", "ne")  # "<" reads text as a number; "=" compares what it is given
            a, b = self.num(max(depth - 1, 0), typed=typed), self.num(max(depth - 1, 0), typed=typed)
            return compare(a[0], op, b[0]), lambda m, f=COMPARERS[op], a=a[1], b=b[1]: f(a(m), b(m))
        if pick == "same_text":
            op = r.choice(["eq", "ne"])
            a, b = self.plain(), self.plain()
            return compare(a[0], op, b[0]), lambda m, f=COMPARERS[op], a=a[1], b=b[1]: f(a(m), b(m))
        if pick == "logic":
            op = r.choice(["and", "or"])
            a, b = self.cond(depth - 1), self.cond(depth - 1)
            if op == "and":
                return logic(a[0], op, b[0]), lambda m, a=a[1], b=b[1]: a(m) and b(m)
            return logic(a[0], op, b[0]), lambda m, a=a[1], b=b[1]: a(m) or b(m)
        if pick == "not":
            a = self.cond(depth - 1)
            return not_(a[0]), lambda m, a=a[1]: not a(m)
        if pick == "contains":
            a, b = self.anything(depth - 1), self.anything(depth - 1)
            node = block("sff_contains", inputs={"A": a[0], "B": b[0]})
            return node, lambda m, a=a[1], b=b[1]: shown(b(m)).lower() in shown(a(m)).lower()
        name = r.choice(self.LISTS)
        item = self.num(0, typed=False) if r.random() < 0.5 else self.plain()
        node = block("sff_list_contains", fields={"LIST": {"id": var_id(name)}}, inputs={"ITEM": item[0]})
        return node, lambda m, item=item[1]: any(same(x, item(m)) for x in m.variables[name])

    # ---- steps
    def step(self, depth: int) -> Step:
        r = self.rng
        options = ["print", "print", "set_n", "change_n", "set_t", "wait"]
        if not self.lists_locked:
            options += ["list_add", "list_add", "list_delete", "list_clear", "list_set"]
        if self.command_script:
            options += ["reply", "say"]
        if depth > 0:
            options += ["if", "if_else", "repeat"]
            if self.loops:
                options += ["repeat_until", "for_each"]
        pick = r.choice(options)
        return getattr(self, f"step_{pick}")(depth)

    def stack_of(self, depth: int, count: int) -> list[Step]:
        steps: list[Step] = []
        for _ in range(count):
            steps.append(self.step(depth))
        if self.rng.random() < 0.08:
            steps.append((block("sff_stop_script"), self.stop))
        return steps

    @staticmethod
    def stop(m: Model) -> None:
        raise Stop

    @staticmethod
    def run(steps: list[Step], m: Model) -> None:
        for _node, effect in steps:
            effect(m)

    def step_print(self, depth: int) -> Step:
        x = self.anything(depth)
        return show(x[0]), lambda m, x=x[1]: m.printed.append(shown(x(m)))

    def step_reply(self, depth: int) -> Step:
        x = self.anything(depth)
        return reply(x[0]), lambda m, x=x[1]: m.replies.append(shown(x(m)))

    def step_say(self, depth: int) -> Step:
        x = self.anything(depth)
        return say(x[0]), lambda m, x=x[1]: m.replies.append(shown(x(m)))

    def step_wait(self, depth: int) -> Step:
        return wait(0), lambda m: None

    def step_set_n(self, depth: int) -> Step:
        name, x = self.rng.choice(self.NUMBERS), self.num(depth, typed=False)

        def effect(m: Model) -> None:
            m.variables[name] = x[1](m)

        return set_var(name, x[0]), effect

    def step_change_n(self, depth: int) -> Step:
        name, x = self.rng.choice(self.NUMBERS), self.num(depth)

        def effect(m: Model) -> None:
            m.variables[name] += x[1](m)

        return change_var(name, x[0]), effect

    def step_set_t(self, depth: int) -> Step:
        name, x = self.rng.choice(self.TEXTS), self.txt(depth)

        def effect(m: Model) -> None:
            m.variables[name] = x[1](m)

        return set_var(name, x[0]), effect

    def step_list_add(self, depth: int) -> Step:
        name, x = self.rng.choice(self.LISTS), self.anything(depth)
        node = block("sff_list_add", fields={"LIST": {"id": var_id(name)}}, inputs={"ITEM": x[0]})
        return node, lambda m: m.variables[name].append(x[1](m))

    def step_list_delete(self, depth: int) -> Step:
        name, n = self.rng.choice(self.LISTS), self.num(depth)
        node = block("sff_list_delete", fields={"LIST": {"id": var_id(name)}}, inputs={"N": n[0]})

        def effect(m: Model) -> None:
            items, index = m.variables[name], int(n[1](m))
            if 0 < index <= len(items):
                del items[index - 1]

        return node, effect

    def step_list_clear(self, depth: int) -> Step:
        name = self.rng.choice(self.LISTS)
        node = block("sff_list_clear", fields={"LIST": {"id": var_id(name)}})
        return node, lambda m: m.variables[name].clear()

    def step_list_set(self, depth: int) -> Step:
        name, source = self.rng.choice(self.LISTS), self.rng.choice(self.LISTS)
        if self.rng.random() < 0.5:  # a copy of another list
            value: Piece = (list_variable(source), lambda m: m.variables[source])
        else:
            text_, sep = self.txt(max(depth - 1, 0)), self.rng.choice([" ", ",", "a", ""])
            node = block("sff_list_split", inputs={"TEXT": text_[0], "SEP": sep})
            value = (node, lambda m: shown(text_[1](m)).split(sep or None))
        node = block("sff_list_set", fields={"LIST": {"id": var_id(name)}}, inputs={"VALUE": value[0]})

        def effect(m: Model) -> None:
            m.variables[name] = list(value[1](m))

        return node, effect

    def step_if(self, depth: int) -> Step:
        c, then = self.cond(depth), self.stack_of(depth - 1, self.rng.randint(0, 3))
        node = if_(c[0], *[n for n, _e in then])

        def effect(m: Model) -> None:
            if c[1](m):
                self.run(then, m)

        return node, effect

    def step_if_else(self, depth: int) -> Step:
        c = self.cond(depth)
        then, otherwise = (
            self.stack_of(depth - 1, self.rng.randint(0, 3)),
            self.stack_of(depth - 1, self.rng.randint(0, 3)),
        )
        node = if_else(c[0], [n for n, _e in then], [n for n, _e in otherwise])

        def effect(m: Model) -> None:
            self.run(then if c[1](m) else otherwise, m)

        return node, effect

    def step_repeat(self, depth: int) -> Step:
        times = self.rng.choice([0, 1, 2, 3, "2", "3"])
        body = self.stack_of(depth - 1, self.rng.randint(0, 3))
        node = repeat(times, *[n for n, _e in body])

        def effect(m: Model) -> None:
            for _ in range(int(times)):
                self.run(body, m)

        return node, effect

    def step_repeat_until(self, depth: int) -> Step:
        """A loop that is sure to end: a counter nothing else touches goes up by one on every round."""
        self.counters += 1
        counter, limit = f"c{self.counters}", self.rng.randint(0, 3)
        body = self.stack_of(depth - 1, self.rng.randint(0, 3))
        bump = change_var(counter, 1)
        loop = block(
            "sff_repeat_until",
            inputs={"COND": compare(variable(counter), "ge", limit), "DO": [*[n for n, _e in body], bump]},
        )
        # the counter is set to 0 first, by a step that comes right before the loop
        start = set_var(counter, 0)
        group = block("sff_if", inputs={"COND": boolean(True), "DO": [start, loop]})

        def effect(m: Model) -> None:
            m.variables[counter] = 0
            while not (m.variables[counter] >= limit):
                self.run(body, m)
                m.variables[counter] += 1

        return group, effect

    def step_for_each(self, depth: int) -> Step:
        name = self.rng.choice(self.LISTS)
        was_in_scope, was_locked = self.item_in_scope, self.lists_locked
        self.item_in_scope, self.lists_locked = True, True  # the list being walked through isn't changed meanwhile
        body = self.stack_of(depth - 1, self.rng.randint(0, 3))
        self.item_in_scope, self.lists_locked = was_in_scope, was_locked
        node = block(
            "sff_for_each",
            fields={"VAR": {"id": var_id("item")}},
            inputs={"LIST": list_variable(name), "DO": [n for n, _e in body] or None},
        )

        def effect(m: Model) -> None:
            for entry in list(m.variables[name]):
                m.variables["item"] = entry
                self.run(body, m)

        return node, effect

    # ---- the whole program
    def program(self) -> Program:
        r = self.rng
        prelude: list[Step] = []
        for name in self.NUMBERS:
            value = r.choice(NUMBER_POOL)
            prelude.append(
                (set_var(name, value), lambda m, name=name, value=value: m.variables.__setitem__(name, value))
            )
        for name in (*self.TEXTS, "p1"):
            value = r.choice(PLAIN_WORDS if name == "p1" else TEXT_POOL)
            prelude.append(
                (set_var(name, value), lambda m, name=name, value=value: m.variables.__setitem__(name, value))
            )
        steps = prelude + self.stack_of(3, r.randint(1, 8))
        argument = r.choice(
            ["", "hello", "two words", "5", "  spaced   out  ", 'quote " and \\ slash', "{braces}", "😀 é"]
        )
        hat_node = command("go", *[n for n, _e in steps]) if self.command_script else on_ready(*[n for n, _e in steps])
        variables = (
            *self.NUMBERS,
            *self.TEXTS,
            "p1",
            "item",
            *[f"c{n}" for n in range(1, self.counters + 1)],
            *[(name, "List") for name in self.LISTS],
        )
        ws = workspace(hat_node, variables=variables)

        initial: dict[str, Any] = {name: 0 for name in variables if isinstance(name, str)}
        initial.update({name: [] for name in self.LISTS})
        model = Model(initial, command_text=argument.strip() if self.command_script else "")
        error = None
        try:
            self.run(steps, model)
        except Stop:
            pass
        except Exception as exc:  # the script should fail in the same way
            error = type(exc).__name__
        trigger = f"!go {argument}".rstrip() if self.command_script else "ready"
        return Program(self.seed, ws, trigger, model.printed, model.replies, error)
