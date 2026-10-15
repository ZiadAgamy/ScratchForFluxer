"""The guide in a real browser: the search box, the menu on a phone, copy buttons and links that open an answer."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser

PHONE = {"width": 390, "height": 800}


@pytest.fixture
def open_docs(new_page, live_server):
    def _open(viewport=None, path="/docs"):
        page = new_page(viewport=viewport)
        page.goto(f"{live_server}{path}")
        page.wait_for_load_state("networkidle")
        return page

    return _open


def test_it_loads_without_a_single_error(open_docs):
    page = open_docs()
    assert page.problems == []
    expect(page.locator("h1")).to_have_text("Build a Fluxer bot with blocks")


class TestSearch:
    def test_typing_narrows_the_blocks_to_what_matches(self, open_docs):
        page = open_docs()
        total = page.locator(".block-item").count()
        page.fill("#docs-search-input", "embed")
        shown = page.locator(".block-item:visible").count()
        assert 0 < shown < total
        assert page.locator(".family:visible").count() < 9

    def test_a_family_with_no_match_disappears_from_the_menu_too(self, open_docs):
        page = open_docs()
        page.fill("#docs-search-input", "ban the person")
        expect(page.locator('#docs-nav a[data-family="lists"]')).to_be_hidden()
        expect(page.locator('#docs-nav a[data-family="messages"]')).to_be_visible()

    def test_nothing_found_says_so_and_clearing_brings_everything_back(self, open_docs):
        page = open_docs()
        total = page.locator(".block-item").count()
        page.fill("#docs-search-input", "zzzzqqq")
        expect(page.locator("#docs-no-results")).to_be_visible()
        assert page.locator(".block-item:visible").count() == 0
        page.fill("#docs-search-input", "")
        expect(page.locator("#docs-no-results")).to_be_hidden()
        assert page.locator(".block-item:visible").count() == total


def test_the_menu_marks_where_you_are(open_docs):
    page = open_docs()
    page.locator("#run").scroll_into_view_if_needed()
    page.evaluate("document.getElementById('run').scrollIntoView()")
    expect(page.locator("#docs-nav a[aria-current=true]")).to_have_attribute("href", "#run")


def test_every_code_box_can_be_copied(open_docs, new_page, live_server):
    page = open_docs()
    boxes = page.locator(".codebox")
    assert boxes.count() >= 5
    assert page.locator(".codebox .copy-btn").count() == boxes.count()
    first = page.locator(".codebox").first
    assert first.locator("code").inner_text().strip()


def test_a_link_to_an_answer_opens_it(open_docs):
    page = open_docs(path="/docs#trouble-roles")
    expect(page.locator("#trouble-roles")).to_have_attribute("open", "")


class TestOnAPhone:
    def test_nothing_sticks_out_sideways(self, open_docs):
        page = open_docs(viewport=PHONE)
        assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
        wide = page.evaluate(
            """() => [...document.querySelectorAll('.docs-main *')]
                 .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1 && !el.closest('pre, .token-line'))
                 .map((el) => el.tagName + '.' + el.className).slice(0, 5)"""
        )
        assert wide == []

    def test_the_side_menu_becomes_a_drop_down(self, open_docs):
        page = open_docs(viewport=PHONE)
        expect(page.locator("#docs-nav")).to_be_hidden()
        page.click(".docs-toc-mobile summary")
        expect(page.locator('.docs-toc-body a[href="#run"]')).to_be_visible()
        page.click('.docs-toc-body a[href="#run"]')
        expect(page.locator(".docs-toc-mobile")).not_to_have_attribute("open", "")
