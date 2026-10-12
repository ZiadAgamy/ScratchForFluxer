"""The block editor in a real browser: the JavaScript the API tests can't see.

Fluxer is the shared mock from conftest.py. The server and Blockly both run for real, so these check what a person
actually gets: blocks that snap together, saves that don't reload the page under their hands, files that show up in
the block dropdowns, and so on. Skipped automatically when Playwright/Chromium isn't installed.

Set ``FUZZ_BROWSER_SEEDS`` (and ``FUZZ_FIRST``) to load more random workspaces into Blockly.
"""

from __future__ import annotations

import json
import os
import random
import struct
import zlib
from types import SimpleNamespace

import pytest
from blocks import command, join, reply, text
from blocks import workspace as workspace_of
from fluxer_mock import OWNER_FLUXER_ID, TOKEN
from fuzz_blocks import Randomizer, mutate
from playwright.sync_api import expect
from sqlalchemy import select, update
from workspace_check import every_block

from scratch_fluxer import catalogue, workspace
from scratch_fluxer.compiler import compile_workspace, default_workspace
from scratch_fluxer.db import _call_with_session
from scratch_fluxer.models import Bot

pytestmark = pytest.mark.browser

FIRST = int(os.environ.get("FUZZ_FIRST", "0"))
COUNT = int(os.environ.get("FUZZ_BROWSER_SEEDS", "100"))

#: Written into every page before its own scripts run: anything the page's security policy refuses is recorded.
WATCH_THE_POLICY = """
window.__violations = [];
document.addEventListener('securitypolicyviolation', (event) => {
  window.__violations.push(event.violatedDirective + ' ' + event.blockedURI);
});
"""


#: Puts a block on the page the way a drop would, without the mouse: the save timing tests need the change to happen
#: at once, not "about 200 ms after the drop".
ADD_A_BLOCK = """async () => {
    const workspace = window.sffEditor.blocks.workspace();
    const block = workspace.newBlock('sff_console_print');
    block.initSvg();
    block.render();
    block.moveBy(420, 320);
    await new Promise((resolve) => setTimeout(resolve, 60)); /* Blockly tells the editor about it a moment later */
    return block.id;
}"""


# --------------------------------------------------------------------------------------------- what the server holds
def stored(bot_id: str) -> SimpleNamespace:
    """The bot's saved blocks (``None`` if nothing is saved yet) and their version."""

    def _read(db):
        row = db.execute(select(Bot).where(Bot.id == bot_id)).scalar_one()
        text = row.workspace_json
        return SimpleNamespace(version=row.workspace_version, workspace=json.loads(text) if text else None)

    return _call_with_session(_read, (), {})


def types_in(state: dict) -> list[str]:
    """The type of every block in a saved workspace, however deeply it is nested."""
    return [node["type"] for node in every_block(state)]


def first_of(state: dict, block_type: str) -> dict:
    return next(node for node in every_block(state) if node["type"] == block_type)


def put_in_database(bot_id: str, text: str, *, moves_version_on: bool = False) -> None:
    """Store exactly ``text`` as the bot's blocks, as if some other tab (or something broken) had saved it."""

    def _write(db):
        values = {"workspace_json": text}
        if moves_version_on:
            values["workspace_version"] = Bot.workspace_version + 1
        db.execute(update(Bot).where(Bot.id == bot_id).values(**values))

    _call_with_session(_write, (), {})


def store_workspace(bot_id: str, state: dict) -> None:
    put_in_database(bot_id, json.dumps(workspace.clean_workspace(state).workspace))


def tiny_png(width: int = 8, height: int = 8) -> bytes:
    """A real picture (in the accent colour) that a browser can draw."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + b"\x17\x6d\xff" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def upload_file(name: str, data: bytes, mime: str) -> dict:
    return {"name": name, "mimeType": mime, "buffer": data}


CAT = upload_file("cat.png", tiny_png(), "image/png")
DOG = upload_file("dog.png", tiny_png(12, 12), "image/png")
JOKES = upload_file("jokes.json", json.dumps({"jokes": ["one", "two"]}).encode(), "application/json")
NOTES = upload_file("notes.txt", b"Remember the milk.\n", "text/plain")


# --------------------------------------------------------------------------------------------- the page
@pytest.fixture
def open_editor(fluxer, new_page, live_server, make_user):
    """``open_editor(viewport=None, state=None, bot_text=None)``: a page showing the editor for a brand new bot.

    ``state`` is a workspace to start from; ``bot_text`` is stored as-is (to start from something broken).
    The page also gets ``.base``, ``.bot_id`` and ``.csrf``. Listing ``fluxer`` makes sure the mock Fluxer is in place
    before the server starts asking it about the bot's token.
    """

    def _open(viewport=None, state=None, bot_text=None):
        _, csrf, cookies = make_user(fluxer_id=OWNER_FLUXER_ID, username="agamy", global_name="AGAMY")
        page = new_page(cookies=cookies, viewport=viewport)
        page.add_init_script(WATCH_THE_POLICY)
        page.base = live_server
        page.csrf = csrf
        added = page.request.post(
            f"{live_server}/api/bots",
            data={"token": TOKEN},
            headers={"X-CSRF-Token": csrf, "Origin": live_server},
        )
        assert added.status == 201, added.text()
        page.bot_id = added.json()["bot"]["id"]
        if state is not None:
            store_workspace(page.bot_id, state)
        if bot_text is not None:
            put_in_database(page.bot_id, bot_text)
        page.goto(f"{live_server}/bots/{page.bot_id}/edit")
        wait_until_ready(page)
        return page

    return _open


def wait_until_ready(page) -> None:
    page.wait_for_selector("#ed-loading", state="hidden", timeout=20000)
    page.wait_for_function("() => window.sffEditor.blocks.workspace().getAllBlocks(false).length > 0")


def assert_quiet(page) -> None:
    """No JavaScript errors, no console errors, and nothing the page's security policy had to refuse."""
    assert page.evaluate("() => window.__violations") == []
    assert page.problems == []


def wait_for_version(page, version: int, seconds: int = 12) -> None:
    """Wait until the server holds this version of the blocks and the editor says they are saved."""
    for _ in range(seconds * 10):
        if stored(page.bot_id).version >= version:
            break
        page.wait_for_timeout(100)
    else:
        raise AssertionError(f"the server is still at version {stored(page.bot_id).version}, not {version}")
    page.wait_for_function("() => document.getElementById('ed-status').dataset.state === 'saved'", timeout=10000)


def status(page) -> str:
    return page.inner_text("#ed-status-text")


def python_shown(page) -> str:
    """The code in the Python tab, one line per line (empty lines hold an invisible space)."""
    return "\n".join(page.locator("#code-view .ct").all_inner_texts()).replace("\u200b", "")


def block_types(page) -> list[str]:
    return page.evaluate("() => window.sffEditor.blocks.workspace().getAllBlocks(true).map((b) => b.type)")


def top_types(page) -> list[str]:
    return page.evaluate("() => window.sffEditor.blocks.workspace().getTopBlocks(true).map((b) => b.type)")


def block_box(page, block_id: str) -> dict:
    return page.evaluate(
        """(id) => {
            const block = window.sffEditor.blocks.workspace().getBlockById(id);
            const r = block.getSvgRoot().getBoundingClientRect();
            return {left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height};
        }""",
        block_id,
    )


def field_centre(page, block_id: str, field_name: str) -> tuple[float, float]:
    x, y = page.evaluate(
        """([id, name]) => {
            const block = window.sffEditor.blocks.workspace().getBlockById(id);
            const r = block.getField(name).getSvgRoot().getBoundingClientRect();
            return [r.left + r.width / 2, r.top + r.height / 2];
        }""",
        [block_id, field_name],
    )
    return x, y


def click_block(page, block_id: str, button: str = "left") -> None:
    """Click the left edge of a block, which is the block itself and never one of its fields."""
    box = block_box(page, block_id)
    page.mouse.click(box["left"] + 5, box["top"] + box["height"] / 2, button=button)


