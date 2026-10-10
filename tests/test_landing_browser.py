"""The landing page in a real browser: the live demo, the block explorer, and layout at phone sizes.

Skipped automatically when Playwright/Chromium isn't installed (see conftest.py).
"""

from __future__ import annotations

import pytest

from scratch_fluxer.compiler import clean_command_name, compile_workspace, default_workspace

pytestmark = pytest.mark.browser

RUN = "[data-act=run]"
RESTART = "[data-act=restart]"
STOP = "[data-act=stop]"
STATUS = "[data-role=status]"
ONLINE = "[data-role=status][data-status=online]"
OFFLINE = "[data-role=status][data-status=stopped]"
SEND = ".chat-form [type=submit]"


def open_landing(new_page, base, **options):
    page = new_page(**options)
    page.goto(base + "/")
    page.wait_for_function("() => { const b = document.querySelector('[data-act=run]'); return b && !b.disabled; }")
    return page


def code_lines(page) -> list[str]:
    return page.eval_on_selector_all("#demo .ln", "els => els.map(e => e.textContent)")


def console_lines(page) -> list[str]:
    return page.eval_on_selector_all("#demo .log span", "els => els.map(e => e.textContent)")


def bot_replies(page) -> list[str]:
    return page.eval_on_selector_all("#demo .msg-bot .msg-text", "els => els.map(e => e.textContent)")


def say(page, text: str) -> None:
    page.fill("[name=message]", text)
    page.click(SEND)


