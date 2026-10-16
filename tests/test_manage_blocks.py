"""Blocks that make and delete channels and roles, start threads and publish forum posts."""

from __future__ import annotations

import asyncio
import json

import pytest
from blocks import block, command, if_, on_ready, reply, workspace

from scratch_fluxer import catalogue, codecheck, compiler
from scratch_fluxer import workspace as ws_lib
from scratch_fluxer.compiler import HELPERS, compile_workspace

NEW = [
    "sff_create_channel",
    "sff_delete_channel",
    "sff_create_role",
    "sff_delete_role",
    "sff_new_channel_id",
    "sff_new_role_id",
    "sff_thread_start",
    "sff_thread_from_message",
    "sff_thread_send",
    "sff_thread_change",
    "sff_new_thread_id",
    "sff_forum_post",
    "sff_forum_post_picture",
    "sff_forum_post_link",
    "sff_forum_tag_add",
    "sff_forum_tag_remove",
    "sff_forum_tags",
]


def build(*steps, assets=None):
    state = workspace(command("t", *steps))
    return compile_workspace(ws_lib.clean_workspace(state).workspace, assets=assets)


def test_every_new_block_is_in_the_catalogue_and_the_compiler():
    for type_ in NEW:
        assert type_ in catalogue.BLOCKS and (type_ in compiler.STATEMENTS or type_ in compiler.VALUES), type_
    assert [c for c in catalogue.CHANNEL_KIND_OPTIONS] == [
        ["text", "0"],
        ["voice", "2"],
        ["announcement", "5"],
        ["forum", "15"],
        ["media", "16"],
        ["link", "998"],
    ]


@pytest.mark.parametrize("kind", [value for _label, value in catalogue.CHANNEL_KIND_OPTIONS])
def test_each_channel_kind_writes_its_number(kind):
    r = build(block("sff_create_channel", fields={"KIND": kind}, inputs={"NAME": "news"}))
    assert f'make_channel(ctx.guild_id, {kind}, "news"' in r.code
    codecheck.check(r.code)


def test_the_blocks_compile_and_pass_the_safety_check():
    steps = [
        block("sff_create_channel", fields={"KIND": "998"}, inputs={"NAME": "site", "EXTRA": "https://fluxer.app"}),
        block("sff_delete_channel", inputs={"ID": block("sff_new_channel_id")}),
        block("sff_create_role", fields={"COLOR": "0", "HOIST": "True", "MENTION": "False"}, inputs={"NAME": "VIP"}),
        block("sff_delete_role", inputs={"ID": block("sff_new_role_id")}),
        block("sff_thread_start", fields={"KIND": "private"}, inputs={"NAME": "Chat"}),
        block("sff_thread_from_message", inputs={"NAME": "More"}),
        block("sff_thread_send", inputs={"TEXT": "Hi", "THREAD": block("sff_new_thread_id")}),
        block("sff_thread_change", fields={"ACTION": "lock"}, inputs={"THREAD": "5"}),
        block("sff_forum_post", inputs={"CHANNEL": "1", "TITLE": "T", "TEXT": "B", "TAGS": "Help, Bug"}),
        block(
            "sff_forum_post_link", inputs={"CHANNEL": "1", "TITLE": "T", "TEXT": "B", "PICTURE": "https://a.b/c.png"}
        ),
        block("sff_forum_post_picture", fields={"FILE": "p.png"}, inputs={"CHANNEL": "1", "TITLE": "T", "TEXT": "B"}),
        block("sff_forum_tag_add", inputs={"NAME": "Help", "CHANNEL": "1"}),
        block("sff_forum_tag_remove", inputs={"NAME": "Help", "CHANNEL": "1"}),
        reply(block("sff_forum_tags", inputs={"CHANNEL": "1"})),
    ]
    r = build(*steps, assets={"p.png": "image"})
    assert not [d for d in r.diagnostics if d.level == "error"], r.diagnostics
    codecheck.check(r.code)
    for name in ("make_channel", "remove_role", "make_thread", "forum_post", "forum_tag_remove", "forum_tags"):
        assert f"async def {name}(" in r.code


