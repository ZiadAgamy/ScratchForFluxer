"""Sharing in a real browser: the share dialog, the invite page, and the read-only editor a helper gets."""

from __future__ import annotations

import pytest
from test_editor_browser import WATCH_THE_POLICY, assert_quiet, open_editor, wait_until_ready  # noqa: F401  (fixture + helpers)

pytestmark = pytest.mark.browser


def test_owner_makes_a_link_and_a_helper_gets_a_read_only_editor(open_editor, new_page, make_user, live_server):
    owner = open_editor()
    owner.click("#share-btn")
    owner.wait_for_selector("#share-title")
    owner.click("text=Create link")
    owner.wait_for_selector(".share-made input")
    url = owner.input_value(".share-made input")
    assert "/invite/" in url

    _, csrf, cookies = make_user(fluxer_id="900000000000777", username="helper", global_name="Helper")
    helper = new_page(cookies=cookies)
    helper.add_init_script(WATCH_THE_POLICY)
    helper.goto(url.replace(url.split("/invite/")[0], live_server))
    helper.click("#invite-join")
    helper.wait_for_url(f"{live_server}/bots/{owner.bot_id}/edit", timeout=15000)
    wait_until_ready(helper)
    assert helper.get_attribute(".ed-shell", "data-role") == "member"
    assert helper.get_attribute(".ed-shell", "data-can-edit") == "yes"
    assert helper.locator("#share-btn").count() == 0
    assert_quiet(helper)
    assert_quiet(owner)


def test_a_view_only_helper_sees_blocks_but_cannot_change_them(open_editor, new_page, make_user, live_server):
    owner = open_editor()
    r = owner.request.post(
        f"{live_server}/api/bots/{owner.bot_id}/invites",
        data={"perms": [], "expires": "7d", "max_uses": 1},
        headers={"X-CSRF-Token": owner.csrf, "Origin": live_server},
    )
    assert r.status == 201, r.text()
    code = r.json()["url"].rsplit("/", 1)[1]
    _, csrf, cookies = make_user(fluxer_id="900000000000778", username="viewer", global_name="Viewer")
    viewer = new_page(cookies=cookies)
    viewer.add_init_script(WATCH_THE_POLICY)
    viewer.goto(f"{live_server}/invite/{code}")
    viewer.click("#invite-join")
    viewer.wait_for_url(f"{live_server}/bots/{owner.bot_id}/edit", timeout=15000)
    wait_until_ready(viewer)
    assert viewer.get_attribute(".ed-shell", "data-can-edit") == "no"
    assert viewer.evaluate("() => window.sffEditor.blocks.workspace().options.readOnly") is True
    assert_quiet(viewer)
