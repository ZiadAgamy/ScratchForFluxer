"""The editor must never save an empty workspace over blocks the person had.

A bug report: after the editor was "messed up", refreshing the page left only the two blocks every bot has. The cause
was a chain: a save was answered with a copy of the blocks the editor could not open, opening it emptied the editor,
and leaving the page then saved that empty editor over the real blocks. Each link is tested here.
"""

from __future__ import annotations

import pytest
from test_editor_browser import (
    ADD_A_BLOCK,
    assert_quiet,
    block_types,
    drag_from_list,
    open_editor,  # noqa: F401 - open_editor is a fixture
    open_list,
    stored,
    top_types,
    wait_for_version,
)

pytestmark = pytest.mark.browser

UNOPENABLE = {
    "blocks": {"languageVersion": 0, "blocks": [{"type": "no_such_block", "id": "x", "x": 1, "y": 1}]},
    "variables": [],
}
STARTER = ["sff_bot_setup", "sff_event_ready", "sff_event_command", "sff_bot_login"]


def stored_types(page) -> list[str]:
    return [b["type"] for b in stored(page.bot_id).workspace["blocks"]["blocks"]]


def test_blocks_that_cannot_be_opened_leave_what_was_there(open_editor):
    page = open_editor()
    page.evaluate(ADD_A_BLOCK)
    before = sorted(block_types(page))
    thrown = page.evaluate(
        """(state) => { try { window.sffEditor.blocks.load(state); return false; } catch (error) { return true; } }""",
        UNOPENABLE,
    )
    assert thrown is True
    assert sorted(block_types(page)) == before  # put back, not left empty or half open
    assert page.evaluate("() => window.sffEditor.blocks.isEmpty()") is False


def test_a_failed_load_is_not_a_change_to_save(open_editor):
    page = open_editor()
    page.evaluate(
        "(state) => { try { window.sffEditor.blocks.load(state); } catch (error) {} }",
        UNOPENABLE,
    )
    page.wait_for_timeout(1800)  # longer than the delay before a change is saved
    assert stored(page.bot_id).version == 0


def test_a_save_answered_with_a_copy_the_editor_cannot_open_keeps_the_blocks_and_saves_nothing_empty(open_editor):
    page = open_editor()

    def answer(route):
        if route.request.method != "PUT":
            return route.continue_()
        real = route.fetch()
        body = real.json()
        body["workspace"] = UNOPENABLE  # what the server says it kept...
        body["dropped"] = 1  # ...because it had to remove blocks, which is when the editor shows the kept copy
        return route.fulfill(response=real, json=body)

    page.route("**/api/bots/*/workspace", answer)
    page.evaluate(ADD_A_BLOCK)
    page.wait_for_selector(".toast:has-text('reopen')", timeout=8000)
    assert sorted(top_types(page)) == sorted([*STARTER, "sff_console_print"])  # still the person's own blocks
    assert page.inner_text("#ed-status-text") == "Saved"  # the save worked; only showing it failed
    kept = stored(page.bot_id)
    assert kept.version == 1
    page.unroute("**/api/bots/*/workspace")
    # leaving the page (a refresh) saves nothing, because nothing is waiting
    page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
    page.wait_for_timeout(1500)
    assert stored(page.bot_id).version == 1
    assert sorted(stored_types(page)) == sorted([*STARTER, "sff_console_print"])
    page.reload()
    page.wait_for_selector("#ed-loading", state="hidden", timeout=20000)
    page.wait_for_function("() => window.sffEditor.blocks.workspace().getAllBlocks(false).length > 0")
    assert sorted(top_types(page)) == sorted([*STARTER, "sff_console_print"])


def test_an_editor_with_no_blocks_never_overwrites_the_saved_ones(open_editor):
    page = open_editor()
    page.evaluate(ADD_A_BLOCK)
    wait_for_version(page, 1)
    assert len(stored_types(page)) == len(STARTER) + 1
    # whatever the cause, the editor ends up with nothing in it
    page.evaluate(
        """() => {
            const E = window.sffEditor;
            const w = E.blocks.workspace();
            window.Blockly.Events.disable();
            w.clear();
            window.Blockly.Events.enable();
            E.bus.emit('change');  /* what the next edit would do */
        }"""
    )
    assert page.evaluate("() => window.sffEditor.blocks.isEmpty()") is True
    page.wait_for_function("() => document.getElementById('ed-status').dataset.state === 'error'", timeout=8000)
    assert "Not saved" in page.inner_text("#ed-status-text")
    page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(1200)
    assert stored(page.bot_id).version == 1
    assert len(stored_types(page)) == len(STARTER) + 1
    # and a reload brings the real blocks back
    page.reload()
    page.wait_for_selector("#ed-loading", state="hidden", timeout=20000)
    page.wait_for_function("() => window.sffEditor.blocks.workspace().getAllBlocks(false).length > 0")
    assert sorted(top_types(page)) == sorted([*STARTER, "sff_console_print"])


def test_deleting_every_block_that_can_be_deleted_is_still_saved(open_editor):
    """The two locked blocks stay, so this is not "empty": the person may clear their script."""
    page = open_editor()
    page.evaluate(ADD_A_BLOCK)
    wait_for_version(page, 1)
    page.evaluate(
        """() => {
            const w = window.sffEditor.blocks.workspace();
            w.getTopBlocks(false).forEach((b) => { if (b.isDeletable()) b.dispose(); });
        }"""
    )
    wait_for_version(page, 2)
    assert stored_types(page) == ["sff_bot_setup", "sff_bot_login"]
    assert_quiet(page)