def test_a_command_that_manages_the_community_warns_unless_staff_are_checked():
    unsafe = build(block("sff_delete_channel", inputs={"ID": "5"}))
    assert any("channels, roles or tags" in d.message for d in unsafe.diagnostics)
    safe = build(
        if_(block("sff_author_has_role", inputs={"ROLE": "9"}), block("sff_delete_channel", inputs={"ID": "5"}))
    )
    assert not [d for d in safe.diagnostics if "channels, roles or tags" in d.message]


def test_they_need_an_event_that_has_a_community():
    state = workspace(on_ready(block("sff_create_role", inputs={"NAME": "x"})))
    r = compile_workspace(ws_lib.clean_workspace(state).workspace)
    assert not r.ok


def test_codenames_for_the_things_the_bot_made():
    r = build(reply("Made $newChannelID, $newRoleID and $newThreadID"))
    assert r.ok, r.diagnostics
    assert 'made["channel"]' in r.code and 'made["thread"]' in r.code


def test_user_text_cannot_leave_its_place():
    nasty = '"); import os; os.system("x") #'
    r = build(block("sff_create_channel", fields={"KIND": "0"}, inputs={"NAME": nasty}))
    codecheck.check(r.code)
    assert r.ok


# ---------------------------------------------------------------------------------------------- what the helpers send


class FakeHttp:
    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply or {"id": "777", "available_tags": [{"id": "1", "name": "Help"}, {"id": "2", "name": "Bug"}]}

    def _route(self, method, path, **params):
        return (method, path, params)

    async def request(self, route, json=None):
        self.calls.append(("request", route, json))
        return self.reply

    async def create_guild_channel(self, guild_id, **kw):
        self.calls.append(("create_guild_channel", guild_id, kw))
        return self.reply

    async def create_guild_role(self, guild_id, **kw):
        self.calls.append(("create_guild_role", guild_id, kw))
        return self.reply

    async def delete_channel(self, channel_id):
        self.calls.append(("delete_channel", channel_id))

    async def delete_guild_role(self, guild_id, role_id):
        self.calls.append(("delete_guild_role", guild_id, role_id))

    async def send_message(self, channel_id, **kw):
        self.calls.append(("send_message", channel_id, kw))


class FakeBot:
    def __init__(self, http):
        self._http = http


def world(*names, http=None):
    """The helpers `names` (and what they need), run against a pretend connection."""
    http = http or FakeHttp()
    space = {"bot": FakeBot(http)}
    wanted: list[str] = []

    def add(name):
        for need in HELPERS[name]["needs"]:
            add(need)
        if name not in wanted:
            wanted.append(name)

    for name in names:
        add(name)
    for name in compiler.HELPER_ORDER:
        if name in wanted:
            exec(HELPERS[name]["source"], space)  # noqa: S102 - our own helper source
    return space, http


def run(coro):
    return asyncio.run(coro)


def test_make_channel_sends_the_right_fields():
    space, http = world("make_channel")
    run(space["make_channel"]("42", 15, "news", "About news", "99"))
    assert http.calls == [
        ("create_guild_channel", "42", {"name": "news", "type": 15, "topic": "About news", "parent_id": "99"})
    ]
    assert space["made"]["channel"] == "777"


def test_a_link_channel_needs_a_web_address():
    space, http = world("make_channel")
    run(space["make_channel"]("42", 998, "site", "not a link"))
    assert http.calls == []
    run(space["make_channel"]("42", 998, "site", "https://fluxer.app"))
    assert http.calls[0][2]["url"] == "https://fluxer.app" and http.calls[0][2]["type"] == 998


def test_delete_uses_only_digits():
    space, http = world("remove_channel", "remove_role")
    run(space["remove_channel"]("../../guilds/5"))
    assert http.calls == [("delete_channel", "5")]
    run(space["remove_role"]("42", "abc"))
    assert len(http.calls) == 1


