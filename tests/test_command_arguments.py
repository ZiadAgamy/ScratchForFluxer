"""Commands with arguments, and commands locked to a permission, run through the real fluxer.py."""

from __future__ import annotations

import bot_harness
import fluxer
from blocks import block, command, hat, reply, workspace

from scratch_fluxer.compiler import compile_workspace

KICK = int(fluxer.Permissions.KICK_MEMBERS)
STAFF_ROLE = "810"


def build(tmp_path, *top):
    result = compile_workspace(workspace(*top))
    assert result.ok, result.diagnostics
    return bot_harness.start(result.code, tmp_path), result


async def ready(running):
    await running.ready()
    await running.guild()
    return running


async def chat(running, text, *, author_id=800, mentions=()):
    data = {
        "id": "1",
        "channel_id": "300",
        "guild_id": "700",
        "content": text,
        "author": bot_harness.user_data(author_id, "mod"),
        "timestamp": "2026-10-10T10:00:00+00:00",
        "embeds": [],
        "attachments": [],
        "mentions": [bot_harness.user_data(user, "someone") for user in mentions],
    }
    await running.bot._dispatch("MESSAGE_CREATE", data)


def say_command(**kw):
    return command("say", reply("$argMessage"), args="message", **kw)


async def test_the_last_argument_takes_the_rest_of_the_message(tmp_path):
    running, _ = build(tmp_path, say_command())
    await ready(running)
    await chat(running, "!say hello there  everyone")
    assert running.texts == ["hello there  everyone"]


async def test_arguments_are_split_by_words_and_the_last_keeps_the_rest(tmp_path):
    steps = block("sff_reply", inputs={"TEXT": "[$argFirst] [$argSecond] [$argRest]"})
    running, _ = build(tmp_path, command("pair", steps, args="first second rest"))
    await ready(running)
    await chat(running, "!pair a b c d e")
    assert running.texts == ["[a] [b] [c d e]"]


async def test_a_missing_required_argument_shows_how_to_use_the_command(tmp_path):
    running, _ = build(tmp_path, command("kick", reply("done"), args="user reason?"))
    await ready(running)
    await chat(running, "!kick")
    assert running.texts == ["Usage: !kick <user> [reason]"]
    await chat(running, "!kick 321")
    assert running.texts[-1] == "done"


async def test_the_argument_block_is_the_same_as_the_codename(tmp_path):
    step = block("sff_reply", inputs={"TEXT": block("sff_command_arg", fields={"NAME": "Name"})})
    running, _ = build(tmp_path, command("hi", step, args="name"))
    await ready(running)
    await chat(running, "!hi Moka")
    assert running.texts == ["Moka"]


async def test_an_argument_can_hold_a_mention_for_a_moderation_block(tmp_path):
    kick = block("sff_kick_member", inputs={"USER": "$argUser", "REASON": "$argReason"})
    running, _ = build(tmp_path, command("kick", kick, who="KICK_MEMBERS", args="user reason?"))
    running.http.role_permissions = {"700": 0, STAFF_ROLE: KICK}
    running.http.member_roles[800] = [STAFF_ROLE]
    await ready(running)
    await chat(running, "!kick <@321> breaking the rules")
    assert running.http.named("kick_guild_member") == [((700, 321), {"reason": "breaking the rules"})]


async def test_a_locked_command_works_for_people_with_the_permission(tmp_path):
    running, _ = build(tmp_path, say_command(who="KICK_MEMBERS"))
    running.http.role_permissions = {"700": 0, STAFF_ROLE: KICK}
    running.http.member_roles[800] = [STAFF_ROLE]
    await ready(running)
    await chat(running, "!say hi")
    assert running.texts == ["hi"]


async def test_a_locked_command_refuses_everyone_else(tmp_path):
    running, _ = build(tmp_path, say_command(who="KICK_MEMBERS"))
    running.http.role_permissions = {"700": 0, STAFF_ROLE: KICK}
    await ready(running)
    await chat(running, "!say hi", author_id=801)
    assert running.texts == ["You don't have permission to use this command."]


async def test_administrators_and_the_owner_pass_a_lock(tmp_path):
    running, _ = build(tmp_path, say_command(who="BAN_MEMBERS"))
    running.http.role_permissions = {"700": 0, "811": int(fluxer.Permissions.ADMINISTRATOR)}
    running.http.member_roles[802] = ["811"]
    await ready(running)
    await chat(running, "!say admin", author_id=802)
    await chat(running, "!say owner", author_id=999)
    assert running.texts == ["admin", "owner"]


def test_a_lock_means_no_permission_warning_for_moderation_blocks():
    kick = block("sff_kick_member", inputs={"USER": "$argUser", "REASON": ""})
    open_ = compile_workspace(workspace(command("kick", kick, args="user")))
    locked = compile_workspace(workspace(command("kick", kick, who="KICK_MEMBERS", args="user")))
    assert any("Anyone who can type this" in d.message for d in open_.diagnostics)
    assert not any("Anyone who can type this" in d.message for d in locked.diagnostics)


def test_bad_argument_names_and_unknown_arguments_are_warnings_not_crashes():
    step = block("sff_reply", inputs={"TEXT": block("sff_command_arg", fields={"NAME": "nope"})})
    result = compile_workspace(workspace(command("x", step, args="ok 9bad ok")))
    assert result.ok
    messages = " ".join(d.message for d in result.diagnostics)
    assert "can't be an argument name" in messages and "written twice" in messages and "no argument called" in messages


def test_an_unknown_lock_is_ignored_with_a_warning():
    result = compile_workspace(workspace(command("x", reply("hi"), who="DESTROY_EVERYTHING")))
    assert result.ok and "@fluxer.checks" not in result.code
    assert any("isn't one of the choices" in d.message for d in result.diagnostics)


def test_an_old_command_block_without_the_new_fields_still_compiles_clean():
    old = hat("sff_event_command", reply("Pong!"), fields={"NAME": "ping"})
    result = compile_workspace(workspace(old))
    assert result.ok and not result.diagnostics and "split_args" not in result.code
