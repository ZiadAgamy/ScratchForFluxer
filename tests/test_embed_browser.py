"""The embed blocks' pop-up in a real browser: it opens from the block, changes the blocks and the Python they write."""

from __future__ import annotations

import json

import pytest
from blocks import block, command, workspace
from playwright.sync_api import expect
from test_editor_browser import (  # noqa: F401 - open_editor is a fixture
    assert_quiet,
    field_centre,
    open_editor,
    python_shown,
    wait_for_version,
)

pytestmark = pytest.mark.browser


def with_embed(kind: str = "sff_reply_embed", **fields):
    return workspace(
        command(
            "card",
            block(kind, id="emb1", fields=fields or None, inputs={"TITLE": "Rules", "TEXT": "Be kind."}),
        )
    )


def press_card_options(page) -> None:
    x, y = field_centre(page, "emb1", "OPTIONS")
    page.mouse.click(x, y)
    expect(page.locator("dialog.dialog-embed")).to_be_visible()


def options_of(page) -> dict:
    return json.loads(
        page.evaluate("() => window.sffEditor.blocks.workspace().getBlockById('emb1').getFieldValue('OPTIONS')")
    )


def test_the_block_has_a_card_options_button_and_no_colour_menu(open_editor):
    page = open_editor(state=with_embed())
    assert page.evaluate("() => !!window.sffEditor.blocks.workspace().getBlockById('emb1').getField('OPTIONS')")
    assert page.evaluate("() => !window.sffEditor.blocks.workspace().getBlockById('emb1').getField('COLOR')")
    assert_quiet(page)


def test_changing_the_colour_and_title_in_the_pop_up_changes_the_python(open_editor):
    page = open_editor(state=with_embed())
    press_card_options(page)
    page.fill("#" + page.locator("dialog.dialog-embed input[id$='-title']").get_attribute("id"), "Server rules")
    page.click("dialog.dialog-embed .embed-swatch[data-name='red']")
    expect(page.locator(".embed-preview-card .embed-title")).to_have_text("Server rules")
    page.click("dialog.dialog-embed button[type=submit]")
    expect(page.locator("dialog.dialog-embed")).to_have_count(0)
    assert options_of(page) == {"color": "red"}
    wait_for_version(page, 1)
    code = python_shown(page)
    assert 'title="Server rules"' in code and "color=0xF2735A" in code
    assert_quiet(page)


def test_a_custom_colour_link_pictures_author_footer_and_time(open_editor):
    page = open_editor(state=with_embed())
    press_card_options(page)
    dialog = page.locator("dialog.dialog-embed")
    dialog.locator(".embed-hex").fill("ff8800")
    dialog.locator("summary", has_text="Link, pictures").click()
    dialog.locator("input[id$='-url']").fill("https://example.com/rules")
    dialog.locator("select[id$='-image-source']").select_option("__link__")
    dialog.locator("input[id$='-image']").fill("https://example.com/banner.png")
    dialog.locator("select[id$='-thumbnail-source']").select_option("__link__")
    dialog.locator("input[id$='-thumbnail']").fill("https://example.com/small.png")
    dialog.locator("summary", has_text="Author and footer").click()
    dialog.locator("input[id$='-author']").fill("The team")
    dialog.locator("input[id$='-footer']").fill("Read them twice")
    dialog.locator("input[id$='-time']").check()
    dialog.locator("button[type=submit]").click()
    assert options_of(page) == {
        "author": "The team",
        "color": "#ff8800",
        "footer": "Read them twice",
        "image": "https://example.com/banner.png",
        "thumbnail": "https://example.com/small.png",
        "time": True,
        "url": "https://example.com/rules",
    }
    wait_for_version(page, 1)
    code = python_shown(page)
    for line in (
        "color=0xFF8800",
        'url="https://example.com/rules"',
        'embed.set_image(url="https://example.com/banner.png")',
        'embed.set_thumbnail(url="https://example.com/small.png")',
        'embed.set_author(name="The team")',
        'embed.set_footer(text="Read them twice")',
        "timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat()",
    ):
        assert line in code
    assert_quiet(page)


