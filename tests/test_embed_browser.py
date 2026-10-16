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
    return json.loads(page.evaluate("() => window.sffEditor.blocks.workspace().getBlockById('emb1').getFieldValue('OPTIONS')"))


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
    assert page.evaluate("() => document.querySelector('dialog.dialog-embed .dialog-body').scrollWidth <= document.querySelector('dialog.dialog-embed .dialog-body').clientWidth")


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
    assert 'title=str(ctx.id)' in python_shown(page) or "ctx.id" in python_shown(page)
    assert_quiet(page)