def open_list(page, category: str) -> None:
    """Open one of the block lists in the rail on the left (events, bot, messages, ...)."""
    page.click(f"#cat-{category}")
    page.wait_for_function("() => window.sffEditor.blocks.workspace().getToolbox().getFlyout().isVisible()")
    page.wait_for_timeout(200)


def list_types(page) -> list[str]:
    return page.evaluate(
        """() => window.sffEditor.blocks.workspace().getToolbox().getFlyout().getWorkspace()
            .getTopBlocks(true).map((b) => b.type)"""
    )


def list_box(page, block_type: str) -> dict:
    box = page.evaluate(
        """(type) => {
            const flyout = window.sffEditor.blocks.workspace().getToolbox().getFlyout();
            const block = flyout.getWorkspace().getTopBlocks(false).find((b) => b.type === type);
            if (!block) return null;
            const r = block.getSvgRoot().getBoundingClientRect();
            return {left: r.left, top: r.top, width: r.width, height: r.height};
        }""",
        block_type,
    )
    assert box, f"{block_type} is not in the open block list"
    return box


def drag_from_list(page, block_type: str, left: float, top: float) -> None:
    """Pick the block up from the open list and let go with its top left corner at (left, top)."""
    box = list_box(page, block_type)
    grab_x, grab_y = box["left"] + 14, box["top"] + box["height"] / 2
    page.mouse.move(grab_x, grab_y)
    page.mouse.down()
    page.mouse.move(grab_x + 40, grab_y + 40, steps=4)  # far enough that it is a drag and not a click
    page.mouse.move(left + 14, top + box["height"] / 2, steps=12)
    page.mouse.up()
    page.wait_for_timeout(150)


def drop_under(page, block_type: str, block_id: str) -> None:
    """Drag a block from the open list so it snaps on below the block called ``block_id``."""
    below = block_box(page, block_id)
    drag_from_list(page, block_type, below["left"], below["bottom"])


def selected_id(page):
    return page.evaluate("() => { const s = window.Blockly.common.getSelected(); return s ? s.id : null; }")


def menu_items(page) -> list[str]:
    """The entries of whatever menu is open (a block's dropdown or the right-click menu), without shortcut keys."""
    return [text.strip().split("\n")[0] for text in page.locator(".blocklyMenuItem").all_inner_texts()]


# =============================================================================================== opening the editor
class TestOpening:
    def test_a_new_bot_opens_with_the_starter_blocks_and_the_python_they_write(self, open_editor):
        page = open_editor()
        assert top_types(page) == ["sff_bot_setup", "sff_event_ready", "sff_event_command", "sff_bot_login"]
        expected = compile_workspace(default_workspace(), assets={}).code
        assert python_shown(page) == expected.rstrip("\n")
        assert status(page) == "Saved"
        assert page.inner_text("#code-meta") == f"· {len(expected.splitlines())} lines"
        assert page.is_hidden("#ed-problems-pill")
        assert page.inner_text(".ed-bot-name") == "Ping Bot"
        assert_quiet(page)

    def test_nothing_is_saved_just_for_looking(self, open_editor):
        page = open_editor()
        page.wait_for_timeout(1800)  # longer than the delay before an edit is saved
        assert stored(page.bot_id).version == 0
        assert status(page) == "Saved"

    def test_the_rail_lists_the_categories_the_server_describes(self, open_editor):
        page = open_editor()
        wanted = [category["name"] for category in catalogue.browser_payload()["categories"]]
        assert page.locator(".blocklyToolboxCategoryLabel").all_inner_texts() == wanted

    def test_every_block_list_shows_what_the_server_says_it_should(self, open_editor):
        page = open_editor()
        payload = catalogue.browser_payload()
        for category in payload["categories"]:
            open_list(page, category["id"])
            # Blocks that need a variable wait until there is one; everything else is there, in order.
            wanted = [entry["type"] for entry in payload["toolbox"][category["id"]] if not entry.get("variable")]
            assert list_types(page) == wanted, category["id"]
        assert_quiet(page)

    def test_the_lists_that_need_a_variable_say_so_and_offer_to_make_one(self, open_editor):
        page = open_editor()
        for category, button in (
            ("variables", "Make a variable"),
            ("lists", "Make a list"),
            ("data", "Make a variable"),
        ):
            open_list(page, category)
            buttons = page.locator(".blocklyFlyoutButton").all_text_contents()  # SVG text has no innerText
            assert button in [text.strip() for text in buttons], category
            assert page.locator(".blocklyFlyoutLabel").count() == 1, category

    def test_the_page_keeps_to_its_security_policy(self, open_editor):
        """Inline scripts are refused, and the editor never needs one: the whole load above ran without a refusal."""
        page = open_editor()
        assert page.evaluate("() => window.__violations") == []
        refused = page.evaluate(
            """() => new Promise((resolve) => {
                document.addEventListener('securitypolicyviolation', (event) => resolve(event.violatedDirective));
                const script = document.createElement('script');
                script.textContent = 'window.__ran = true';
                document.head.appendChild(script);
                setTimeout(() => resolve('not refused'), 1500);
            })"""
        )
        assert refused.startswith("script-src")
        assert page.evaluate("() => window.__ran") is None

    def test_a_bots_name_is_only_ever_shown_as_text(self, open_editor):
        page = open_editor()
        name = "<img src=x onerror=alert(1)> & <b>bold</b>"
        renamed = page.request.patch(
            f"{page.base}/api/bots/{page.bot_id}",
            data={"name": name},
            headers={"X-CSRF-Token": page.csrf, "Origin": page.base},
        )
        assert renamed.status == 200, renamed.text()
        page.reload()
        wait_until_ready(page)
        assert page.locator(".ed-bot-name img, .ed-bot-name b").count() == 0
        assert page.inner_text(".ed-bot-name") == name
        assert name in page.title()
        assert_quiet(page)

    def test_a_bot_that_is_not_there_is_not_found(self, open_editor):
        page = open_editor()
        response = page.goto(f"{page.base}/bots/{'0' * 32}/edit")
        assert response.status == 404


# =============================================================================================== blocks
class TestBlocks:
    def test_a_block_dragged_from_the_list_snaps_on_and_is_saved(self, open_editor):
        page = open_editor()
        open_list(page, "messages")
        with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/workspace")) as saving:
            drop_under(page, "sff_send_here", "reply")
        added = page.evaluate(
            """() => {
                const reply = window.sffEditor.blocks.workspace().getBlockById('reply');
                const next = reply.getNextBlock();
                return next ? next.type : null;
            }"""
        )
        assert added == "sff_send_here"
        wait_for_version(page, 1)
        body = saving.value.json()
        assert body["version"] == 1 and body["compile"]["ok"] is True
        assert "workspace" not in body  # the server kept exactly what was sent
        command_block = first_of(stored(page.bot_id).workspace, "sff_event_command")
        assert command_block["next"]["block"]["next"]["block"]["type"] == "sff_send_here"  # reply, then the new block
        assert "send(" in python_shown(page)
        assert_quiet(page)

    def test_a_block_dropped_in_the_open_stays_where_it_was_put(self, open_editor):
        page = open_editor()
        open_list(page, "bot")
        drag_from_list(page, "sff_console_print", 620, 520)
        wait_for_version(page, 1)
        loose = [b for b in stored(page.bot_id).workspace["blocks"]["blocks"] if b["type"] == "sff_console_print"]
        assert len(loose) == 1 and "next" not in loose[0]
        # a block that is not under an event writes no Python, and the editor says nothing about it
        assert python_shown(page).count("print(") == 1
        assert_quiet(page)

    def test_undo_and_redo_follow_what_was_done(self, open_editor):
        page = open_editor()
        assert page.is_disabled("#tool-undo") and page.is_disabled("#tool-redo")
        open_list(page, "bot")
        drag_from_list(page, "sff_console_print", 620, 520)
        expect(page.locator("#tool-undo")).to_be_enabled()
        count = len(block_types(page))
        page.click("#tool-undo")
        assert len(block_types(page)) < count and page.is_enabled("#tool-redo")
        page.click("#tool-redo")
        assert len(block_types(page)) == count
        wait_for_version(page, 1)

    def test_tidy_up_lines_the_blocks_up_and_saves_the_result(self, open_editor):
        page = open_editor()
        open_list(page, "bot")
        drag_from_list(page, "sff_console_print", 620, 520)
        wait_for_version(page, 1)
        page.click("#tool-tidy")
        wait_for_version(page, 2)
        xs = {round(b["x"]) for b in stored(page.bot_id).workspace["blocks"]["blocks"]}
        assert len(xs) == 1  # one column

    def test_the_tip_goes_away_for_good_when_it_is_closed(self, open_editor):
        page = open_editor()
        assert page.is_visible("#ed-tip")
        page.click("#ed-tip-close")
        assert page.is_hidden("#ed-tip")
        page.reload()
        wait_until_ready(page)
        assert page.is_hidden("#ed-tip")


