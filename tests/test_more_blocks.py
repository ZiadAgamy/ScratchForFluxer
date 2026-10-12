"""Edit and delete a message by ID, ping and uptime, webhooks and the typing indicator."""

from __future__ import annotations

import bot_harness
from blocks import block, command, on_ready, reply, show, variable, workspace

from scratch_fluxer.compiler import compile_workspace

WEBHOOK = "https://fluxer.example/api/webhooks/4567/abc_DEF-123"


def build(tmp_path, *top, variables=()):
    result = compile_workspace(workspace(*top, variables=variables))
    assert result.ok, result.diagnostics
    return bot_harness.start(result.code, tmp_path)


async def start_bot(running):
    await running.ready()
    await running.guild()
    return running


async def test_a_sent_message_can_be_edited_and_deleted_through_its_saved_id(tmp_path):
    steps = [
        block("sff_send_here_save_id", fields={"VAR": {"id": "var-sent"}}, inputs={"TEXT": "Loading"}),
        block("sff_edit_message", inputs={"ID": variable("sent"), "CHANNEL": "300", "TEXT": "Done!"}),
        block("sff_delete_message_by_id", inputs={"ID": variable("sent"), "CHANNEL": "300"}),
    ]
    running = await start_bot(build(tmp_path, command("go", *steps), variables=[("sent", "")]))
    await running.message("!go")
    assert running.texts == ["Loading"]
    sent_id = running.http.calls[[n for n, _a, _k in running.http.calls].index("send_message")]
    edits = running.http.named("edit_message")
    assert len(edits) == 1 and edits[0][1]["content"] == "Done!"
    deletes = running.http.named("delete_message")
    assert len(deletes) == 1 and deletes[0][0][0] == "300"
    assert sent_id


async def test_sending_to_a_channel_saves_the_id_too(tmp_path):
    steps = [
        block(
            "sff_send_channel_save_id",
            fields={"VAR": {"id": "var-sent"}},
            inputs={"TEXT": "Hi", "CHANNEL": "300"},
        ),
        block("sff_edit_message", inputs={"ID": variable("sent"), "CHANNEL": "300", "TEXT": "Edited"}),
    ]
    running = await start_bot(build(tmp_path, command("go", *steps), variables=[("sent", "")]))
    await running.message("!go")
    assert running.texts == ["Hi"]
    assert running.http.named("edit_message")[0][1]["content"] == "Edited"


async def test_a_bad_message_id_is_reported_and_the_bot_carries_on(tmp_path, capsys):
    steps = [block("sff_edit_message", inputs={"ID": "nope", "CHANNEL": "300", "TEXT": "x"}), reply("after")]
    running = await start_bot(build(tmp_path, command("go", *steps)))
    await running.message("!go")
    assert running.texts == ["after"]
    assert "Edit failed" in capsys.readouterr().out


async def test_ping_and_uptime(tmp_path):
    steps = [
        reply(block("sff_bot_uptime_text")),
        reply(block("sff_bot_uptime_seconds")),
        reply(block("sff_bot_ping")),
    ]
    running = await start_bot(build(tmp_path, command("status", *steps)))
    await running.message("!status")
    assert running.texts[0].endswith("s") and running.texts[0][0].isdigit()
    assert running.texts[1].isdigit()
    assert running.texts[2].isdigit()
    assert running.http.named("get_user")  # the ping asks Fluxer for something small


async def test_a_webhook_message_and_embed(tmp_path):
    steps = [
        block("sff_webhook_send", inputs={"TEXT": "Hello hook", "URL": WEBHOOK, "NAME": "News bot"}),
        block(
            "sff_webhook_embed",
            fields={"COLOR": "green"},
            inputs={"TITLE": "Update", "TEXT": "Body", "URL": WEBHOOK, "NAME": ""},
        ),
    ]
    running = await start_bot(build(tmp_path, command("hook", *steps)))
    await running.message("!hook")
    calls = running.http.named("execute_webhook")
    assert len(calls) == 2
    assert calls[0][0] == ("4567", "abc_DEF-123")
    assert calls[0][1]["content"] == "Hello hook" and calls[0][1]["username"] == "News bot"
    embed = calls[1][1]["embeds"][0]
    assert embed["title"] == "Update" and embed["description"] == "Body"
    assert calls[1][1]["username"] is None


async def test_a_wrong_webhook_url_is_skipped(tmp_path, capsys):
    steps = [block("sff_webhook_send", inputs={"TEXT": "x", "URL": "not a url", "NAME": ""}), reply("after")]
    running = await start_bot(build(tmp_path, command("hook", *steps)))
    await running.message("!hook")
    assert running.http.named("execute_webhook") == []
    assert running.texts == ["after"]
    assert "Webhook skipped" in capsys.readouterr().out


async def test_typing_in_this_channel_and_in_another(tmp_path):
    steps = [block("sff_typing_here"), block("sff_typing_channel", inputs={"CHANNEL": "301"}), reply("hi")]
    running = await start_bot(build(tmp_path, command("t", *steps)))
    await running.message("!t")
    assert [args for args, _kw in running.http.named("trigger_typing")] == [(300,), (301,)]
    assert running.texts == ["hi"]


def test_blocks_that_need_a_message_are_refused_on_ready():
    for typ in ("sff_typing_here", "sff_send_here_save_id"):
        result = compile_workspace(workspace(on_ready(block(typ, inputs={"TEXT": "x"}))))
        assert not result.ok, typ
    assert compile_workspace(workspace(on_ready(show("ok")))).ok
