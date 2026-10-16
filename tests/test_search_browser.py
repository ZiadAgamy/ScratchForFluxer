"""Finding blocks by typing: the toolbar search and the search over each family's block list."""

from __future__ import annotations

import pytest
from blocks import workspace
from playwright.sync_api import expect
from test_editor_browser import assert_quiet, open_editor  # noqa: F401 - open_editor is a fixture

pytestmark = pytest.mark.browser


def block_types(page) -> list[str]:
    return page.evaluate("() => window.sffEditor.blocks.workspace().getAllBlocks(false).map(b => b.type)")


def open_family(page, name: str) -> None:
    page.click(f".blocklyToolbox .blocklyToolboxCategory:has-text('{name}')")
    expect(page.locator("#ed-flyout-search")).to_be_visible()


def test_typing_in_the_toolbar_lists_matching_blocks(open_editor):
    page = open_editor(state=workspace())
    page.fill("#ed-search-input", "ban")
    rows = page.locator("#ed-search-list .ed-search-row")
    expect(rows.first).to_be_visible()
    assert rows.count() >= 1
    assert any("ban" in t.lower() for t in rows.all_inner_texts())
    assert_quiet(page)


def test_enter_adds_the_top_result_to_the_workspace(open_editor):
    page = open_editor(state=workspace())
    page.fill("#ed-search-input", "ban")
    wanted = page.locator("#ed-search-list .ed-search-row").first.get_attribute("data-type")
    page.keyboard.press("Enter")
    assert wanted in block_types(page)
    expect(page.locator("#ed-search-list")).to_be_hidden()
    assert page.input_value("#ed-search-input") == ""
    assert_quiet(page)


def test_clicking_a_result_and_arrow_keys(open_editor):
    page = open_editor(state=workspace())
    page.fill("#ed-search-input", "send")
    rows = page.locator("#ed-search-list .ed-search-row")
    expect(rows.nth(1)).to_be_visible()
    page.keyboard.press("ArrowDown")
    assert "is-active" in rows.nth(1).get_attribute("class")
    chosen = rows.nth(1).get_attribute("data-type")
    rows.nth(1).click()
    assert chosen in block_types(page)
    assert_quiet(page)


def test_a_query_with_no_match_says_so(open_editor):
    page = open_editor(state=workspace())
    before = block_types(page)
    page.fill("#ed-search-input", "zzzqqq")
    expect(page.locator("#ed-search-list .ed-search-none")).to_be_visible()
    page.keyboard.press("Enter")
    assert block_types(page) == before
    assert_quiet(page)


def test_slash_focuses_the_search_and_escape_leaves(open_editor):
    page = open_editor(state=workspace())
    page.locator("#blockly").click(position={"x": 400, "y": 300})
    page.keyboard.press("/")
    expect(page.locator("#ed-search-input")).to_be_focused()
    page.keyboard.type("role")
    page.keyboard.press("Escape")
    expect(page.locator("#ed-search-input")).not_to_be_focused()
    assert page.input_value("#ed-search-input") == ""
    assert_quiet(page)


def test_a_family_has_its_own_search_that_narrows_its_blocks(open_editor):
    page = open_editor(state=workspace())
    open_family(page, "Messages")
    count = lambda: page.evaluate("() => window.sffEditor.blocks.flyoutShape().count")  # noqa: E731
    before = count()
    page.fill("#ed-flyout-input", "reaction")
    page.wait_for_function(f"() => window.sffEditor.blocks.flyoutShape().count < {before}")
    assert 1 <= count() < before
    page.fill("#ed-flyout-input", "")
    page.wait_for_function(f"() => window.sffEditor.blocks.flyoutShape().count === {before}")
    assert_quiet(page)


def test_the_family_search_overlays_the_top_of_the_list_and_keeps_focus(open_editor):
    page = open_editor(state=workspace())
    open_family(page, "Messages")
    page.click("#ed-flyout-input")
    page.keyboard.type("send")
    expect(page.locator("#ed-flyout-input")).to_be_focused()
    box = page.locator("#ed-flyout-search").bounding_box()
    shape = page.evaluate("() => window.sffEditor.blocks.flyoutShape()")
    assert abs(box["x"] - shape["left"]) < 3 and box["width"] <= shape["width"] + 3
    first = page.evaluate(
        "() => document.querySelector('.blocklyToolboxFlyout .blocklyDraggable').getBoundingClientRect().top"
    )
    assert first >= box["y"] + box["height"] - 2, "the first block must sit below the search box"
    assert_quiet(page)


def test_closing_a_family_clears_its_search(open_editor):
    page = open_editor(state=workspace())
    open_family(page, "Messages")
    page.fill("#ed-flyout-input", "reaction")
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    expect(page.locator("#ed-flyout-search")).to_be_hidden()
    open_family(page, "Messages")
    assert page.input_value("#ed-flyout-input") == ""
    assert_quiet(page)


def test_a_family_search_with_no_match_shows_a_note(open_editor):
    page = open_editor(state=workspace())
    open_family(page, "Messages")
    page.fill("#ed-flyout-input", "zzzqqq")
    expect(page.locator(".blocklyToolboxFlyout .blocklyFlyoutLabelText:has-text('No block matches')")).to_be_visible()
    assert_quiet(page)


def test_the_search_fits_a_phone_and_does_not_scroll_the_page(open_editor):
    page = open_editor(viewport={"width": 360, "height": 740}, state=workspace())
    page.fill("#ed-search-input", "send")
    expect(page.locator("#ed-search-list .ed-search-row").first).to_be_visible()
    box = page.locator("#ed-search").bounding_box()
    listing = page.locator("#ed-search-list").bounding_box()
    rail = page.locator(".blocklyToolbox").bounding_box()
    edge = rail["x"] + rail["width"]
    assert box["x"] >= edge - 1 and box["x"] + box["width"] <= 360
    assert listing["x"] >= edge - 1 and listing["x"] + listing["width"] <= 360
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path="/tmp/phone.png")
    assert_quiet(page)