class TestTheLockedBlocks:
    """The block that sets the bot up and the one that logs it in are always there, once."""

    def test_they_can_be_moved_but_not_deleted(self, open_editor):
        page = open_editor()
        flags = page.evaluate(
            """() => ['setup', 'login'].map((id) => {
                const block = window.sffEditor.blocks.workspace().getBlockById(id);
                return [block.isDeletable(), block.isMovable(), block.isEditable()];
            })"""
        )
        assert flags == [[False, True, True]] * 2
        click_block(page, "login")
        page.keyboard.press("Delete")
        page.keyboard.press("Backspace")
        assert block_types(page).count("sff_bot_login") == 1

    def test_they_are_not_in_any_block_list(self, open_editor):
        page = open_editor()
        for category in catalogue.browser_payload()["categories"]:
            open_list(page, category["id"])
            assert not {"sff_bot_setup", "sff_bot_login"} & set(list_types(page)), category["id"]

    def test_they_cannot_be_copied_or_duplicated(self, open_editor):
        page = open_editor()
        click_block(page, "setup")
        page.keyboard.press("Control+c")
        page.keyboard.press("Control+v")
        page.wait_for_timeout(200)
        assert block_types(page).count("sff_bot_setup") == 1
        click_block(page, "setup", button="right")
        items = menu_items(page)
        assert "Turn this block off" in items  # the menu opened and has ordinary entries...
        assert "Duplicate" not in items and not [item for item in items if item.startswith("Delete")]
        page.keyboard.press("Escape")

    def test_an_ordinary_block_can_be_copied_and_deleted(self, open_editor):
        page = open_editor()
        click_block(page, "reply")
        page.keyboard.press("Control+c")
        page.keyboard.press("Control+v")
        page.wait_for_timeout(200)
        assert block_types(page).count("sff_reply") == 2
        click_block(page, "reply", button="right")
        items = menu_items(page)
        assert "Duplicate" in items and [item for item in items if item.startswith("Delete")]
        page.keyboard.press("Escape")

    def test_moving_them_is_saved_and_nothing_else_changes(self, open_editor):
        page = open_editor()
        box = block_box(page, "login")
        page.mouse.move(box["left"] + 5, box["top"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["left"] + 200, box["top"] + 40, steps=8)
        page.mouse.up()
        wait_for_version(page, 1)
        kept = {b["id"]: b for b in stored(page.bot_id).workspace["blocks"]["blocks"]}
        assert kept["login"]["x"] > default_workspace()["blocks"]["blocks"][-1].get("x", 0)
        assert [b["type"] for b in kept.values()].count("sff_bot_login") == 1


# =============================================================================================== saving
class TestSaving:
    def test_a_save_does_not_reload_the_blocks_or_throw_away_what_can_be_undone(self, open_editor):
        page = open_editor()
        page.evaluate(
            """() => {
                const E = window.sffEditor;
                window.__loads = 0;
                const original = E.blocks.load;
                E.blocks.load = function () { window.__loads += 1; return original.apply(this, arguments); };
            }"""
        )
        open_list(page, "bot")
        drag_from_list(page, "sff_console_print", 620, 520)
        wait_for_version(page, 1)
        # a second change, after the first save, to be sure it is not just the first one that is left alone
        open_list(page, "bot")
        drag_from_list(page, "sff_console_print", 620, 420)
        wait_for_version(page, 2)
        assert page.evaluate("() => window.__loads") == 0
        assert page.evaluate("() => window.sffEditor.blocks.workspace().getUndoStack().length") >= 2
        assert page.is_enabled("#tool-undo")
        assert_quiet(page)

    def test_a_save_made_while_more_changes_are_being_made_loses_nothing(self, open_editor):
        page = open_editor()
        open_list(page, "bot")
        drag_from_list(page, "sff_console_print", 620, 520)
        page.wait_for_timeout(500)
        open_list(page, "bot")
        drag_from_list(page, "sff_console_print", 620, 420)
        wait_for_version(page, 1)
        for _ in range(40):
            if sum(b["type"] == "sff_console_print" for b in stored(page.bot_id).workspace["blocks"]["blocks"]) == 2:
                break
            page.wait_for_timeout(150)
        loose = [b for b in stored(page.bot_id).workspace["blocks"]["blocks"] if b["type"] == "sff_console_print"]
        assert len(loose) == 2

    def test_control_s_saves_without_waiting(self, open_editor):
        page = open_editor()
        page.evaluate(ADD_A_BLOCK)
        assert status(page) == "Unsaved changes"
        assert stored(page.bot_id).version == 0
        page.keyboard.press("Control+s")
        page.wait_for_selector(".toast:has-text('Saved')")
        assert stored(page.bot_id).version == 1
        assert status(page) == "Saved"

    def test_leaving_the_page_saves_what_is_waiting_in_a_way_that_survives_it(self, open_editor):
        page = open_editor()
        page.evaluate(
            """() => {
                window.__puts = [];
                const original = window.fetch.bind(window);
                window.fetch = (url, init) => {
                    if (init && init.method === 'PUT') window.__puts.push(!!init.keepalive);
                    return original(url, init);
                };
            }"""
        )
        page.evaluate(ADD_A_BLOCK)
        assert stored(page.bot_id).version == 0
        page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
        wait_for_version(page, 1)
        # a request that is allowed to finish after the page is gone (the ordinary save is not)
        assert page.evaluate("() => window.__puts") == [True]

    def test_the_browser_asks_before_leaving_with_unsaved_changes_and_not_otherwise(self, open_editor):
        page = open_editor()
        ask = """() => {
            const event = new Event('beforeunload', {cancelable: true});
            window.dispatchEvent(event);
            return event.defaultPrevented;
        }"""
        assert page.evaluate(ask) is False  # nothing to lose
        page.evaluate(ADD_A_BLOCK)
        assert page.evaluate(ask) is True
        wait_for_version(page, 1)
        assert page.evaluate(ask) is False  # saved, so nothing to lose again

    def test_when_the_server_cannot_be_reached_it_keeps_trying_and_nothing_is_lost(self, open_editor):
        page = open_editor()
        page.route(
            "**/api/bots/*/workspace",
            lambda route: route.abort() if route.request.method == "PUT" else route.continue_(),
        )
        open_list(page, "bot")
        drag_from_list(page, "sff_console_print", 620, 520)
        page.wait_for_function("() => document.getElementById('ed-status').dataset.state === 'offline'", timeout=8000)
        assert "Trying again" in status(page)
        assert stored(page.bot_id).version == 0
        page.unroute("**/api/bots/*/workspace")
        wait_for_version(page, 1, seconds=15)
        assert any(b["type"] == "sff_console_print" for b in stored(page.bot_id).workspace["blocks"]["blocks"])
        page.problems[:] = [p for p in page.problems if "ERR_FAILED" not in p and "Failed to load resource" not in p]
        assert_quiet(page)

    def test_blocks_that_are_damaged_in_the_database_start_again_from_the_starter_blocks(self, open_editor):
        page = open_editor(bot_text="{this is not json")
        page.wait_for_selector(".toast:has-text('damaged')")
        assert top_types(page) == ["sff_bot_setup", "sff_event_ready", "sff_event_command", "sff_bot_login"]
        assert_quiet(page)

    def test_saved_blocks_that_cannot_all_be_loaded_say_how_many_were_left_out(self, open_editor):
        raw = default_workspace()
        raw["blocks"]["blocks"].append({"type": "sff_made_up", "id": "ghost", "x": 5, "y": 5})
        page = open_editor(bot_text=json.dumps(raw))
        page.wait_for_selector(".toast:has-text('left out')")
        assert "sff_made_up" not in block_types(page)


class TestWhenTheBlocksChangedSomewhereElse:
    @pytest.fixture
    def conflict(self, open_editor):
        """An editor whose bot was saved by another tab, and then changed in this one."""
        page = open_editor()
        other = default_workspace(command="other", reply="From the other tab")
        put_in_database(page.bot_id, json.dumps(other), moves_version_on=True)
        open_list(page, "bot")
        drag_from_list(page, "sff_console_print", 620, 520)
        page.wait_for_selector("dialog.dialog[open]", timeout=8000)
        yield page
        page.problems[:] = [p for p in page.problems if "409" not in p]  # the refused save is logged by the browser

    def test_the_person_is_asked_and_nothing_is_overwritten(self, conflict):
        page = conflict
        assert "changed somewhere else" in page.inner_text("dialog.dialog h2")
        assert status(page) == "Changed somewhere else"
        kept = stored(page.bot_id)
        assert kept.version == 1
        assert first_of(kept.workspace, "sff_event_command")["fields"]["NAME"] == "other"
        assert types_in(kept.workspace).count("sff_console_print") == 1  # the starter one; the dragged one was refused

    def test_loading_the_other_version_replaces_what_is_in_this_tab(self, conflict):
        page = conflict
        page.click("dialog.dialog .choice:has-text('Load the other version')")
        page.wait_for_selector(".toast:has-text('Loaded the newer version')")
        assert status(page) == "Saved"
        assert "async def other(ctx)" in python_shown(page)
        assert "From the other tab" in python_shown(page)
        assert block_types(page).count("sff_console_print") == 1  # the one dragged in here is gone
        assert stored(page.bot_id).version == 1

    def test_keeping_this_version_replaces_the_other_one(self, conflict):
        page = conflict
        page.click("dialog.dialog .choice:has-text('Keep the version in this tab')")
        wait_for_version(page, 2)
        kept = stored(page.bot_id).workspace
        assert types_in(kept).count("sff_console_print") == 2  # the starter one and the one dragged in this tab
        assert first_of(kept, "sff_event_command")["fields"]["NAME"] == "ping"

    def test_deciding_later_leaves_the_question_to_be_asked_again(self, conflict):
        page = conflict
        page.click("dialog.dialog button:has-text('Decide later')")
        assert page.locator("dialog.dialog[open]").count() == 0
        assert status(page) == "Changed somewhere else"
        assert stored(page.bot_id).version == 1
        page.click("#ed-status")  # the status is a button now
        page.wait_for_selector("dialog.dialog[open]")
        assert "changed somewhere else" in page.inner_text("dialog.dialog h2")

    def test_escape_is_deciding_later(self, conflict):
        page = conflict
        page.keyboard.press("Escape")
        assert page.locator("dialog.dialog[open]").count() == 0
        assert status(page) == "Changed somewhere else"


# =============================================================================================== variables
class TestVariables:
    def make(self, page, category, name):
        open_list(page, category)
        page.locator(".blocklyFlyoutButton").first.click()
        page.wait_for_selector("dialog.dialog[open] #dlg-input")
        page.fill("#dlg-input", name)
        page.keyboard.press("Enter")
        page.wait_for_selector("dialog.dialog[open]", state="detached")

    def names(self, page):
        return page.evaluate(
            "() => window.sffEditor.blocks.workspace().getVariableMap().getAllVariables().map((v) => [v.getName(), v.getType()])"
        )

    def test_making_a_variable_opens_the_list_again_with_its_blocks(self, open_editor):
        page = open_editor()
        self.make(page, "variables", "score")
        assert self.names(page) == [["score", ""]]
        page.wait_for_function("() => window.sffEditor.blocks.workspace().getToolbox().getFlyout().isVisible()")
        assert list_types(page) == ["sff_set_variable", "sff_change_variable", "sff_variable"]
        wait_for_version(page, 1)
        assert stored(page.bot_id).workspace["variables"][0]["name"] == "score"
        assert_quiet(page)

    def test_a_list_is_made_the_same_way_and_has_its_own_blocks(self, open_editor):
        page = open_editor()
        self.make(page, "lists", "fruits")
        assert self.names(page) == [["fruits", "List"]]
        types = list_types(page)
        assert "sff_list_add" in types and "sff_for_each" not in types  # that one needs a variable of its own
        assert "Make a variable" in [t.strip() for t in page.locator(".blocklyFlyoutButton").all_text_contents()]

    def test_a_name_that_is_taken_is_refused_with_a_reason(self, open_editor):
        page = open_editor()
        self.make(page, "variables", "Score")
        open_list(page, "lists")
        page.locator(".blocklyFlyoutButton").first.click()
        page.wait_for_selector("dialog.dialog[open] #dlg-input")
        page.fill("#dlg-input", "  score ")
        page.keyboard.press("Enter")
        page.wait_for_selector("dialog.dialog .field-error:not([hidden])")
        assert "already a variable or list called" in page.inner_text("dialog.dialog .field-error")
        page.fill("#dlg-input", "")
        page.keyboard.press("Enter")
        assert "name" in page.inner_text("dialog.dialog .field-error").lower()
        page.keyboard.press("Escape")
        assert self.names(page) == [["Score", ""]]

    def test_a_variable_can_be_renamed_and_the_blocks_follow(self, open_editor):
        page = open_editor()
        self.make(page, "variables", "score")
        drag_from_list(page, "sff_set_variable", 620, 520)
        block_id = page.evaluate(
            "() => window.sffEditor.blocks.workspace().getAllBlocks(false).find((b) => b.type === 'sff_set_variable').id"
        )
        x, y = field_centre(page, block_id, "VAR")
        page.mouse.click(x, y)
        page.wait_for_selector(".blocklyMenuItem")
        assert any(item.startswith("Rename") for item in menu_items(page))
        page.click(".blocklyMenuItem:has-text('Rename')")
        page.wait_for_selector("dialog.dialog[open] #dlg-input")
        assert "score" in page.inner_text("dialog.dialog label")  # it says which one is being renamed
        page.fill("#dlg-input", "points")
        page.keyboard.press("Enter")
        page.wait_for_selector("dialog.dialog[open]", state="detached")
        assert self.names(page) == [["points", ""]]
        shown = f"() => window.sffEditor.blocks.workspace().getBlockById('{block_id}').getField('VAR').getText()"
        assert page.evaluate(shown) == "points"
        wait_for_version(page, 1)
        assert stored(page.bot_id).workspace["variables"][0]["name"] == "points"

    def test_a_variable_can_be_deleted_from_the_dropdown(self, open_editor):
        page = open_editor()
        self.make(page, "variables", "score")
        drag_from_list(page, "sff_set_variable", 620, 520)
        block_id = page.evaluate(
            "() => window.sffEditor.blocks.workspace().getAllBlocks(false).find((b) => b.type === 'sff_set_variable').id"
        )
        x, y = field_centre(page, block_id, "VAR")
        page.mouse.click(x, y)
        page.wait_for_selector(".blocklyMenuItem")
        page.click(".blocklyMenuItem:has-text('Delete')")
        page.wait_for_timeout(300)
        assert self.names(page) == []
        assert "sff_set_variable" not in block_types(page)
        page.click("#tool-undo")  # taking it back brings the variable and its block with it
        assert self.names(page) == [["score", ""]]
        assert "sff_set_variable" in block_types(page)


# =============================================================================================== files
class TestFiles:
    def add(self, page, *files):
        page.click("#tab-files")
        with page.expect_file_chooser() as chooser:
            page.click("#files-add")
        chooser.value.set_files(list(files))
        for file in files:
            page.wait_for_selector(f'.ed-files .ed-file[data-name="{file["name"]}"]')

    def test_files_are_added_listed_and_counted(self, open_editor):
        page = open_editor()
        assert page.inner_text("#files-usage").startswith("0 files of 40")
        self.add(page, CAT, JOKES, NOTES)
        assert page.locator(".ed-files .ed-file").count() == 3
        assert page.inner_text("#files-count") == "3"
        assert page.inner_text("#files-usage").startswith("3 files of 40")
        kinds = page.locator(".ed-files .ed-file .ed-file-meta span:first-child").all_inner_texts()
        assert [k.split(" · ")[0] for k in kinds] == ["Image", "JSON", "Text"]
        page.wait_for_function("() => document.querySelector('.ed-files .ed-thumb img')?.naturalWidth === 8")
        assert_quiet(page)

    def test_the_files_are_still_there_after_a_reload(self, open_editor):
        page = open_editor()
        self.add(page, CAT, NOTES)
        page.reload()
        wait_until_ready(page)
        page.click("#tab-files")
        assert page.locator(".ed-files .ed-file").count() == 2

    def test_the_block_lists_start_offering_the_files(self, open_editor):
        page = open_editor()
        chosen = (
            "() => window.sffEditor.blocks.workspace().getToolbox().getFlyout().getWorkspace()"
            ".getTopBlocks(false).find((b) => b.type === 'sff_reply_image').getFieldValue('FILE')"
        )
        open_list(page, "data")
        assert page.evaluate(chosen) == ""  # the block is there, but there is no file to choose yet
        open_list(page, "bot")
        self.add(page, CAT)
        open_list(page, "data")
        assert page.evaluate(chosen) == "cat.png"

    def test_a_block_can_use_a_file_and_the_files_tab_says_so(self, open_editor):
        page = open_editor()
        self.add(page, CAT, DOG, JOKES)
        open_list(page, "data")
        drop_under(page, "sff_reply_image", "reply")
        image_block = page.evaluate(
            "() => window.sffEditor.blocks.workspace().getAllBlocks(false).find((b) => b.type === 'sff_reply_image').id"
        )
        # its dropdown lists the pictures (and only the pictures), then offers to add another
        x, y = field_centre(page, image_block, "FILE")
        page.mouse.click(x, y)
        page.wait_for_selector(".blocklyMenuItem")
        assert menu_items(page) == ["cat.png", "dog.png", "Upload a file…"]
        page.click(".blocklyMenuItem:has-text('dog.png')")
        page.wait_for_function(
            "() => document.getElementById('code-view').textContent.includes('dog.png')", timeout=10000
        )
        assert 'dog.png"' in python_shown(page)
        page.click("#tab-files")
        assert page.inner_text('.ed-files .ed-file[data-name="dog.png"] .ed-link') == "Used by 1 block"
        assert page.locator('.ed-files .ed-file[data-name="cat.png"] .ed-link').count() == 0
        # "Used by" finds the block
        page.click('.ed-files .ed-file[data-name="dog.png"] .ed-link')
        page.wait_for_timeout(500)
        assert selected_id(page) == image_block

    def test_the_dropdown_on_a_block_can_add_a_file_too(self, open_editor):
        page = open_editor()
        self.add(page, CAT)
        open_list(page, "data")
        drop_under(page, "sff_reply_image", "reply")
        image_block = page.evaluate(
            "() => window.sffEditor.blocks.workspace().getAllBlocks(false).find((b) => b.type === 'sff_reply_image').id"
        )
        x, y = field_centre(page, image_block, "FILE")
        page.mouse.click(x, y)
        page.wait_for_selector(".blocklyMenuItem")
        with page.expect_file_chooser() as chooser:
            page.click(".blocklyMenuItem:has-text('Upload a file')")
        accept = chooser.value.element.evaluate("input => input.accept")
        assert set(accept.split(",")) == {".png", ".jpg", ".jpeg", ".gif", ".webp"}  # only pictures for a picture block
        chooser.value.set_files([DOG])
        page.wait_for_function(
            f"() => window.sffEditor.blocks.workspace().getBlockById('{image_block}').getFieldValue('FILE') === 'dog.png'"
        )

    def test_pictures_and_data_can_be_previewed(self, open_editor):
        page = open_editor()
        self.add(page, CAT, JOKES)
        page.click('.ed-files .ed-file[data-name="cat.png"] .ed-thumb')
        page.wait_for_selector("dialog.dialog[open] img.ed-preview-img")
        page.wait_for_function("() => document.querySelector('img.ed-preview-img').naturalWidth === 8")
        page.keyboard.press("Escape")
        page.click('.ed-files .ed-file[data-name="jokes.json"] .ed-thumb')
        page.wait_for_function("() => document.querySelector('pre.ed-preview-text')?.textContent.includes('one')")
        text = page.inner_text("pre.ed-preview-text")
        assert json.loads(text) == {"jokes": ["one", "two"]}
        assert "\n  " in text  # shown in a readable layout, not as it was uploaded
        page.click("dialog.dialog button[aria-label=Close]")
        assert page.locator("dialog.dialog[open]").count() == 0

    def test_a_file_the_bot_cannot_use_is_refused_in_plain_words(self, open_editor):
        page = open_editor()
        page.click("#tab-files")
        with page.expect_file_chooser() as chooser:
            page.click("#files-add")
        chooser.value.set_files(
            [
                upload_file("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"),
                upload_file("run.exe", b"MZ", "application/octet-stream"),
                upload_file("empty.txt", b"", "text/plain"),
            ]
        )
        page.wait_for_selector(".toast:has-text('empty.txt is empty')")
        toasts = " ".join(page.locator(".toast").all_inner_texts())
        assert "logo.svg isn't a file type that can be used" in toasts
        assert "run.exe isn't a file type that can be used" in toasts
        assert page.locator(".ed-files .ed-file").count() == 0

    def test_a_file_that_is_too_big_is_refused_before_it_is_sent(self, open_editor):
        page = open_editor()
        limit = page.evaluate("() => window.sffEditor.files.limits().max_file_bytes")
        page.click("#tab-files")
        with page.expect_file_chooser() as chooser:
            page.click("#files-add")
        chooser.value.set_files([upload_file("big.txt", b"x" * (limit + 1), "text/plain")])
        page.wait_for_selector(".toast:has-text('big.txt is bigger than')")
        assert page.locator(".ed-files .ed-file").count() == 0

    def test_a_file_with_a_name_in_use_asks_before_it_replaces_it(self, open_editor):
        page = open_editor()
        self.add(page, CAT)
        with page.expect_file_chooser() as chooser:
            page.click("#files-add")
        chooser.value.set_files([upload_file("cat.png", tiny_png(16, 16), "image/png")])
        page.wait_for_selector("dialog.dialog[open]")
        assert "Replace cat.png?" in page.inner_text("dialog.dialog h2")
        page.click("dialog.dialog button:has-text('Cancel')")
        assert page.locator(".ed-files .ed-file").count() == 1
        with page.expect_file_chooser() as chooser:
            page.click("#files-add")
        chooser.value.set_files([upload_file("cat.png", tiny_png(16, 16), "image/png")])
        page.click("dialog.dialog button:has-text('Replace')")
        page.wait_for_selector(".toast:has-text('Replaced cat.png')")
        assert page.locator(".ed-files .ed-file").count() == 1

    def test_dropping_files_on_the_page_adds_them(self, open_editor):
        page = open_editor()
        page.evaluate(
            """() => {
                const transfer = new DataTransfer();
                transfer.items.add(new File(['hello'], 'hello.txt', {type: 'text/plain'}));
                document.dispatchEvent(new DragEvent('dragenter', {dataTransfer: transfer, bubbles: true}));
                window.__veilShown = !document.getElementById('ed-dropveil').hidden;
                document.dispatchEvent(new DragEvent('drop', {dataTransfer: transfer, bubbles: true, cancelable: true}));
            }"""
        )
        assert page.evaluate("() => window.__veilShown") is True
        page.wait_for_selector('.ed-files .ed-file[data-name="hello.txt"]')
        assert page.is_hidden("#ed-dropveil")
        assert page.get_attribute("#tab-files", "aria-selected") == "true"  # and the Files tab is showing

    def test_deleting_a_file_that_a_block_uses_explains_and_marks_the_block(self, open_editor):
        page = open_editor()
        self.add(page, CAT)
        open_list(page, "data")
        drop_under(page, "sff_reply_image", "reply")
        wait_for_version(page, 1)
        page.click("#tab-files")
        page.click('.ed-files .ed-file[data-name="cat.png"] button[aria-label^="Delete"]')
        page.wait_for_selector("dialog.dialog[open]")
        assert "1 block uses this file" in page.inner_text("dialog.dialog")
        page.click("dialog.dialog .btn-danger")
        page.wait_for_selector(".toast:has-text('Deleted cat.png')")
        assert page.locator(".ed-files .ed-file").count() == 0
        # the block keeps its choice and shows it as missing; the Problems tab and the pill say what to do
        assert page.locator(".sff-missing").count() == 1
        assert page.inner_text("#problems-count") == "1"
        assert page.inner_text("#ed-problems-pill") == "1 problem"
        page.click("#tab-problems")
        assert "cat.png" in page.inner_text(".ed-problem")
        assert page.get_attribute("#get-py", "aria-disabled") == "true"
        # and choosing the problem finds the block
        page.click(".ed-problem")
        page.wait_for_timeout(500)
        assert page.evaluate("() => window.Blockly.common.getSelected().type") == "sff_reply_image"
        # putting the file back makes it all go away
        self.add(page, CAT)
        page.wait_for_function("() => document.getElementById('ed-problems-pill').hidden")
        assert page.locator(".sff-missing").count() == 0
        assert page.get_attribute("#get-py", "aria-disabled") == "false"

    def test_removing_a_file_nothing_uses_just_asks_to_be_sure(self, open_editor):
        page = open_editor()
        self.add(page, NOTES)
        page.click('.ed-files .ed-file[data-name="notes.txt"] button[aria-label^="Delete"]')
        page.wait_for_selector("dialog.dialog[open]")
        assert "can't be undone" in page.inner_text("dialog.dialog")
        page.click("dialog.dialog button:has-text('Cancel')")
        assert page.locator(".ed-files .ed-file").count() == 1
        page.click('.ed-files .ed-file[data-name="notes.txt"] button[aria-label^="Delete"]')
        page.click("dialog.dialog .btn-danger")
        page.wait_for_function("() => document.querySelectorAll('.ed-files .ed-file').length === 0")


# =============================================================================================== the Python tab
class TestThePythonTab:
    def test_selecting_a_block_lights_up_the_lines_it_wrote(self, open_editor):
        page = open_editor()
        lit = page.locator("#code-view .cl.is-lit")
        click_block(page, "reply")
        assert selected_id(page) == "reply"
        expect(lit).to_have_count(1)
        expect(lit).to_contain_text('await ctx.reply("Pong!")')
        click_block(page, "login")
        expect(lit).to_have_count(1)
        expect(lit).to_contain_text('bot.run(os.environ["FLUXER_TOKEN"])')
        page.mouse.click(700, 600)  # empty space: nothing is selected, so nothing is lit
        expect(lit).to_have_count(0)

    def test_a_block_inside_another_lights_up_the_block_that_wrote_the_line(self, open_editor):
        state = workspace_of(command("hello", reply(join(text("Hi, "), text("there")))))
        page = open_editor(state=state)
        page.evaluate(
            """() => {
                const ws = window.sffEditor.blocks.workspace();
                window.Blockly.common.setSelected(ws.getAllBlocks(false).find((b) => b.type === 'sff_join'));
            }"""
        )
        lit = page.locator("#code-view .cl.is-lit")
        expect(lit).to_have_count(1)
        expect(lit).to_contain_text("ctx.reply(")

    def test_a_block_that_wrote_several_lines_lights_them_all(self, open_editor):
        page = open_editor()
        page.evaluate(
            "() => window.Blockly.common.setSelected(window.sffEditor.blocks.workspace().getBlockById('command'))"
        )
        expect(page.locator("#code-view .cl.is-lit")).to_have_count(3)
        lit = page.locator("#code-view .cl.is-lit").all_inner_texts()
        assert "@bot.command()" in lit[0] and "Pong" in lit[2]
        assert page.locator("#code-view .cl.is-lit-first").count() == 1
        assert page.locator("#code-view .cl.is-lit-last").count() == 1

    def test_clicking_a_line_selects_the_block_that_wrote_it(self, open_editor):
        page = open_editor()
        lines = json.loads(
            page.evaluate(
                "async (id) => JSON.stringify((await (await fetch('/api/bots/' + id + '/workspace')).json()).compile.lines)",
                page.bot_id,
            )
        )
        first, _ = lines["command"]
        page.click(f'#code-view .cl[data-line="{first}"] .ct')
        page.wait_for_timeout(400)
        assert selected_id(page) == "command"
        page.click(f'#code-view .cl[data-line="{lines["reply"][0]}"] .ct')
        page.wait_for_timeout(400)
        assert selected_id(page) == "reply"

    def test_the_code_is_coloured(self, open_editor):
        page = open_editor()
        classes = set(
            page.evaluate("() => [...document.querySelectorAll('#code-view [class^=py-]')].map((e) => e.className)")
        )
        assert {"py-kw", "py-str", "py-fn"} <= classes
        assert page.locator("#code-view .py-fn").first.inner_text() == "on_ready"

    def test_the_python_colouring_reads_what_it_is_given_without_losing_a_character(self, open_editor):
        page = open_editor()
        samples = [
            'print("hi")  # note',
            "x = 12.5e3 + .5 - 0x1F",
            "s = f'{a}' + r\"\\d\" + 'it\\'s'",
            "@bot.event",
            "async def on_ready():",
            "'unterminated string",
            '"""triple',
            "",
            "   leading spaces\tand\ttabs",
            "ünïcode = 'ملحوظة'",
        ]
        for sample in samples:
            rebuilt = page.evaluate(
                """(line) => window.sffEditor.python.tokens(line).map((t) => t[1]).join('')""", sample
            )
            assert rebuilt == sample

    def test_the_copy_button_copies_the_code(self, open_editor):
        page = open_editor()
        page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=page.base)
        page.click("#code-copy")
        page.wait_for_function("() => document.querySelector('#code-copy span').textContent === 'Copied'")
        copied = page.evaluate("() => navigator.clipboard.readText()")
        assert copied.rstrip("\n") == python_shown(page)

    def test_the_code_view_scrolls_by_itself_but_the_page_does_not(self, open_editor):
        page = open_editor(viewport={"width": 1280, "height": 360})
        page.evaluate(
            "() => window.Blockly.common.setSelected(window.sffEditor.blocks.workspace().getBlockById('login'))"
        )
        page.wait_for_function("() => document.getElementById('code-view').scrollTop > 0")
        assert page.evaluate("() => document.scrollingElement.scrollTop") == 0