def test_a_bad_address_is_refused_and_done_waits(open_editor):
    page = open_editor(state=with_embed())
    press_card_options(page)
    dialog = page.locator("dialog.dialog-embed")
    dialog.locator("summary", has_text="Link, pictures").click()
    dialog.locator("input[id$='-url']").fill("javascript:alert(1)")
    expect(dialog.locator(".field-error:visible")).to_have_count(1)
    expect(dialog.locator("button[type=submit]")).to_be_disabled()
    dialog.locator("input[id$='-url']").fill("https://example.com")
    expect(dialog.locator("button[type=submit]")).to_be_enabled()


def test_cancel_changes_nothing(open_editor):
    page = open_editor(state=with_embed())
    press_card_options(page)
    page.click("dialog.dialog-embed .embed-swatch[data-name='green']")
    page.get_by_role("button", name="Cancel").click()
    assert options_of(page) == {}


def test_an_old_block_with_a_colour_menu_keeps_its_colour(open_editor):
    page = open_editor(state=with_embed(COLOR="purple"))
    assert options_of(page) == {"color": "purple"}
    assert "color=0xA98BFF" in python_shown(page)


def test_a_block_in_the_title_socket_is_left_alone(open_editor):
    state = workspace(
        command(
            "card",
            block(
                "sff_reply_embed",
                id="emb1",
                inputs={"TITLE": block("sff_msg_author_name"), "TEXT": "Be kind."},
            ),
        )
    )
    page = open_editor(state=state)
    press_card_options(page)
    expect(page.locator("dialog.dialog-embed .embed-note")).to_be_visible()
    page.get_by_role("button", name="Cancel").click()


def test_the_webhook_block_offers_web_addresses_only(open_editor):
    page = open_editor(state=with_embed("sff_webhook_embed"))
    press_card_options(page)
    dialog = page.locator("dialog.dialog-embed")
    dialog.locator("summary", has_text="Link, pictures").click()
    options = dialog.locator("select[id$='-image-source'] option").all_inner_texts()
    assert options == ["None", "A web address…"]


def test_it_fits_a_phone(open_editor):
    page = open_editor(state=with_embed(), viewport={"width": 390, "height": 800})
    page.evaluate("() => window.sffEditor.blocks.workspace().getBlockById('emb1').getField('OPTIONS').showEditor_()")
    expect(page.locator("dialog.dialog-embed")).to_be_visible()
    box = page.locator("dialog.dialog-embed").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert page.evaluate(
        "() => document.querySelector('dialog.dialog-embed .dialog-body').scrollWidth <= document.querySelector('dialog.dialog-embed .dialog-body').clientWidth"
    )


def test_a_codename_can_be_inserted_from_the_pop_up(open_editor):
    page = open_editor(state=with_embed())
    press_card_options(page)
    dialog = page.locator("dialog.dialog-embed")
    dialog.locator("summary", has_text="Author and footer").click()
    author = dialog.locator("input[id$='-author']")
    author.fill("By ")
    dialog.locator("select[id$='-author-codename']").select_option("$authorName")
    expect(author).to_have_value("By $authorName")
    dialog.locator("select[id$='-author-codename']").select_option("$authorID")
    expect(author).to_have_value("By $authorName$authorID")
    # the title and the text have one too, and what is inserted goes into the block's own text box
    dialog.locator("input[id$='-title']").fill("")
    dialog.locator("select[id$='-title-codename']").select_option("$messageID")
    expect(dialog.locator("input[id$='-title']")).to_have_value("$messageID")
    dialog.locator("button[type=submit]").click()
    assert options_of(page)["author"] == "By $authorName$authorID"
    wait_for_version(page, 1)
    assert "title=str(ctx.id)" in python_shown(page) or "ctx.id" in python_shown(page)
    assert_quiet(page)


def channels(dialog):
    return [int(dialog.locator(".cp-num").nth(i).input_value()) for i in range(3)]


def open_picker(page):
    press_card_options(page)
    dialog = page.locator("dialog.dialog-embed")
    dialog.locator("button.embed-picker").click()
    expect(dialog.locator(".cp")).to_be_visible()
    return dialog


