"""“forever” in a place where it keeps a script from ever finishing gets a warning (the bot still runs)."""

from __future__ import annotations

from blocks import command, forever, on_ready, reply, show, wait, workspace

from scratch_fluxer.compiler import compile_workspace


def warnings(*top):
    result = compile_workspace(workspace(*top))
    assert result.ok, result.diagnostics
    return [d["message"] if isinstance(d, dict) else d.message for d in result.diagnostics if (d["level"] if isinstance(d, dict) else d.level) == "warning"]


def test_forever_in_ready_warns():
    found = warnings(on_ready(forever(show("x"), wait(3))))
    assert any("when the bot is ready" in text for text in found)


def test_forever_in_a_command_warns():
    found = warnings(command("ls", forever(reply("x"), wait(3))))
    assert any("never ends" in text for text in found)


def test_a_normal_ready_script_is_quiet():
    assert not any("forever" in text for text in warnings(on_ready(show("hi"))))