# =============================================================================================== problems
class TestProblems:
    def test_an_empty_name_is_pointed_out_and_fixing_it_clears_it(self, open_editor):
        page = open_editor()
        x, y = field_centre(page, "command", "NAME")
        page.mouse.click(x, y)
        page.wait_for_selector(".blocklyHtmlInput")
        page.keyboard.press("Control+a")
        page.keyboard.press("Backspace")
        page.keyboard.press("Enter")
        page.wait_for_function("() => !document.getElementById('ed-problems-pill').hidden", timeout=8000)
        assert page.inner_text("#ed-problems-pill") == "1 problem"
        assert page.inner_text("#problems-count") == "1"
        assert "This bot can't run yet: 1 problem to fix." in page.inner_text("#code-banner")
        assert page.locator(".sff-error").count() == 1
        # the downloads are held back with an explanation
        assert page.get_attribute("#get-py", "aria-disabled") == "true"
        page.click("#ed-get summary")
        assert page.is_visible("#get-note")
        page.click("#get-py", force=True)  # it looks switched off, but a click still explains why
        page.wait_for_selector(".toast:has-text('Fix the problems')")
        # the problem leads to the block
        page.click("#ed-problems-pill")
        assert page.get_attribute("#tab-problems", "aria-selected") == "true"
        assert "Give this command a name." in page.inner_text(".ed-problem")
        page.click(".ed-problem")
        page.wait_for_timeout(500)
        assert selected_id(page) == "command"
        # type a name and everything calms down
        x, y = field_centre(page, "command", "NAME")
        page.mouse.click(x, y)
        page.wait_for_selector(".blocklyHtmlInput")
        page.keyboard.type("hello")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.getElementById('ed-problems-pill').hidden", timeout=8000)
        assert page.locator(".sff-error").count() == 0
        assert page.get_attribute("#get-py", "aria-disabled") == "false"
        assert "No problems" in page.inner_text("#problems-list")
        assert "async def hello(ctx)" in python_shown(page)


