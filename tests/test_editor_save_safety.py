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
    open_editor,  # noqa: F401 - open_editor is a fixture
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
        body["workspace"] = UNOPENABLE  # what the server says it kept
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