def test_the_colour_picker_opens_and_is_not_the_browsers_own(open_editor):
    page = open_editor(state=with_embed())
    dialog = open_picker(page)
    assert dialog.locator("input[type=color]").count() == 0
    for part in (".cp-square", ".cp-hue", ".cp-num", ".cp-hex"):
        assert dialog.locator(part).count() >= 1
    assert dialog.locator(".cp-num").count() == 3
    dialog.locator("button.embed-picker").click()
    expect(dialog.locator(".cp")).to_be_hidden()


def test_dragging_in_the_square_changes_the_colour(open_editor):
    page = open_editor(state=with_embed())
    dialog = open_picker(page)
    dialog.locator(".cp-square").scroll_into_view_if_needed()
    box = dialog.locator(".cp-square").bounding_box()
    dialog.locator(".cp-hue").fill("0")  # red
    page.mouse.click(box["x"] + box["width"] - 2, box["y"] + 14)  # near the strongest, brightest corner (it is rounded)
    r, g, b = channels(dialog)
    assert r > 200 and g < 20 and b < 20
    box = dialog.locator(".cp-square").bounding_box()  # the dialog may have scrolled
    page.mouse.click(box["x"] + 2, box["y"] + 14)  # near the white corner
    r, g, b = channels(dialog)
    assert min(r, g, b) > 200
    dialog.locator("button[type=submit]").click()
    assert options_of(page)["color"].startswith("#f") or options_of(page)["color"].startswith("#e")


def test_numbers_and_the_code_change_the_colour_and_back(open_editor):
    page = open_editor(state=with_embed())
    dialog = open_picker(page)
    dialog.locator(".cp-num").nth(0).fill("255")
    dialog.locator(".cp-num").nth(1).fill("136")
    dialog.locator(".cp-num").nth(2).fill("0")
    expect(dialog.locator(".cp-hex")).to_have_value("#ff8800")
    expect(dialog.locator(".embed-hex")).to_have_value("#ff8800")
    dialog.locator(".cp-hex").fill("#00ff88")
    expect(dialog.locator(".cp-num").nth(1)).to_have_value("255")
    dialog.locator(".embed-hex").fill("#112233")
    expect(dialog.locator(".cp-hex")).to_have_value("#112233")
    dialog.locator("button[type=submit]").click()
    assert options_of(page) == {"color": "#112233"}


def test_the_square_works_with_the_keyboard(open_editor):
    page = open_editor(state=with_embed())
    dialog = open_picker(page)
    dialog.locator(".cp-hue").fill("0")
    dialog.locator(".cp-hex").fill("#ffffff")
    square = dialog.locator(".cp-square")
    square.focus()
    for _ in range(10):
        page.keyboard.press("ArrowRight")  # more colour, still bright
    expect(dialog.locator(".cp-hex")).not_to_have_value("#ffffff")
    page.keyboard.press("Shift+ArrowDown")
    value = dialog.locator(".cp-hex").input_value()
    assert value.startswith("#") and value != "#ffffff"


def test_the_last_colours_are_kept(open_editor):
    page = open_editor(state=with_embed())
    dialog = open_picker(page)
    dialog.locator(".cp-hex").fill("#abcdef")
    dialog.locator(".cp-hex").press("Tab")  # the person settled on it
    dialog.locator("button.embed-picker").click()  # close and open again: the row shows the last colours
    dialog.locator("button.embed-picker").click()
    swatch = dialog.locator(".cp-recent-swatch").first
    expect(swatch).to_have_attribute("title", "#abcdef")
    dialog.locator(".cp-hex").fill("#123456")
    swatch.click()
    expect(dialog.locator(".cp-hex")).to_have_value("#abcdef")


def test_the_preview_follows_the_picker(open_editor):
    page = open_editor(state=with_embed())
    dialog = open_picker(page)
    dialog.locator(".cp-hex").fill("#00ff88")
    colour = page.evaluate("() => getComputedStyle(document.querySelector('.embed-card')).borderLeftColor")
    assert colour == "rgb(0, 255, 136)"