# =============================================================================================== getting the code
class TestGettingTheCode:
    def test_the_python_file_is_the_python_on_screen(self, open_editor):
        page = open_editor()
        page.click("#ed-get summary")
        with page.expect_download() as download:
            page.click("#get-py")
        assert download.value.suggested_filename == "main.py"
        path = download.value.path()
        with open(path, encoding="utf-8") as handle:
            assert handle.read().rstrip("\n") == python_shown(page)

    def test_the_zip_holds_the_python_and_the_files(self, open_editor):
        page = open_editor()
        TestFiles().add(page, CAT)
        page.click("#ed-get summary")
        with page.expect_download() as download:
            page.click("#get-zip")
        import zipfile

        with zipfile.ZipFile(download.value.path()) as archive:
            names = archive.namelist()
        assert any(name.endswith("main.py") for name in names)
        assert any(name.endswith("cat.png") for name in names)

    def test_what_was_just_done_is_in_the_file(self, open_editor):
        """A download that starts before the last change is saved waits for the save, then downloads."""
        page = open_editor()
        open_list(page, "messages")
        drop_under(page, "sff_send_here", "reply")
        page.click("#ed-get summary")
        with page.expect_download() as download:
            page.click("#get-py")
        with open(download.value.path(), encoding="utf-8") as handle:
            assert "send(" in handle.read()