def test_make_role_sends_colour_and_flags():
    space, http = world("make_role")
    run(space["make_role"]("42", "VIP", 1535487, True, False))
    assert http.calls == [
        ("create_guild_role", "42", {"name": "VIP", "color": 1535487, "hoist": True, "mentionable": False})
    ]
    assert space["made"]["role"] == "777"


def test_threads_public_private_and_from_a_message():
    space, http = world("make_thread", "thread_from_message")
    run(space["make_thread"]("10", "Chat", "private"))
    run(space["make_thread"]("10", "Chat"))
    run(space["thread_from_message"]("10", "20", "More"))
    assert http.calls[0][1:] == (
        ("POST", "/channels/{channel_id}/threads", {"channel_id": "10"}),
        {"name": "Chat", "type": 12},
    )
    assert http.calls[1][2]["type"] == 11
    assert http.calls[2][1][1] == "/channels/{channel_id}/messages/{message_id}/threads"
    assert http.calls[2][1][2] == {"channel_id": "10", "message_id": "20"}


def test_send_and_change_a_thread():
    space, http = world("send_in_thread", "change_thread")
    run(space["send_in_thread"]("555", "hello"))
    run(space["change_thread"]("555", "lock"))
    run(space["change_thread"]("555", "nonsense"))
    assert http.calls[0] == ("send_message", "555", {"content": "hello"})
    assert http.calls[1][1][0] == "PATCH" and http.calls[1][2] == {"locked": True}
    assert len(http.calls) == 2


def test_a_forum_post_picks_tags_by_name_and_skips_wrong_ones(capsys):
    space, http = world("forum_post")
    run(space["forum_post"]("10", "Title", "Body", "help, Nope"))
    get, post = http.calls
    assert get[1][0] == "GET"
    assert post[2] == {"name": "Title", "message": {"content": "Body"}, "applied_tags": ["1"]}
    assert "tag nope does not exist" in capsys.readouterr().out
    assert space["made"]["thread"] == "777"


def test_a_forum_post_with_a_web_picture_embeds_it():
    space, http = world("forum_post")
    run(space["forum_post"]("10", "Title", "", "", None, "https://a.b/c.png"))
    body = http.calls[0][2]
    assert body["message"]["embeds"] == [{"image": {"url": "https://a.b/c.png"}}]
    assert body["message"]["content"] == "Title"
    run(space["forum_post"]("10", "Title", "x", "", None, "javascript:1"))
    assert "embeds" not in http.calls[1][2]["message"]


def test_a_forum_post_with_a_file_sends_it_after():
    class Pic:
        def to_dict(self):
            return {"filename": "p.png", "data": b""}

    space, http = world("forum_post")
    run(space["forum_post"]("10", "Title", "Body", "", Pic()))
    assert http.calls[-1] == ("send_message", "777", {"files": [{"filename": "p.png", "data": b""}]})


def test_tags_can_be_listed_added_and_removed():
    space, http = world("forum_tags", "forum_tag_add", "forum_tag_remove")
    assert run(space["forum_tags"]("10")) == ["Help", "Bug"]
    run(space["forum_tag_add"]("10", "New"))
    run(space["forum_tag_remove"]("10", "bug"))
    assert http.calls[1][2] == {"name": "New"}
    assert http.calls[-1][1] == ("DELETE", "/channels/{channel_id}/tags/{tag_id}", {"channel_id": "10", "tag_id": "2"})


def test_a_failure_is_printed_not_raised(capsys):
    class Broken(FakeHttp):
        async def create_guild_channel(self, guild_id, **kw):
            raise RuntimeError("403")

    space, _ = world("make_channel", http=Broken())
    run(space["make_channel"]("42", 0, "x"))
    assert "Manage Channels" in capsys.readouterr().out


def test_the_payload_of_a_request_is_json():
    space, http = world("make_thread")
    run(space["make_thread"]("10", "x"))
    json.dumps(http.calls[0][2])