def server_says(page, change):
    """Answer every save with what the server really said, after ``change(body)`` has had its way with it."""

    def answer(route):
        if route.request.method != "PUT":
            return route.continue_()
        real = route.fetch()
        body = real.json()
        change(body, route.request.post_data_json["workspace"])
        return route.fulfill(response=real, json=body)

    page.route("**/api/bots/*/workspace", answer)


def watch_loads(page):
    page.evaluate(
        """() => {
            const E = window.sffEditor;
            window.__loads = 0;
            window.__views = [];
            const original = E.blocks.load;
            const where = () => {
                const w = E.blocks.workspace();
                return {x: w.scrollX, y: w.scrollY, scale: w.getScale()};
            };
            E.blocks.load = function () {
                window.__loads += 1;
                const before = where();
                const result = original.apply(this, arguments);
                window.__views.push({before, after: where()});
                return result;
            };
        }"""
    )


def test_a_tidied_copy_is_not_loaded_so_the_view_does_not_jump(open_editor):
    """The server may write something a little differently (a trimmed space, an option in its usual order)."""
    page = open_editor()
    watch_loads(page)

    def tidy(body, sent):
        # different from what was sent, but nothing was removed
        body["workspace"] = {"blocks": {"languageVersion": 0, "blocks": []}, "variables": []}
        body["dropped"] = 0

    server_says(page, tidy)
    page.evaluate(ADD_A_BLOCK)
    wait_for_version(page, 1)
    assert page.evaluate("() => window.__loads") == 0
    assert sorted(top_types(page)) == sorted([*STARTER, "sff_console_print"])  # nothing was replaced
    assert_quiet(page)


def test_when_the_server_removed_blocks_the_kept_copy_is_shown_where_the_person_is_looking(open_editor):
    page = open_editor()
    watch_loads(page)
    page.evaluate(ADD_A_BLOCK)
    wait_for_version(page, 1)

    def removed(body, sent):
        body["workspace"] = sent  # the same blocks, so the view can be compared (the server says one was removed)
        body["dropped"] = 1

    server_says(page, removed)
    page.evaluate("() => window.sffEditor.blocks.workspace().scroll(-300, -200)")
    page.evaluate(ADD_A_BLOCK)
    page.wait_for_selector(".toast:has-text('removed')", timeout=8000)
    assert page.evaluate("() => window.__loads") == 1
    view = page.evaluate("() => window.__views[0]")
    # the view stayed where it was (Blockly keeps the offset inside the blocks' bounds, so it may shift a few pixels,
    # where going back to the first block would move it by hundreds)
    assert view["after"]["scale"] == view["before"]["scale"]
    assert abs(view["after"]["x"] - view["before"]["x"]) < 30
    assert abs(view["after"]["y"] - view["before"]["y"]) < 30
    assert_quiet(page)


def test_after_blocks_could_not_be_opened_adding_blocks_still_works_and_is_remembered(open_editor):
    page = open_editor()
    page.evaluate(
        "(state) => { try { window.sffEditor.blocks.load(state); } catch (error) {} }",
        {
            "blocks": {
                "languageVersion": 0,
                "blocks": [
                    {"type": "sff_console_print", "id": "a", "x": 1, "y": 1},
                    {"type": "no_such_block", "id": "x", "x": 1, "y": 1},
                ],
            },
            "variables": [],
        },
    )
    # Blockly is left as it was: not grouping changes, and remembering them for undo
    assert page.evaluate("() => window.Blockly.Events.getGroup()") == ""
    assert page.evaluate("() => window.Blockly.Events.getRecordUndo()") is True
    open_list(page, "bot")
    drag_from_list(page, "sff_console_print", 620, 520)
    wait_for_version(page, 1)
    assert "sff_console_print" in stored_types(page)
    assert page.evaluate("() => window.sffEditor.blocks.workspace().getUndoStack().length") >= 1


def test_a_block_being_carried_is_never_replaced_by_a_reload(open_editor):
    """Replacing the blocks under a block that is being dragged can leave the editor unable to pick up any more."""
    page = open_editor()
    watch_loads(page)
    page.evaluate(ADD_A_BLOCK)
    wait_for_version(page, 1)

    def removed(body, sent):
        body["workspace"] = sent
        body["dropped"] = 1  # would normally make the editor show the kept copy

    server_says(page, removed)
    open_list(page, "bot")
    page.evaluate(ADD_A_BLOCK)  # a change: it is saved a moment later, and the answer asks for a reload
    box = page.evaluate(
        """() => {
            const flyout = window.sffEditor.blocks.workspace().getToolbox().getFlyout();
            const block = flyout.getWorkspace().getTopBlocks(false).find((b) => b.type === 'sff_console_print');
            const r = block.getSvgRoot().getBoundingClientRect();
            return {x: r.left + 14, y: r.top + r.height / 2};
        }"""
    )
    page.mouse.move(box["x"], box["y"])
    page.mouse.down()
    page.mouse.move(box["x"] + 200, box["y"] + 120, steps=8)  # carrying a block out of the list
    assert page.evaluate("() => window.sffEditor.blocks.isDragging()") is True
    page.wait_for_timeout(2500)  # the save happens, and answers, while the block is still in the hand
    assert page.evaluate("() => window.__loads") == 0
    page.mouse.up()
    assert page.evaluate("() => window.sffEditor.blocks.isDragging()") is False
    page.wait_for_selector(".toast:has-text('removed')", timeout=8000)  # after it was put down, the answer is acted on
    assert page.evaluate("() => window.__loads") == 1
    open_list(page, "bot")
    drag_from_list(page, "sff_console_print", 620, 420)  # and blocks can still be added
    assert page.evaluate("() => window.sffEditor.blocks.workspace().getTopBlocks(false).length") >= 6