# =============================================================================================== layout
class TestOnAPhone:
    def test_one_thing_is_shown_at_a_time_and_the_dock_switches(self, open_editor):
        page = open_editor(viewport={"width": 390, "height": 844})
        root = "#editor"
        assert page.get_attribute(root, "data-view") == "blocks"
        assert page.is_visible("#ed-dock")
        assert page.is_visible("#blockly") and not page.is_visible("#code-view")
        shown = {
            "code": "#code-view",
            "files": "#files-drop",
            "problems": "#problems-list",
            "console": "#console-log",
        }
        for view, selector in shown.items():
            page.click(f'#ed-dock button[data-view="{view}"]')
            assert page.get_attribute(root, "data-view") == view
            assert page.get_attribute(f'#ed-dock button[data-view="{view}"]', "aria-pressed") == "true"
            assert page.locator(selector).first.is_visible(), view
            assert not page.is_visible("#blockly"), view
        page.click('#ed-dock button[data-view="blocks"]')
        assert page.is_visible("#blockly") and not page.is_visible("#console-log")
        assert_quiet(page)

    def test_nothing_is_wider_than_the_screen(self, open_editor):
        page = open_editor(viewport={"width": 390, "height": 844})
        for view in ("blocks", "code", "files", "problems", "console"):
            page.click(f'#ed-dock button[data-view="{view}"]')
            assert page.evaluate("() => document.documentElement.scrollWidth") <= 390, view

    def test_blocks_can_be_added_with_a_finger_sized_target(self, open_editor):
        page = open_editor(viewport={"width": 390, "height": 844})
        rail = page.evaluate("() => document.querySelector('.blocklyToolbox').getBoundingClientRect().width")
        assert 56 <= rail <= 90
        boxes = page.evaluate(
            "() => [...document.querySelectorAll('.blocklyToolboxCategory')].map((e) => e.getBoundingClientRect().height)"
        )
        assert min(boxes) >= 44

    def test_choosing_a_problem_goes_back_to_the_blocks(self, open_editor):
        page = open_editor(viewport={"width": 390, "height": 844})
        page.evaluate("() => window.sffEditor.blocks.workspace().getBlockById('command').setFieldValue('', 'NAME')")
        page.wait_for_function("() => !document.getElementById('ed-problems-pill').hidden", timeout=8000)
        page.click('#ed-dock button[data-view="problems"]')
        page.click(".ed-problem")
        page.wait_for_function("() => document.getElementById('editor').dataset.view === 'blocks'")
        assert selected_id(page) == "command"