class TestPythonPane:
    def test_the_script_rewrites_the_same_markup_the_server_sent(self, new_page, live_server):
        """If these drift, the code pane would flicker (or change colour) the first time someone edits a block."""
        page = open_landing(new_page, live_server)
        before = page.eval_on_selector_all("#demo .ln", "els => els.map(e => e.outerHTML)")
        page.evaluate(
            "() => document.querySelector('[data-field=reply]').dispatchEvent(new Event('input', {bubbles: true}))"
        )
        after = page.eval_on_selector_all("#demo .ln", "els => els.map(e => e.outerHTML)")
        assert before == after
        assert page.problems == []

    def test_editing_a_block_rewrites_the_python(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        page.fill("[data-field=reply]", 'Hello "world"')
        page.fill("[data-field=ready]", "Back\\slash")
        lines = code_lines(page)
        assert '    await ctx.reply("Hello \\"world\\"")' in lines
        assert '    print("Back\\\\slash")' in lines
        assert page.problems == []

    @pytest.mark.parametrize(
        "typed",
        [
            "my cmd!",
            "class",
            "1up",
            "_hidden",
            "on_message",
            "bot",
            "print",
            "exit",
            "help",
            "id",
            "ctx",
            "fluxer",
            "None",
            "match",
            "hello world",
            "my-command",
            "  spaced    out  ",
            "UPPER",
            "مرحبا",
            "héllo",
            "mañana",
            "ａｂｃ",
            "x" * 24,
        ],
    )
    def test_command_names_become_the_python_the_editor_would_write(self, new_page, live_server, typed):
        """The demo is a small model of the editor, so it has to name things the way the compiler does."""
        page = open_landing(new_page, live_server)
        page.fill("[data-field=command]", typed)
        page.press("[data-field=command]", "Enter")  # leaving the box tidies what it shows
        shown = page.input_value("[data-field=command]")
        assert shown == clean_command_name(typed)
        real = compile_workspace(default_workspace(command=shown), assets={}).code.splitlines()
        at = next(i for i, line in enumerate(real) if line.startswith("@bot.command"))
        lines = code_lines(page)
        assert lines[at : at + 2] == real[at : at + 2]  # the decorator and the function, exactly
        assert page.problems == []

    def test_the_whole_file_matches_the_compilers_for_other_edits_too(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        page.fill("[data-field=command]", "say hi")
        page.fill("[data-field=ready]", 'Online "now" \\ ok')
        page.fill("[data-field=reply]", "Line one & <two>")
        page.press("[data-field=command]", "Enter")
        real = default_workspace(command="say hi", ready='Online "now" \\ ok', reply="Line one & <two>")
        assert "\n".join(code_lines(page)) + "\n" == compile_workspace(real, assets={}).code

    def test_a_space_can_be_typed_in_the_command_box(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        page.fill("[data-field=command]", "")
        page.type("[data-field=command]", "say hi")
        assert page.input_value("[data-field=command]") == "say hi"
        assert '@bot.command(name="say hi")' in code_lines(page)
        assert "async def say_hi(ctx):" in code_lines(page)

    def test_an_empty_command_name_is_put_back(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        page.fill("[data-field=command]", "")
        assert "async def ping(ctx):" in code_lines(page)  # the code never shows a nameless command
        page.press("[data-field=command]", "Enter")  # leaving the box
        assert page.input_value("[data-field=command]") == "ping"

    def test_typed_text_is_shown_as_text_never_as_markup(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        page.fill("[data-field=ready]", "<img src=x onerror=alert(1)>")
        page.click(RUN)
        page.wait_for_selector(ONLINE)
        assert page.locator("#demo img").count() == 0
        assert "<img src=x onerror=alert(1)>" in console_lines(page)
        assert any("<img src=x onerror=alert(1)>" in line for line in code_lines(page))
        assert page.problems == []

    def test_pointing_at_a_block_lights_up_its_lines(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        page.hover("[data-block=reply].blk")
        assert page.eval_on_selector_all("#demo .ln.is-linked", "els => els.map(e => e.textContent)") == [
            '    await ctx.reply("Pong!")'
        ]
        page.hover("#demo .ln[data-block=ready]")
        assert page.locator(".blk.is-linked").get_attribute("data-block") == "ready"
        page.mouse.move(5, 5)
        assert page.locator("#demo .is-linked").count() == 0


class TestRunningTheBot:
    def test_run_starts_the_bot_and_the_console_says_so(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        assert page.is_enabled(RUN) and page.is_disabled(STOP) and page.is_disabled(RESTART)
        page.click(RUN)
        page.wait_for_selector(ONLINE)
        assert console_lines(page) == ["Starting your bot…", "Bot is online!"]
        assert page.is_disabled(RUN) and page.is_enabled(STOP) and page.is_enabled(RESTART)
        assert page.inner_text(STATUS).strip() == "Online"

    def test_the_bot_answers_commands_the_way_fluxer_py_matches_them(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        page.click(RUN)
        page.wait_for_selector(ONLINE)
        say(page, "!ping")
        page.wait_for_function("() => document.querySelectorAll('#demo .msg-bot .msg-text').length === 1")
        assert bot_replies(page) == ["Pong!"]
        # fluxer.py runs a command when the text after the prefix *starts with* its name
        say(page, "!pingpong")
        page.wait_for_function("() => document.querySelectorAll('#demo .msg-bot .msg-text').length === 2")
        # no prefix, or a different command: no answer
        say(page, "ping")
        say(page, "!pong")
        page.wait_for_timeout(1000)
        assert bot_replies(page) == ["Pong!", "Pong!"]

    def test_edits_only_take_effect_after_a_restart(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        page.click(RUN)
        page.wait_for_selector(ONLINE)
        assert not page.is_visible("[data-role=hint]")

        page.fill("[data-field=reply]", "Hi there")
        assert page.is_visible("[data-role=hint]")
        assert "is-hint" in page.get_attribute(RESTART, "class")
        say(page, "!ping")
        page.wait_for_function("() => document.querySelectorAll('#demo .msg-bot .msg-text').length === 1")
        assert bot_replies(page) == ["Pong!"]  # still the version that was started

        page.click(RESTART)
        page.wait_for_selector(ONLINE)
        assert not page.is_visible("[data-role=hint]")
        assert console_lines(page)[-3:] == ["Your bot stopped.", "Starting your bot…", "Bot is online!"]
        say(page, "!ping")
        page.wait_for_function("() => document.querySelectorAll('#demo .msg-bot .msg-text').length === 2")
        assert bot_replies(page) == ["Pong!", "Hi there"]

    def test_renaming_the_command_moves_the_chat_box_with_it(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        page.fill("[data-field=command]", "hello")
        assert page.input_value("[name=message]") == "!hello"
        page.click(RUN)
        page.wait_for_selector(ONLINE)
        say(page, "!hello")
        page.wait_for_function("() => document.querySelectorAll('#demo .msg-bot .msg-text').length === 1")

    def test_stop_takes_the_bot_offline(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        page.click(RUN)
        page.wait_for_selector(ONLINE)
        page.click(STOP)
        page.wait_for_selector(OFFLINE)
        assert console_lines(page)[-1] == "Your bot stopped."
        say(page, "!ping")
        assert "offline" in page.inner_text(".chat-note")
        assert bot_replies(page) == []
        assert page.is_enabled(RUN)

    def test_stopping_while_it_is_still_starting_never_brings_it_online(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        page.click(RUN)
        page.click(STOP)
        page.wait_for_timeout(1300)
        assert page.get_attribute(STATUS, "data-status") == "stopped"
        assert "Bot is online!" not in console_lines(page)

    def test_no_script_errors_through_all_of_that(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        for _ in range(2):
            page.click(RUN)
            page.wait_for_selector(ONLINE)
            say(page, "!ping")
            page.click(RESTART)
            page.wait_for_selector(ONLINE)
            page.click(STOP)
            page.wait_for_selector(OFFLINE)
        assert page.problems == []


class TestAutoplay:
    def test_it_runs_itself_once_the_demo_is_in_view(self, new_page, live_server):
        page = new_page()
        page.goto(live_server + "/")
        page.wait_for_selector(ONLINE, timeout=8000)
        page.wait_for_function("() => document.querySelectorAll('#demo .msg-bot .msg-text').length === 1", timeout=8000)
        assert console_lines(page) == ["Starting your bot…", "Bot is online!"]
        assert bot_replies(page) == ["Pong!"]
        assert page.problems == []

    def test_the_first_touch_cancels_it(self, new_page, live_server):
        page = new_page()
        page.goto(live_server + "/")
        page.wait_for_selector(f"{RUN}:not([disabled])")
        page.click("[data-field=reply]")  # someone is already playing with it
        page.wait_for_timeout(3500)
        assert page.get_attribute(STATUS, "data-status") == "stopped"
        assert console_lines(page) == []

    def test_reduced_motion_skips_the_show_and_lands_on_the_result(self, new_page, live_server):
        page = new_page(reduced_motion="reduce")
        page.goto(live_server + "/")
        page.wait_for_selector(ONLINE, timeout=4000)
        page.wait_for_function("() => document.querySelectorAll('#demo .msg-bot .msg-text').length === 1", timeout=4000)
        assert page.eval_on_selector("#demo .blk", "e => getComputedStyle(e).animationName") == "none"


class TestWithoutJavaScript:
    def test_the_page_is_still_a_readable_picture(self, new_page, live_server):
        page = new_page(java_script_enabled=False)
        page.goto(live_server + "/")
        assert len(code_lines(page)) == 21
        assert page.is_disabled(RUN)
        assert page.locator(".blk").count() > 5
        # the explorer shows every family one after the other instead of hiding behind tabs
        assert page.locator(".cat-panel:visible").count() == 9
        assert not page.locator(".tabs").is_visible()


class TestBlockExplorer:
    def test_choosing_a_family_shows_only_its_blocks(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        assert page.locator(".cat-panel:visible").count() == 1
        assert page.is_visible("#cat-events")
        page.click(".tab:has-text('Bot')")
        assert page.is_visible("#cat-bot") and not page.is_visible("#cat-events")
        assert "set custom status" in page.inner_text("#cat-bot")
        assert page.locator(".cat-panel:visible").count() == 1

    def test_tabs_work_with_the_keyboard(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        assert page.get_attribute("[role=tablist]", "role") == "tablist"
        page.focus(".tab >> nth=0")
        page.keyboard.press("ArrowDown")
        assert page.get_attribute(".tab >> nth=1", "aria-selected") == "true"
        assert page.evaluate("() => document.activeElement.textContent.trim()") == "Bot"
        page.keyboard.press("End")
        assert page.get_attribute(".tab >> nth=8", "aria-selected") == "true"
        page.keyboard.press("ArrowDown")  # wraps around
        assert page.get_attribute(".tab >> nth=0", "aria-selected") == "true"
        page.keyboard.press("Home")
        assert page.is_visible("#cat-events")

    def test_tabs_and_panels_are_wired_together_for_screen_readers(self, new_page, live_server):
        page = open_landing(new_page, live_server)
        pairs = page.eval_on_selector_all(
            ".tab", "tabs => tabs.map(t => [t.getAttribute('role'), t.getAttribute('aria-controls'), t.id])"
        )
        assert len(pairs) == 9
        for role, controls, tab_id in pairs:
            assert role == "tab"
            assert page.get_attribute(f"#{controls}", "role") == "tabpanel"
            assert page.get_attribute(f"#{controls}", "aria-labelledby") == tab_id
        assert page.locator(".tab[tabindex='0']").count() == 1  # only the selected tab is in the tab order


class TestLayout:
    @pytest.mark.parametrize("width", [320, 360, 390, 600, 820, 1024, 1280, 1600])
    def test_nothing_overflows_sideways(self, new_page, live_server, width):
        page = new_page(viewport={"width": width, "height": 900})
        page.goto(live_server + "/")
        page.wait_for_selector(".demo")
        page.wait_for_timeout(300)
        scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
        assert scroll_width <= width, f"the page is {scroll_width}px wide in a {width}px window"
        assert page.problems == []

    def test_the_demo_is_on_screen_on_a_laptop(self, new_page, live_server):
        page = new_page(viewport={"width": 1366, "height": 768})
        page.goto(live_server + "/")
        box = page.locator("#demo").bounding_box()
        assert box["y"] < 200 and box["y"] + 300 < 768  # the blocks and the Run button show without scrolling

    def test_block_fields_are_as_wide_as_their_text(self, new_page, live_server):
        """A field must not keep the browser's default input width (that pushed 'to the console' onto a second line)."""
        page = open_landing(new_page, live_server)
        width = page.locator("[data-field=ready]").locator("xpath=..").bounding_box()["width"]
        assert width < 140
        page.fill("[data-field=ready]", "x" * 40)
        wide = page.locator("[data-field=ready]").locator("xpath=..").bounding_box()["width"]
        assert wide > width  # grows with what is typed

    @pytest.mark.parametrize("scheme", ["light", "dark"])
    def test_stays_dark_whatever_the_system_says(self, new_page, live_server, scheme):
        page = new_page(color_scheme=scheme)
        page.goto(live_server + "/")
        colour = page.eval_on_selector("body", "e => getComputedStyle(e).backgroundColor")
        channels = [int(part) for part in colour.removeprefix("rgb(").removesuffix(")").split(",")]
        assert max(channels) < 30, colour  # the page background is near-black
