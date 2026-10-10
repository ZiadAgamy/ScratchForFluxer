"""My bots + Add a bot in a real browser (the JavaScript the API tests can't see).

Fluxer is the shared mock from conftest.py; it answers the server running inside ``live_server``.
Skipped automatically when Playwright/Chromium isn't installed.
"""

from __future__ import annotations

import pytest
from fluxer_mock import OWNER_FLUXER_ID, TOKEN, TOKEN_2, application
from sqlalchemy import select

from scratch_fluxer.db import _call_with_session
from scratch_fluxer.models import Bot, User

pytestmark = pytest.mark.browser


@pytest.fixture
def member(fluxer, new_page, live_server, make_user):
    """A logged-in page. Listing ``fluxer`` first makes sure the mock is in place before the server starts."""
    _, _, cookies = make_user(fluxer_id=OWNER_FLUXER_ID, username="agamy", global_name="AGAMY")
    page = new_page(cookies=cookies)
    page.base = live_server
    return page


def bots_in_db() -> list[Bot]:
    return _call_with_session(lambda db: list(db.execute(select(Bot).order_by(Bot.created_at)).scalars()), (), {})


def add_bot_through_the_ui(page, token=TOKEN, name=None):
    page.goto(page.base + "/bots/new")
    page.fill("#token-input", token)
    page.click("#check-btn")
    page.wait_for_selector("#state-confirm:not([hidden])")
    if name is not None:
        page.fill("#bot-name-input", name)
    page.click("#save-btn")
    page.wait_for_url("**/bots/*/edit")
    page.wait_for_selector("#ed-loading", state="hidden")  # let the editor finish starting before the test moves on


class TestEmptyList:
    def test_invites_you_to_add_the_first_bot(self, member):
        member.goto(member.base + "/bots")
        assert member.inner_text("h2:has-text('Add your first bot')")
        member.click(".empty a:has-text('Add a bot')")
        member.wait_for_url("**/bots/new")
        assert member.problems == []


class TestAddABot:
    def test_paste_check_name_save(self, member):
        member.goto(member.base + "/bots/new")
        assert member.get_attribute("#token-input", "type") == "password"  # masked while you paste
        assert member.is_disabled("#check-btn")

        member.fill("#token-input", TOKEN)
        assert member.is_enabled("#check-btn")
        member.click("#check-btn")
        member.wait_for_selector("#state-confirm:not([hidden])")
        assert member.inner_text("#bot-app-name") == "Ping Bot"
        assert member.inner_text("#bot-username") == "@pingbot"
        assert member.input_value("#bot-name-input") == "Ping Bot"
        assert TOKEN not in member.content()  # the token is never written back into the page

        member.fill("#bot-name-input", "  My   ping bot ")
        member.click("#save-btn")
        member.wait_for_url("**/bots/*/edit")
        member.wait_for_selector("#ed-loading", state="hidden")

        (bot,) = bots_in_db()
        assert bot.name == "My ping bot"  # tidied, as the API does
        assert bot.token_last4 == TOKEN[-4:]
        assert TOKEN not in bot.token_ciphertext  # stored encrypted
        assert member.problems == []

    def test_the_token_can_be_shown_while_you_check_it(self, member):
        member.goto(member.base + "/bots/new")
        member.fill("#token-input", TOKEN)
        member.click("#token-toggle")
        assert member.get_attribute("#token-input", "type") == "text"
        assert member.get_attribute("#token-toggle", "aria-pressed") == "true"
        member.click("#token-toggle")
        assert member.get_attribute("#token-input", "type") == "password"

    def test_pasting_a_token_checks_it_straight_away(self, member):
        member.goto(member.base + "/bots/new")
        member.focus("#token-input")
        member.evaluate(
            """(token) => {
                const input = document.getElementById('token-input');
                input.value = token;
                input.dispatchEvent(new Event('paste', {bubbles: true}));
            }""",
            f'"Bot {TOKEN}"',  # people copy it with quotes and the word Bot; both are tidied away
        )
        member.wait_for_selector("#state-confirm:not([hidden])")
        assert member.inner_text("#bot-app-name") == "Ping Bot"

    def test_a_token_fluxer_rejects_shows_a_plain_message(self, member):
        member.goto(member.base + "/bots/new")
        member.fill("#token-input", "999999999999999999.ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ0000")
        member.click("#check-btn")
        member.wait_for_selector("#flash:not([hidden])")
        assert "rejected this bot token" in member.inner_text("#flash")
        assert member.is_visible("#state-paste")
        assert bots_in_db() == []

    def test_something_that_is_not_a_token_is_explained_before_asking_fluxer(self, member, fluxer):
        member.goto(member.base + "/bots/new")
        member.fill("#token-input", "hello there")
        member.click("#check-btn")
        member.wait_for_selector("#flash:not([hidden])")
        assert "token" in member.inner_text("#flash").lower()
        assert fluxer.me.call_count == 0

    def test_you_can_only_add_a_bot_you_own(self, member, fluxer):
        fluxer.apps[TOKEN_2] = application("222222222222222222", "Not Mine", owner_id="123123123123123123")
        member.goto(member.base + "/bots/new")
        member.fill("#token-input", TOKEN_2)
        member.click("#check-btn")
        member.wait_for_selector("#flash:not([hidden])")
        assert "different Fluxer account" in member.inner_text("#flash")
        assert bots_in_db() == []

    def test_a_bot_that_is_already_added_points_you_to_it(self, member):
        add_bot_through_the_ui(member)
        member.goto(member.base + "/bots/new")
        member.fill("#token-input", TOKEN)
        member.click("#check-btn")
        member.wait_for_selector("#state-confirm:not([hidden])")
        assert member.is_visible("#already-box")
        assert member.is_hidden("#save-btn")
        assert member.is_hidden("#name-field")
        (bot,) = bots_in_db()
        assert member.get_attribute("#already-link", "href") == f"/bots/{bot.id}/edit"
        member.click("#retry-btn")  # "Use a different token"
        assert member.is_visible("#state-paste") and member.input_value("#token-input") == ""

    def test_the_limit_is_explained_instead_of_a_form(self, member, monkeypatch):
        monkeypatch.setenv("MAX_BOTS_PER_USER", "0")
        from scratch_fluxer import config

        config.reset_settings_cache()
        member.goto(member.base + "/bots/new")
        assert "reached the limit" in member.inner_text(".onboard-panel")
        assert member.locator("#token-input").count() == 0