class TestOnAWideScreen:
    @pytest.mark.parametrize("size", [(900, 700), (1024, 768), (1920, 1080)])
    def test_everything_fits_and_the_panels_can_be_resized(self, open_editor, size):
        width, height = size
        page = open_editor(viewport={"width": width, "height": height})
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        assert page.evaluate("() => document.documentElement.scrollHeight") <= height
        before = page.evaluate("() => document.querySelector('.ed-side').getBoundingClientRect().width")
        page.focus("#split-side")
        page.keyboard.press("ArrowLeft")
        after = page.evaluate("() => document.querySelector('.ed-side').getBoundingClientRect().width")
        assert after > before
        page.dblclick("#split-side")  # a double click puts it back
        again = page.evaluate("() => document.querySelector('.ed-side').getBoundingClientRect().width")
        assert abs(again - before) < 2
        assert_quiet(page)

    def test_the_tabs_work_from_the_keyboard(self, open_editor):
        page = open_editor()
        page.focus("#tab-code")
        page.keyboard.press("ArrowRight")
        assert page.get_attribute("#tab-files", "aria-selected") == "true"
        assert page.is_visible("#pane-files") and not page.is_visible("#pane-code")
        page.keyboard.press("End")
        assert page.get_attribute("#tab-problems", "aria-selected") == "true"
        page.keyboard.press("Home")
        assert page.get_attribute("#tab-code", "aria-selected") == "true"

    def test_the_tab_that_was_open_is_remembered(self, open_editor):
        page = open_editor()
        page.click("#tab-files")
        page.reload()
        wait_until_ready(page)
        assert page.get_attribute("#tab-files", "aria-selected") == "true"


