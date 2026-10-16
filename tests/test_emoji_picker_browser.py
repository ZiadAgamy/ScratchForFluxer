"""The emoji picker in a card's text, a bot's community emoji, and what the server sends for them."""

from __future__ import annotations

import json

import httpx
import pytest
from blocks import block, command, workspace
from fluxer_mock import TOKEN
from playwright.sync_api import expect
from test_editor_browser import (  # noqa: F401 - open_editor is a fixture
    assert_quiet,
    field_centre,
    open_editor,
)

pytestmark = pytest.mark.browser

API = "https://api.fluxer.app/v1"


def with_embed():
    return workspace(
        command("card", block("sff_reply_embed", id="emb1", inputs={"TITLE": "Rules", "TEXT": "Be kind."}))
    )


def community_mock(fluxer, *, guilds=None, emojis=None, guild_status=200):
    guilds = guilds if guilds is not None else [{"id": "700", "name": "Cool Place"}, {"id": "701", "name": "Empty"}]
    emojis = (
        emojis
        if emojis is not None
        else {
            "700": [
                {"id": "9001", "name": "party", "animated": False},
                {"id": "9002", "name": "dance", "animated": True},
            ],
            "701": [],
        }
    )

    def auth_ok(request):
        return request.headers.get("authorization", "").removeprefix("Bot ") == TOKEN

    def guild_list(request):
        if not auth_ok(request):
            return httpx.Response(401, json={})
        return httpx.Response(guild_status, json=guilds)

    fluxer.get(f"{API}/users/@me/guilds").mock(side_effect=guild_list)
    for guild_id, rows in emojis.items():
        fluxer.get(f"{API}/guilds/{guild_id}/emojis").mock(return_value=httpx.Response(200, json=rows))


def open_picker(page):
    x, y = field_centre(page, "emb1", "OPTIONS")
    page.mouse.click(x, y)
    expect(page.locator("dialog.dialog-embed")).to_be_visible()
    page.click("dialog.dialog-embed .emoji-toggle")
    expect(page.locator("dialog.dialog-embed .emoji-panel")).to_be_visible()


def test_the_picker_is_only_on_the_description(open_editor, fluxer):
    community_mock(fluxer)
    page = open_editor(state=with_embed())
    x, y = field_centre(page, "emb1", "OPTIONS")
    page.mouse.click(x, y)
    expect(page.locator("dialog.dialog-embed")).to_be_visible()
    assert page.locator("dialog.dialog-embed .emoji-toggle").count() == 1
    assert page.locator("dialog.dialog-embed textarea ~ .embed-text-tools .emoji-toggle").count() == 1
    assert_quiet(page)


def test_a_normal_emoji_goes_into_the_text_at_the_cursor(open_editor, fluxer):
    community_mock(fluxer)
    page = open_editor(state=with_embed())
    open_picker(page)
    area = page.locator("dialog.dialog-embed textarea")
    area.focus()
    area.evaluate("el => el.setSelectionRange(2, 2)")
    page.fill(".emoji-search", "fire")
    page.locator(".emoji-pick").first.click()
    assert area.input_value() == "Be🔥 kind."
    expect(page.locator(".embed-preview-card .embed-text")).to_contain_text("Be🔥 kind.")
    assert_quiet(page)


def test_community_emoji_are_listed_and_written_the_way_fluxer_reads_them(open_editor, fluxer):
    community_mock(fluxer)
    page = open_editor(state=with_embed())
    open_picker(page)
    expect(page.locator(".emoji-heading", has_text="Cool Place")).to_be_visible()
    assert page.locator(".emoji-heading", has_text="Empty").count() == 0  # a community with no emoji is left out
    page.locator(".emoji-pick[title=':party:']").click()
    page.locator(".emoji-pick[title=':dance:']").click()
    area = page.locator("dialog.dialog-embed textarea")
    assert area.input_value().endswith("<:party:9001><a:dance:9002>")
    page.click("dialog.dialog-embed button[type=submit]")
    code = json.dumps(
        page.evaluate(
            "() => window.sffEditor.blocks.workspace().getBlockById('emb1').getInput('TEXT').connection.targetBlock().getFieldValue('TEXT')"
        )
    )
    assert "<:party:9001>" in code
    assert_quiet(page)


def test_a_failing_lookup_still_leaves_the_ordinary_emoji(open_editor, fluxer):
    community_mock(fluxer, guild_status=500)
    page = open_editor(state=with_embed())
    open_picker(page)
    expect(page.locator(".emoji-status")).to_contain_text("ordinary emoji still work")
    assert page.locator(".emoji-pick").count() > 50


def test_escape_closes_only_the_picker(open_editor, fluxer):
    community_mock(fluxer)
    page = open_editor(state=with_embed())
    open_picker(page)
    page.keyboard.press("Escape")
    expect(page.locator("dialog.dialog-embed .emoji-panel")).to_be_hidden()
    expect(page.locator("dialog.dialog-embed")).to_be_visible()
    assert_quiet(page)


def test_the_server_gives_the_owner_the_emojis_and_nobody_else(fluxer, open_editor):
    community_mock(fluxer)
    page = open_editor(state=with_embed())
    reply = page.request.get(f"{page.base}/api/bots/{page.bot_id}/emojis")
    assert reply.status == 200
    body = reply.json()
    assert [c["name"] for c in body["communities"]] == ["Cool Place"]
    assert body["communities"][0]["emojis"][1] == {"id": "9002", "name": "dance", "animated": True}
    assert TOKEN not in reply.text()
    other = page.request.get(f"{page.base}/api/bots/{'0' * 32}/emojis")
    assert other.status == 404