class TestMyBots:
    def test_lists_bots_without_ever_showing_a_token(self, member, fluxer):
        add_bot_through_the_ui(member)
        fluxer.apps[TOKEN_2] = application("222222222222222222", "Quiz Bot", bot_username="quizbot")
        add_bot_through_the_ui(member, TOKEN_2)
        member.goto(member.base + "/bots")
        assert member.locator(".bot-row").count() == 2
        page_text = member.content()
        assert TOKEN not in page_text and TOKEN_2 not in page_text
        assert "Token ends in" in member.inner_text(".bot-row >> nth=0")
        assert member.inner_text(".list-count").startswith("2 of")

    def test_rename(self, member):
        add_bot_through_the_ui(member)
        member.goto(member.base + "/bots")
        member.click(".bot-menu summary")
        member.click("button[data-action=rename]")
        assert member.is_visible("#dlg-rename")
        assert member.input_value("#rename-input") == "Ping Bot"
        member.fill("#rename-input", "Renamed bot")
        member.click("#dlg-rename button[type=submit]")
        member.wait_for_selector("#dlg-rename:not([open])", state="attached")
        assert member.inner_text(".bot-name") == "Renamed bot"
        assert bots_in_db()[0].name == "Renamed bot"

    def test_a_blank_name_is_refused_with_a_message(self, member):
        add_bot_through_the_ui(member)
        member.goto(member.base + "/bots")
        member.click(".bot-menu summary")
        member.click("button[data-action=rename]")
        member.fill("#rename-input", "   ")
        member.click("#dlg-rename button[type=submit]")
        member.wait_for_selector("#dlg-rename .field-error:not([hidden])")
        assert member.is_visible("#dlg-rename")  # stays open so you can fix it
        assert bots_in_db()[0].name == "Ping Bot"

    def test_replace_the_token(self, member, fluxer):
        from fluxer_mock import TOKEN_1_ROTATED

        add_bot_through_the_ui(member)
        before = bots_in_db()[0].token_ciphertext
        member.goto(member.base + "/bots")
        member.click(".bot-menu summary")
        member.click("button[data-action=token]")
        member.fill("#token-replace-input", TOKEN_1_ROTATED)
        member.click("#dlg-token button[type=submit]")
        member.wait_for_selector("#dlg-token:not([open])", state="attached")
        assert member.inner_text("[data-role=last4]") == TOKEN_1_ROTATED[-4:]
        assert bots_in_db()[0].token_ciphertext != before

    def test_replacing_with_another_bots_token_is_refused(self, member):
        add_bot_through_the_ui(member)
        member.goto(member.base + "/bots")
        member.click(".bot-menu summary")
        member.click("button[data-action=token]")
        member.fill("#token-replace-input", TOKEN_2)
        member.click("#dlg-token button[type=submit]")
        member.wait_for_selector("#dlg-token .field-error:not([hidden])")
        assert "different bot" in member.inner_text("#dlg-token .field-error")

    def test_remove_asks_first_and_then_goes_back_to_the_empty_state(self, member):
        add_bot_through_the_ui(member)
        member.goto(member.base + "/bots")
        member.click(".bot-menu summary")
        member.click("button[data-action=remove]")
        assert "Ping Bot" in member.inner_text("#dlg-remove [data-role=remove-name]")
        member.click("#dlg-remove button[data-close]")  # "Keep it"
        assert len(bots_in_db()) == 1

        member.click(".bot-menu summary")
        member.click("button[data-action=remove]")
        member.click("#dlg-remove button[type=submit]")
        member.wait_for_selector("h2:has-text('Add your first bot')")
        assert bots_in_db() == []

    def test_the_menu_closes_when_you_click_elsewhere_or_press_escape(self, member):
        add_bot_through_the_ui(member)
        member.goto(member.base + "/bots")
        member.click(".bot-menu summary")
        assert member.locator(".bot-menu[open]").count() == 1
        member.click("h1")
        assert member.locator(".bot-menu[open]").count() == 0
        member.click(".bot-menu summary")
        member.keyboard.press("Escape")
        assert member.locator(".bot-menu[open]").count() == 0

    def test_deleting_your_data_needs_your_username_typed_in(self, member):
        add_bot_through_the_ui(member)
        member.goto(member.base + "/bots")
        member.click("button[data-action=delete-account]")
        member.fill("#account-confirm", "someone-else")
        member.click("#dlg-account button[type=submit]")
        member.wait_for_selector("#dlg-account .field-error:not([hidden])")
        assert len(bots_in_db()) == 1  # nothing was deleted

        member.fill("#account-confirm", "agamy")
        member.click("#dlg-account button[type=submit]")
        member.wait_for_url(member.base + "/")
        assert bots_in_db() == []
        users = _call_with_session(lambda db: list(db.execute(select(User)).scalars()), (), {})
        assert users == []
        assert "Log in with Fluxer" in member.content()  # logged out as well

    def test_every_dialog_can_be_dismissed_from_the_keyboard(self, member):
        add_bot_through_the_ui(member)
        member.goto(member.base + "/bots")
        for opener in (
            ("summary", None, "button[data-action=rename]", "#dlg-rename"),
            ("summary", None, "button[data-action=token]", "#dlg-token"),
            ("summary", None, "button[data-action=remove]", "#dlg-remove"),
        ):
            member.click(".bot-menu summary")
            member.click(opener[2])
            assert member.is_visible(opener[3])
            member.keyboard.press("Escape")
            assert not member.is_visible(opener[3])
        member.click("button[data-action=delete-account]")
        member.keyboard.press("Escape")
        assert not member.is_visible("#dlg-account")
        assert member.problems == []

    def test_other_peoples_bots_are_never_listed(self, member, make_user):
        add_bot_through_the_ui(member)
        make_user(fluxer_id="777000000000007", username="someone", global_name="Someone")
        user_id = _call_with_session(
            lambda db: db.execute(select(User.id).where(User.username == "someone")).scalar_one(), (), {}
        )

        def insert(db):
            db.add(
                Bot(
                    user_id=user_id,
                    name="Somebody else's secret bot",
                    token_ciphertext="x",
                    token_last4="0000",
                    bot_user_id="555",
                    bot_username="secret",
                )
            )

        _call_with_session(insert, (), {})
        member.goto(member.base + "/bots")
        assert "Somebody else" not in member.content()
        assert member.locator(".bot-row").count() == 1


class TestLayout:
    @pytest.mark.parametrize("width", [320, 390, 820, 1280])
    def test_no_sideways_scrolling(self, member, width):
        add_bot_through_the_ui(member, name="A bot with a really quite long name, to see how the row copes")
        member.set_viewport_size({"width": width, "height": 900})
        for path in ("/bots", "/bots/new"):
            member.goto(member.base + path)
            assert member.evaluate("() => document.documentElement.scrollWidth") <= width, path
        assert member.problems == []