# =============================================================================================== the cleaner and Blockly agree
class TestWhatTheServerKeepsIsWhatBlocklyDraws:
    """Anything the cleaner lets through loads into the real Blockly and comes back out exactly as it went in.

    If it didn't, the editor would change the blocks by itself as soon as it saved, and the person would see blocks
    move, reload or vanish for no reason.
    """

    def test_random_and_damaged_workspaces_come_back_unchanged(self, open_editor):
        page = open_editor()
        page.evaluate(
            """() => {
                window.__warnings = [];
                const warn = console.warn.bind(console);
                console.warn = (...args) => { window.__warnings.push(args.map(String).join(' ')); warn(...args); };
            }"""
        )
        differ = []
        checked = 0
        for seed in range(FIRST, FIRST + COUNT):
            rng = random.Random(seed)
            raw = Randomizer(rng, mistakes=rng.choice([0.0, 0.05, 0.2])).workspace()
            if seed % 3 == 0:
                raw = mutate(rng, raw, rng.randint(1, 8))
            try:
                cleaned = workspace.clean_workspace(raw).workspace
            except workspace.WorkspaceTooBig:
                continue
            result = page.evaluate(
                """(state) => {
                    window.__warnings.length = 0;
                    try { window.sffEditor.blocks.load(state); }
                    catch (error) { return {error: String(error && error.message || error)}; }
                    return {saved: window.sffEditor.blocks.save(), warnings: window.__warnings.slice()};
                }""",
                cleaned,
            )
            checked += 1
            if "error" in result:
                differ.append((seed, "could not be loaded: " + result["error"][:200]))
            elif result["warnings"]:
                differ.append((seed, "Blockly warned: " + result["warnings"][0][:200]))
            elif json.dumps(result["saved"], sort_keys=True) != json.dumps(cleaned, sort_keys=True):
                differ.append((seed, "came back different"))
        assert checked > COUNT // 2
        assert differ == [], differ[:5]
        assert page.problems == []

    def test_a_workspace_with_every_block_in_the_catalogue_loads_and_draws(self, open_editor):
        """Every block, with nothing filled in: the blocks are all drawn (none is a blank or a "missing" shape)."""
        types = [spec["type"] for spec in catalogue.browser_payload()["blocks"]]
        state = {
            "blocks": {
                "languageVersion": 0,
                "blocks": [
                    {"type": t, "id": f"b{i}", "x": 40 + (i % 8) * 150, "y": 100 + (i // 8) * 90}
                    for i, t in enumerate(types)
                ],
            },
            "variables": [],
        }
        page = open_editor(state=state)
        drawn = page.evaluate(
            """() => window.sffEditor.blocks.workspace().getAllBlocks(false)
                .filter((b) => b.getSvgRoot().getBoundingClientRect().width > 0).length"""
        )
        assert drawn >= len(types)
        assert_quiet(page)


# =============================================================================================== editing text on a touch screen
class TestEditingText:
    """On a phone Blockly asks "Change value:" in a dialog. It has to take a whole rule or welcome, as typed."""

    def ask(self, page, message="Change value:", start="old "):
        page.evaluate(
            "([message, start]) => { window.__answer = 'waiting'; "
            "window.Blockly.dialog.prompt(message, start, (value) => { window.__answer = value; }); }",
            [message, start],
        )
        page.wait_for_selector("dialog.dialog[open] #dlg-input")

    def answer(self, page):
        page.wait_for_function("() => window.__answer !== 'waiting'")
        return page.evaluate("() => window.__answer")

    def test_it_takes_as_much_as_a_text_block_holds_and_keeps_it_as_typed(self, open_editor):
        page = open_editor()
        self.ask(page)
        assert page.get_attribute("#dlg-input", "maxlength") == "2000"
        assert page.input_value("#dlg-input") == "old "
        assert "Edit text" in page.inner_text("dialog.dialog h2")
        text = ("Be kind. " * 200) + "Welcome, "  # 1,809 characters, with a space at the end
        page.fill("#dlg-input", text)
        page.keyboard.press("Enter")
        assert self.answer(page) == text

    def test_it_can_be_left_empty(self, open_editor):
        page = open_editor()
        self.ask(page)
        page.fill("#dlg-input", "")
        page.keyboard.press("Enter")
        assert self.answer(page) == ""

    def test_cancelling_changes_nothing(self, open_editor):
        page = open_editor()
        self.ask(page)
        page.fill("#dlg-input", "something else")
        page.keyboard.press("Escape")
        assert self.answer(page) is None

    def test_other_questions_keep_their_short_limit_and_their_wording(self, open_editor):
        page = open_editor()
        self.ask(page, message="Rename variable to:")
        assert page.get_attribute("#dlg-input", "maxlength") == "80"
        assert "Rename" in page.inner_text("dialog.dialog h2")
        page.fill("#dlg-input", "")
        page.keyboard.press("Enter")
        assert "name" in page.inner_text("dialog.dialog .field-error").lower()


class TestDeletingVariables:
    def make(self, page, category, name):
        open_list(page, category)
        page.locator(".blocklyFlyoutButton").first.click()
        page.wait_for_selector("dialog.dialog[open] #dlg-input")
        page.fill("#dlg-input", name)
        page.keyboard.press("Enter")
        page.wait_for_selector("dialog.dialog[open]", state="detached")

    def names(self, page):
        return page.evaluate(
            "() => window.sffEditor.blocks.workspace().getVariableMap().getAllVariables().map((v) => v.getName())"
        )

    def buttons(self, page):
        return [t.strip() for t in page.locator(".blocklyFlyoutButton").all_text_contents()]

    def test_the_delete_button_only_shows_when_there_is_something_to_delete(self, open_editor):
        page = open_editor()
        open_list(page, "variables")
        assert "Delete a variable" not in self.buttons(page)
        page.locator(".blocklyFlyoutButton").first.click()
        page.wait_for_selector("dialog.dialog[open] #dlg-input")
        page.fill("#dlg-input", "score")
        page.keyboard.press("Enter")
        page.wait_for_selector("dialog.dialog[open]", state="detached")
        page.wait_for_function("() => window.sffEditor.blocks.workspace().getToolbox().getFlyout().isVisible()")
        assert "Delete a variable" in self.buttons(page)

    def test_a_single_variable_is_deleted_after_asking(self, open_editor):
        page = open_editor()
        self.make(page, "variables", "score")
        page.wait_for_function("() => window.sffEditor.blocks.workspace().getToolbox().getFlyout().isVisible()")
        page.locator(".blocklyFlyoutButton", has_text="Delete a variable").click()
        page.wait_for_selector("dialog.dialog[open]")
        assert "Delete the variable" in page.locator("dialog.dialog[open]").inner_text()
        page.locator("dialog.dialog[open] button", has_text="Delete").last.click()
        page.wait_for_selector("dialog.dialog[open]", state="detached")
        assert self.names(page) == []

    def test_cancelling_keeps_the_variable_and_with_two_you_choose_which(self, open_editor):
        page = open_editor()
        self.make(page, "variables", "score")
        page.wait_for_function("() => window.sffEditor.blocks.workspace().getToolbox().getFlyout().isVisible()")
        page.locator(".blocklyFlyoutButton", has_text="Make a variable").click()
        page.wait_for_selector("dialog.dialog[open] #dlg-input")
        page.fill("#dlg-input", "lives")
        page.keyboard.press("Enter")
        page.wait_for_selector("dialog.dialog[open]", state="detached")
        page.wait_for_function("() => window.sffEditor.blocks.workspace().getToolbox().getFlyout().isVisible()")
        page.locator(".blocklyFlyoutButton", has_text="Delete a variable").click()
        page.wait_for_selector("dialog.dialog[open] .choice")
        page.locator("dialog.dialog[open] .choice", has_text="lives").click()
        page.wait_for_selector("dialog.dialog[open] .btn-danger")
        page.locator("dialog.dialog[open] button", has_text="Cancel").click()
        page.wait_for_selector("dialog.dialog[open]", state="detached")
        assert sorted(self.names(page)) == ["lives", "score"]
        page.locator(".blocklyFlyoutButton", has_text="Delete a variable").click()
        page.locator("dialog.dialog[open] .choice", has_text="lives").click()
        page.locator("dialog.dialog[open] .btn-danger").click()
        page.wait_for_selector("dialog.dialog[open]", state="detached")
        assert self.names(page) == ["score"]
