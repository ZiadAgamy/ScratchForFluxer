"""Moderation blocks and reaction roles, run through the real fluxer.py with a fake Fluxer API."""

from __future__ import annotations

import bot_harness
from blocks import block, command, if_, on_reaction_added, on_reaction_removed, reply, workspace

from scratch_fluxer.compiler import compile_workspace

STAFF = "800"
ROLE = "123456789012345678"


def build(tmp_path, *top):
    result = compile_workspace(workspace(*top))
    assert result.ok, result.diagnostics
    return bot_harness.start(result.code, tmp_path)


def staff_only(*steps):
    return if_(block("sff_author_has_role", inputs={"ROLE": STAFF}), *steps)


def kick_command():
    return command(
        "kick", staff_only(block("sff_kick_member", inputs={"USER": block("sff_first_mention_id"), "REASON": "rules"}))
    )


# ---- reaction roles


def reaction_bot(tmp_path):
    return build(
        tmp_path,
        on_reaction_added("✅", "555", block("sff_reaction_give_role", inputs={"ROLE": ROLE})),
        on_reaction_removed("✅", "555", block("sff_reaction_take_role", inputs={"ROLE": ROLE})),
    )


async def test_reacting_gives_the_role_and_taking_the_reaction_back_removes_it(tmp_path):
    running = await _ready(reaction_bot(tmp_path))
    await running.reaction("✅", user_id=321, message_id=555)
    assert running.http.named("add_guild_member_role") == [((700, 321, int(ROLE)), {"reason": None})]
    await running.reaction("✅", added=False, user_id=321, message_id=555)
    assert running.http.named("remove_guild_member_role") == [((700, 321, int(ROLE)), {"reason": None})]


async def test_other_messages_and_other_emoji_are_ignored(tmp_path):
    running = await _ready(reaction_bot(tmp_path))
    await running.reaction("✅", message_id=556)
    await running.reaction("❌", message_id=555)
    await running.reaction("✅", added=False, message_id=556)
    assert running.http.calls == []


async def test_the_bots_own_reaction_is_ignored(tmp_path):
    running = await _ready(reaction_bot(tmp_path))
    await running.reaction("✅", user_id=900, message_id=555)
    assert running.http.named("add_guild_member_role") == []


async def test_an_empty_filter_accepts_any_message_and_a_custom_emoji_matches_by_name(tmp_path):
    running = build(tmp_path, on_reaction_added("blobcat", "", block("sff_reaction_give_role", inputs={"ROLE": ROLE})))
    await _ready(running)
    await running.reaction("blobcat", custom_id=4242, message_id=1)
    await running.reaction("other", custom_id=1, message_id=1)
    assert len(running.http.named("add_guild_member_role")) == 1


def test_a_wrong_message_id_is_a_warning():
    result = compile_workspace(workspace(on_reaction_added("✅", "abc")))
    assert result.ok and any("only digits" in d.message for d in result.diagnostics)


# ---- moderation commands


async def _ready(running):
    await running.ready()
    await running.guild()  # the community's channels are cached, which is how a message knows its community
    return running


async def _chat(running, text, *, author_id=800):
    data = {
        "id": "1",
        "channel_id": "300",
        "guild_id": "700",
        "content": text,
        "author": bot_harness.user_data(author_id, "mod"),
        "timestamp": "2026-10-10T10:00:00+00:00",
        "embeds": [],
        "attachments": [],
        "mentions": [bot_harness.user_data(321, "troll")],
    }
    await running.bot._dispatch("MESSAGE_CREATE", data)


async def test_staff_can_kick_and_others_cannot(tmp_path):
    running = await _ready(build(tmp_path, kick_command()))
    running.http.member_roles[800] = [STAFF]
    await _chat(running, "!kick <@321>")
    assert running.http.named("kick_guild_member") == [((700, 321), {"reason": "rules"})]
    running.http.calls.clear()
    await _chat(running, "!kick <@321>", author_id=801)
    assert running.http.named("kick_guild_member") == []


async def test_each_moderation_block_calls_its_endpoint(tmp_path):
    steps = [
        block("sff_timeout_member", inputs={"USER": "321", "MINUTES": 10, "REASON": "calm down"}),
        block("sff_untimeout_member", inputs={"USER": "321", "REASON": ""}),
        block("sff_ban_member", inputs={"USER": "<@321>", "REASON": "spam"}),
        block("sff_unban_member", inputs={"USER": "321", "REASON": ""}),
        block("sff_give_role_to", inputs={"ROLE": ROLE, "USER": "321"}),
        block("sff_take_role_from", inputs={"ROLE": ROLE, "USER": "321"}),
    ]
    running = await _ready(build(tmp_path, command("all", staff_only(*steps))))
    running.http.member_roles[800] = [STAFF]
    await _chat(running, "!all")
    names = [name for name, _args, _kwargs in running.http.calls]
    for expected in (
        "timeout_guild_member",
        "ban_guild_member",
        "unban_guild_member",
        "add_guild_member_role",
        "remove_guild_member_role",
    ):
        assert expected in names
    timeouts = running.http.named("timeout_guild_member")
    assert timeouts[0][1]["until"] and timeouts[1][1]["until"] is None
    assert running.http.named("ban_guild_member")[0][0][:2] == (700, 321)
    assert running.http.named("unban_guild_member")[0][0][:2] == (700, 321)


async def test_the_bot_never_moderates_itself_and_bad_ids_do_not_crash(tmp_path, capsys):
    steps = [
        block("sff_ban_member", inputs={"USER": "900", "REASON": ""}),
        block("sff_kick_member", inputs={"USER": "nobody", "REASON": ""}),
        reply("done"),
    ]
    running = await _ready(build(tmp_path, command("x", staff_only(*steps))))
    running.http.member_roles[800] = [STAFF]
    await _chat(running, "!x")
    assert running.http.named("ban_guild_member") == [] and running.http.named("kick_guild_member") == []
    assert running.texts == ["done"]
    assert "skipped" in capsys.readouterr().out


def test_moderation_without_a_staff_check_gets_a_warning():
    result = compile_workspace(workspace(command("ban", block("sff_ban_member", inputs={"USER": "1", "REASON": ""}))))
    assert result.ok
    assert any("Anyone who can type this" in d.message for d in result.diagnostics)
    guarded = compile_workspace(workspace(kick_command()))
    assert not any("Anyone who can type this" in d.message for d in guarded.diagnostics)


def test_blocks_that_need_a_message_or_reaction_are_refused_elsewhere():
    from blocks import on_ready

    result = compile_workspace(workspace(on_ready(block("sff_kick_member", inputs={"USER": "1", "REASON": ""}))))
    assert not result.ok
    result = compile_workspace(workspace(command("x", block("sff_reaction_give_role", inputs={"ROLE": ROLE}))))
    assert not result.ok
