"""A small builder for Blockly workspace JSON, shared by the compiler, API and browser tests.

    ws = workspace(command("say", reply(join("You said ", command_text()))))

Everything returns plain dicts in the shape ``Blockly.serialization.workspaces.save`` writes, so what the tests feed
the compiler is what the editor would send.
"""

from __future__ import annotations

import itertools
from typing import Any

_ids = itertools.count(1)


def uid(prefix: str = "b") -> str:
    return f"{prefix}{next(_ids)}"


def var_id(name: str) -> str:
    return f"var-{name}"


# --------------------------------------------------------------------------------------------- building blocks


def socket(value: Any) -> dict[str, Any] | None:
    """What goes in a value input: text becomes a text box, a number a number box, a dict is a block."""
    if value is None:
        return None
    if isinstance(value, bool):
        return {"block": boolean(value)}
    if isinstance(value, str):
        return {"shadow": text(value)}
    if isinstance(value, int | float):
        return {"shadow": number(value)}
    if isinstance(value, list | tuple):  # a stack of statements (the inside of an if or a loop)
        return {"block": stack(*value)}
    return {"block": value}


def block(type_: str, *, fields: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None, **extra: Any):
    node: dict[str, Any] = {"type": type_, "id": extra.pop("id", None) or uid()}
    if fields:
        node["fields"] = dict(fields)
    if inputs:
        node["inputs"] = {name: slot for name, value in inputs.items() if (slot := socket(value)) is not None}
    node.update(extra)
    return node


def stack(*blocks: dict[str, Any]) -> dict[str, Any]:
    """Joins blocks into one stack (each one's `next` is the one after it) and returns the first."""
    for upper, lower in itertools.pairwise(blocks):
        upper["next"] = {"block": lower}
    return blocks[0]


def hat(
    type_: str, *steps: dict[str, Any], fields: dict[str, Any] | None = None, x: int = 40, y: int = 0, **extra: Any
):
    node = block(type_, fields=fields, x=x, y=y, **extra)
    if steps:
        node["next"] = {"block": stack(*steps)}
    return node


def workspace(*top: dict[str, Any], variables: tuple[Any, ...] = (), setup: bool = True, login: bool = True) -> dict:
    """A whole workspace. Hats are spread down the page in the order given; the two locked blocks are added."""
    blocks = list(top)
    for index, node in enumerate(blocks):
        node.setdefault("x", 40)
        node["y"] = node.get("y") or 100 + index * 150
    if setup:
        blocks.insert(0, block("sff_bot_setup", fields={"PREFIX": "!"}, x=40, y=0, id="setup", deletable=False))
    if login:
        blocks.append(block("sff_bot_login", x=40, y=9000, id="login", deletable=False))
    declared = []
    for entry in variables:
        name, kind = (entry, "") if isinstance(entry, str) else entry
        declared.append(
            {"name": name, "id": var_id(name), "type": kind} if kind else {"name": name, "id": var_id(name)}
        )
    return {"blocks": {"languageVersion": 0, "blocks": blocks}, "variables": declared}


# --------------------------------------------------------------------------------------------- events


def on_ready(*steps: dict[str, Any], **kw: Any):
    return hat("sff_event_ready", *steps, **kw)


def on_message(*steps: dict[str, Any], **kw: Any):
    return hat("sff_event_message", *steps, **kw)


def command(name: str, *steps: dict[str, Any], **kw: Any):
    return hat("sff_event_command", *steps, fields={"NAME": name}, **kw)


def on_join(*steps: dict[str, Any], **kw: Any):
    return hat("sff_event_member_join", *steps, **kw)


def on_leave(*steps: dict[str, Any], **kw: Any):
    return hat("sff_event_member_leave", *steps, **kw)


# --------------------------------------------------------------------------------------------- values


def text(value: str) -> dict[str, Any]:
    return block("sff_text", fields={"TEXT": value})


def number(value: int | float) -> dict[str, Any]:
    return block("sff_number", fields={"NUM": value})


def boolean(value: bool) -> dict[str, Any]:
    return block("sff_boolean", fields={"BOOL": "true" if value else "false"})


def variable(name: str) -> dict[str, Any]:
    return block("sff_variable", fields={"VAR": {"id": var_id(name)}})


def list_variable(name: str) -> dict[str, Any]:
    return block("sff_list_variable", fields={"LIST": {"id": var_id(name)}})


def join(a: Any, b: Any) -> dict[str, Any]:
    return block("sff_join", inputs={"A": a, "B": b})


def math_op(a: Any, op: str, b: Any) -> dict[str, Any]:
    return block("sff_math", fields={"OP": op}, inputs={"A": a, "B": b})


def compare(a: Any, op: str, b: Any) -> dict[str, Any]:
    return block("sff_compare", fields={"OP": op}, inputs={"A": a, "B": b})


def logic(a: Any, op: str, b: Any) -> dict[str, Any]:
    return block("sff_logic", fields={"OP": op}, inputs={"A": a, "B": b})


def not_(a: Any) -> dict[str, Any]:
    return block("sff_not", inputs={"A": a})


def command_text() -> dict[str, Any]:
    return block("sff_command_text")


def message_text() -> dict[str, Any]:
    return block("sff_msg_text")


# --------------------------------------------------------------------------------------------- statements


def reply(value: Any = "Pong!"):
    return block("sff_reply", inputs={"TEXT": value})


def say(value: Any = "Hello!"):
    return block("sff_send_here", inputs={"TEXT": value})


def show(value: Any = "Hi"):
    """write ... to the console"""
    return block("sff_console_print", inputs={"TEXT": value})


def set_var(name: str, value: Any):
    return block("sff_set_variable", fields={"VAR": {"id": var_id(name)}}, inputs={"VALUE": value})


def change_var(name: str, by: Any = 1):
    return block("sff_change_variable", fields={"VAR": {"id": var_id(name)}}, inputs={"BY": by})


def wait(seconds: Any = 0.01):
    return block("sff_wait", inputs={"SECONDS": seconds})


def if_(condition: Any, *then: dict[str, Any]):
    return block("sff_if", inputs={"COND": condition, "DO": list(then) or None})


def if_else(condition: Any, then: list[dict[str, Any]], otherwise: list[dict[str, Any]]):
    return block("sff_if_else", inputs={"COND": condition, "DO": then or None, "ELSE": otherwise or None})


def repeat(times: Any, *body: dict[str, Any]):
    return block("sff_repeat", inputs={"TIMES": times, "DO": list(body) or None})


def forever(*body: dict[str, Any]):
    return block("sff_forever", inputs={"DO": list(body) or None})
