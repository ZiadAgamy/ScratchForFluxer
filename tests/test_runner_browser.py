"""Running a bot from the editor, in a real browser, with a real bot process and a pretend Fluxer.

The site runs its bots itself here (``RUNNER_MODE=local``): pressing Run starts a Python process with the real
fluxer.py, which logs in to a pretend Fluxer on a local port, and the console shows what that process writes. Skipped
automatically when Playwright/Chromium isn't installed.
"""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlparse

import pytest
from blocks import block as block_of
from blocks import command, workspace
from fluxer_mock import OWNER_FLUXER_ID, TOKEN
from mock_fluxer_server import MockFluxer
from playwright.sync_api import expect
from test_editor_browser import WATCH_THE_POLICY, assert_quiet, store_workspace, wait_until_ready

from scratch_fluxer import config, deps

pytestmark = [pytest.mark.browser, pytest.mark.e2e]

PHONE = {"width": 390, "height": 844}


@pytest.fixture
def mock_fluxer():
    server = MockFluxer()
    server.start()
    yield server
    server.stop()


def make_opener(name: str, mode: str):
    """A fixture that opens the editor for a new bot, with the runner in the given mode.

    The site talks to the pretend Fluxer for everything (it checks the bot's token there), and bots started by the
    runner connect to it too. The settings are in place before the server starts.
    """

    @pytest.fixture(name=name)
    def opener(monkeypatch, mock_fluxer, new_page, live_server, make_user):
        monkeypatch.setenv("RUNNER_MODE", mode)
        monkeypatch.setenv("FLUXER_API_URL", mock_fluxer.api_url)
        config.reset_settings_cache()
        deps.reset_singletons()

        def _open(state=None, viewport=None, before=None):
            _, csrf, cookies = make_user(fluxer_id=OWNER_FLUXER_ID, username="agamy", global_name="AGAMY")
            page = new_page(cookies=cookies, viewport=viewport)
            page.add_init_script(WATCH_THE_POLICY)
            added = page.request.post(
                f"{live_server}/api/bots",
                data={"token": TOKEN},
                headers={"X-CSRF-Token": csrf, "Origin": live_server},
            )
            assert added.status == 201, added.text()
            page.bot_id = added.json()["bot"]["id"]
            if state is not None:
                store_workspace(page.bot_id, state)
            if before is not None:
                before(page)
            page.goto(f"{live_server}/bots/{page.bot_id}/edit")
            wait_until_ready(page)
            return page

        return _open

    return opener


# The server has to see the settings before it starts, so the opener comes first in every list of fixtures.
open_runner_editor = make_opener("open_runner_editor", "local")
open_editor_without_runner = make_opener("open_editor_without_runner", "off")


def state_label(page) -> str:
    return page.inner_text("#run-state")


def buttons(page) -> tuple[bool, bool, bool]:
    """Whether Run, Restart and Stop can be pressed."""
    return tuple(page.is_enabled(f"#{name}-btn") for name in ("run", "restart", "stop"))  # type: ignore[return-value]


def console_lines(page, text: str):
    return page.locator("#console-log .log-line", has_text=text)


def wait_for_running(page) -> None:
    expect(page.locator("#run-state")).to_contain_text("Running", timeout=25000)


# ================================================================================================= the three buttons
class TestRunStopRestart:
    def test_the_starter_bot_runs_answers_a_command_and_stops(self, open_runner_editor, mock_fluxer):
        page = open_runner_editor()
        assert state_label(page) == "Not running" and buttons(page) == (True, False, False)

        page.click("#run-btn")
        wait_for_running(page)
        expect(page.locator("#console-log")).to_contain_text("Bot is online!")
        assert buttons(page) == (False, True, True)

        mock_fluxer.say("!ping")
        assert mock_fluxer.wait_for(lambda: mock_fluxer.replies() == ["Pong!"])

        page.click("#stop-btn")
        expect(page.locator("#run-state")).to_have_text("Stopped", timeout=20000)
        expect(page.locator("#console-log")).to_contain_text("Your bot stopped.")
        assert buttons(page) == (True, False, False)
        assert mock_fluxer.wait_for(lambda: mock_fluxer.connected == 0)
        assert page.locator("#console-log .lvl-error, #console-log .lvl-warning").count() == 0
        assert_quiet(page)

    def test_the_time_counts_up_while_it_runs(self, open_runner_editor):
        page = open_runner_editor()
        page.click("#run-btn")
        wait_for_running(page)
        first = state_label(page)
        page.wait_for_function(
            "(first) => document.getElementById('run-state').textContent !== first", arg=first, timeout=5000
        )
        assert state_label(page).startswith("Running · ")

    def test_restart_begins_a_new_console_and_leaves_one_bot_connected(self, open_runner_editor, mock_fluxer):
        page = open_runner_editor()
        page.click("#run-btn")
        wait_for_running(page)
        expect(console_lines(page, "Bot is online!")).to_have_count(1, timeout=10000)

        page.click("#restart-btn")
        # The old run's last words are gone with its console, and the new run says its own.
        expect(console_lines(page, "Bot stopped by KeyboardInterrupt")).to_have_count(0, timeout=20000)
        expect(console_lines(page, "Bot is online!")).to_have_count(1, timeout=20000)
        expect(console_lines(page, "Starting your bot…")).to_have_count(1)
        wait_for_running(page)
        assert mock_fluxer.wait_for(lambda: mock_fluxer.connected == 1)
        mock_fluxer.say("!ping")
        assert mock_fluxer.wait_for(lambda: mock_fluxer.replies() == ["Pong!"])
        page.wait_for_timeout(500)
        assert mock_fluxer.replies() == ["Pong!"]  # one bot answers, not two

    def test_pressing_run_again_after_a_stop_starts_a_fresh_run(self, open_runner_editor):
        page = open_runner_editor()
        page.click("#run-btn")
        wait_for_running(page)
        page.click("#stop-btn")
        expect(page.locator("#run-state")).to_have_text("Stopped", timeout=20000)
        page.click("#run-btn")
        wait_for_running(page)
        expect(console_lines(page, "Starting your bot…")).to_have_count(1)
        expect(console_lines(page, "Your bot stopped.")).to_have_count(0)

    def test_the_latest_changes_are_saved_before_the_bot_starts(self, open_runner_editor, mock_fluxer):
        page = open_runner_editor()
        page.evaluate(
            "() => window.sffEditor.blocks.workspace().getBlockById('reply-text').setFieldValue('Changed!', 'TEXT')"
        )
        page.click("#run-btn")  # before the page has had a moment to save by itself
        wait_for_running(page)
        mock_fluxer.say("!ping")
        assert mock_fluxer.wait_for(lambda: mock_fluxer.replies() == ["Changed!"])

    def test_a_running_bot_is_picked_up_again_when_the_page_is_reopened(self, open_runner_editor, mock_fluxer):
        page = open_runner_editor()
        page.click("#run-btn")
        wait_for_running(page)
        expect(console_lines(page, "Bot is online!")).to_have_count(1, timeout=10000)

        page.reload()
        wait_until_ready(page)
        wait_for_running(page)
        expect(console_lines(page, "Bot is online!")).to_have_count(1, timeout=10000)
        assert buttons(page) == (False, True, True)

        page.click("#stop-btn")
        expect(page.locator("#run-state")).to_have_text("Stopped", timeout=20000)
        page.reload()
        wait_until_ready(page)
        expect(page.locator("#run-state")).to_have_text("Stopped", timeout=10000)  # what happened is still there
        expect(page.locator("#console-log")).to_contain_text("Your bot stopped.")
        assert mock_fluxer.connected == 0
        assert_quiet(page)


# ================================================================================================= when it does not run
class TestBlocksThatCannotRun:
    def test_blocks_with_problems_are_not_started_and_the_problems_are_shown(self, open_runner_editor):
        broken = workspace(command("pic", block_of("sff_send_image", fields={"FILE": "gone.png"})))
        page = open_runner_editor(state=broken)
        posts = []
        page.on("request", lambda request: posts.append(request.url) if request.method == "POST" else None)
        page.click("#run-btn")
        expect(page.locator(".toast")).to_contain_text("Fix the problems")
        assert page.get_attribute("#tab-problems", "aria-selected") == "true"
        assert not [url for url in posts if url.endswith("/run") or url.endswith("/restart")]
        assert state_label(page) == "Not running"
        assert "Fix the problems" in page.get_attribute("#run-btn", "title")


class TestWhenSomethingGoesWrong:
    def test_a_failing_command_shows_help_the_details_and_a_way_back_to_its_block(
        self, open_runner_editor, mock_fluxer
    ):
        # The pretend Fluxer has no reactions, so this one is answered with "not found".
        react = block_of("sff_react", fields={"EMOJI": "👍"}, id="react-block")
        page = open_runner_editor(state=workspace(command("react", react)))
        page.click("#run-btn")
        wait_for_running(page)
        mock_fluxer.say("!react")

        error = page.locator("#console-log .log-line.lvl-error").first
        expect(error).to_be_visible(timeout=15000)
        expect(error.locator(".log-text")).to_contain_text("Error in command 'react'")
        expect(error.locator(".log-summary")).to_contain_text("NotFound")
        expect(error.locator(".log-note")).to_contain_text("could not find what the bot asked for")
        details = error.locator(".log-details")
        assert details.is_hidden()
        error.get_by_role("button", name="Show details").click()
        expect(details).to_be_visible()
        expect(details).to_contain_text('File "main.py", line')
        error.get_by_role("button", name="Hide details").click()
        assert details.is_hidden()

        error.get_by_role("button", name="Show block").click()
        assert page.evaluate("() => window.Blockly.common.getSelected().id") == "react-block"
        assert page.locator("#run-state").inner_text().startswith("Running")  # one command failing doesn't end the bot
        assert_quiet(page)

    def test_the_errors_filter_shows_only_the_errors(self, open_runner_editor, mock_fluxer):
        react = block_of("sff_react", fields={"EMOJI": "👍"})
        page = open_runner_editor(state=workspace(command("react", react)))
        page.click("#run-btn")
        wait_for_running(page)
        mock_fluxer.say("!react")
        expect(page.locator("#console-log .lvl-error")).to_have_count(1, timeout=15000)
        page.click('.ed-filter[data-filter="error"]')
        visible = page.locator("#console-log .log-line:visible")
        expect(visible).to_have_count(1)
        assert "ed-count-error" in page.get_attribute('.ed-filter[data-filter="error"] .ed-count', "class")

    def test_a_token_that_fluxer_refuses_is_explained_in_the_console(self, open_runner_editor, mock_fluxer):
        page = open_runner_editor()
        mock_fluxer.applications.clear()  # Fluxer no longer knows the bot's token (it was reset)
        page.click("#run-btn")
        expect(page.locator("#run-state")).to_have_text("Crashed", timeout=25000)
        expect(page.locator("#console-log .log-note").first).to_contain_text("did not accept the bot's token")
        assert buttons(page) == (True, False, False)
        expect(page.locator(".toast").first).to_contain_text("stopped because of a problem")
        page.click('.ed-filter[data-filter="error"]')
        assert page.locator("#console-log .log-line:visible").count() >= 1


# ================================================================================================= phones and switched off
class TestOnAPhone:
    def test_pressing_run_shows_the_console_and_nothing_overflows(self, open_runner_editor):
        page = open_runner_editor(viewport=PHONE)
        page.click("#run-btn")
        page.wait_for_function("() => document.getElementById('editor').dataset.view === 'console'")
        wait_for_running(page)
        expect(page.locator("#console-log")).to_contain_text("Bot is online!")
        assert page.evaluate("() => document.documentElement.scrollWidth") <= PHONE["width"]
        page.click("#stop-btn")
        expect(page.locator("#run-state")).to_have_text("Stopped", timeout=20000)
        assert_quiet(page)


class TestWhenRunningIsSwitchedOff:
    def test_the_buttons_are_off_and_the_console_says_why(self, open_editor_without_runner):
        page = open_editor_without_runner()
        assert buttons(page) == (False, False, False)
        assert state_label(page) == "Not running"
        expect(page.locator("#console-log")).to_contain_text("isn't switched on for this site")
        assert "isn't switched on" in page.get_attribute("#run-btn", "title")
        assert page.get_attribute("#ed-get", "id") == "ed-get"  # downloading the code is still there
        assert_quiet(page)


# ================================================================================================= scripted answers
RUN_A = "a" * 32
RUN_B = "b" * 32


def a_line(n: int, text: str, level: str = "info", source: str = "print", **more) -> dict:
    return {"n": n, "t": time.time(), "level": level, "source": source, "text": text, **more}


def a_run(state: str = "running", run: str | None = RUN_A, lines=(), **more) -> dict:
    """What the site answers about a run (see ``scratch_fluxer/runner/manager.py: view``)."""
    now = time.time()
    lines = list(lines)
    answer = {
        "now": now,
        "run": run,
        "state": state,
        "reason": "",
        "ready": state == "running",
        "exit_code": None,
        "started_at": now - more.pop("started_ago", 5),
        "ended_at": None,
        "lines": lines,
        "next": lines[-1]["n"] if lines else 0,
        "skipped": 0,
        "reset": False,
    }
    answer.update(more)
    return answer


class Script:
    """Answers the editor's calls about runs from a list, so the page can be shown any answer the site could give.

    ``reads`` are the answers to ``GET .../run`` in turn (the last one is repeated); ``posts`` are the answers to the
    buttons (``run``, ``restart``, ``stop``), each a run or a ``(status, body)`` pair.
    """

    def __init__(self, reads, **posts):
        self.reads = list(reads)
        self.posts = posts
        self.calls: list[tuple[str, str, dict]] = []

    def install(self, page) -> None:
        page.route(re.compile(r".*/api/bots/[0-9a-f]+/(run|restart|stop)(\?.*)?$"), self.handle)

    def handle(self, route) -> None:
        request = route.request
        url = urlparse(request.url)
        kind = url.path.rsplit("/", 1)[1]
        self.calls.append((request.method, kind, parse_qs(url.query)))
        if request.method == "GET":
            answer = self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]
        else:
            answer = self.posts[kind]
        status, body = answer if isinstance(answer, tuple) else (200, answer)
        route.fulfill(status=status, json=body)

    def reads_made(self) -> int:
        return len([call for call in self.calls if call[0] == "GET"])


class TestTheConsoleFollowsWhatTheSiteSays:
    def test_a_run_that_lost_its_first_lines_says_so(self, open_runner_editor):
        script = Script(
            [
                a_run(lines=[a_line(301, "late line"), a_line(302, "later line")], skipped=300),
                a_run(lines=[a_line(301, "late line"), a_line(302, "later line")]),
            ]
        )
        page = open_runner_editor(before=script.install)
        expect(page.locator("#console-log")).to_contain_text("The first 300 messages of this run are not shown")
        expect(console_lines(page, "late line")).to_have_count(1)
        page.wait_for_timeout(2500)  # more polls come in with the same lines: none is shown twice
        expect(console_lines(page, "later line")).to_have_count(1)

    def test_when_the_bot_started_again_somewhere_else_the_console_starts_over(self, open_runner_editor):
        script = Script(
            [
                a_run(run=RUN_A, lines=[a_line(1, "first run"), a_line(2, "still first")]),
                a_run(run=RUN_B, reset=True, lines=[a_line(1, "second run")]),
            ]
        )
        page = open_runner_editor(before=script.install)
        expect(console_lines(page, "second run")).to_have_count(1, timeout=8000)
        expect(console_lines(page, "first run")).to_have_count(0)
        queries = [call[2] for call in script.calls if call[0] == "GET"]
        # the first read (when the page opens) has no position yet; the next one carries the position and the run it knows
        assert queries[0] == {} and queries[1] == {"after": ["2"], "run": [RUN_A]}

    def test_a_run_the_runner_has_forgotten_keeps_its_lines_and_stops_the_polling(self, open_runner_editor):
        script = Script([a_run(lines=[a_line(1, "hello")]), a_run(state="none", run=None, reset=True)])
        page = open_runner_editor(before=script.install)
        expect(page.locator("#console-log")).to_contain_text("The runner no longer has this run", timeout=8000)
        expect(console_lines(page, "hello")).to_have_count(1)
        assert state_label(page) == "Not running" and buttons(page) == (True, False, False)
        reads = script.reads_made()
        page.wait_for_timeout(2500)
        assert script.reads_made() == reads  # nothing is running, so nothing is asked any more

    def test_a_runner_that_cannot_be_reached_is_waited_for_and_then_followed_again(self, open_runner_editor):
        script = Script(
            [
                a_run(lines=[a_line(1, "before")]),
                (503, {"detail": "The bot runner can't be reached right now. Try again in a moment."}),
                a_run(lines=[a_line(1, "before"), a_line(2, "after")]),
            ]
        )
        page = open_runner_editor(before=script.install)
        expect(page.locator("#run-state")).to_contain_text("Can't reach the runner", timeout=8000)
        assert page.get_attribute("#run-state", "data-state") == "error"
        expect(page.locator("#run-state")).to_contain_text("Running", timeout=10000)
        expect(console_lines(page, "after")).to_have_count(1)
        assert page.get_attribute("#run-state", "data-state") == "running"

    def test_the_time_comes_from_the_runner_s_clock(self, open_runner_editor):
        script = Script([a_run(started_ago=307)])
        page = open_runner_editor(before=script.install)
        expect(page.locator("#run-state")).to_have_text(re.compile(r"Running · 5m (0[7-9]|1\d)s"), timeout=8000)

    def test_a_press_of_run_on_a_bot_that_is_already_running_adds_nothing_twice(self, open_runner_editor):
        lines = [a_line(1, "one"), a_line(2, "two")]
        script = Script([a_run(state="none", run=None)], run=a_run(lines=lines, already_running=True))
        page = open_runner_editor(before=script.install)
        page.click("#run-btn")
        wait_for_running(page)
        expect(console_lines(page, "two")).to_have_count(1)
        script.reads[:] = [a_run(lines=lines)]
        page.wait_for_timeout(2500)
        assert console_lines(page, "one").count() == 1 and console_lines(page, "two").count() == 1

    def test_what_the_site_refuses_is_said_in_a_toast_and_in_the_console(self, open_runner_editor):
        refusal = "You can run 2 bots at the same time. Stop one of them first."
        script = Script([a_run(state="none", run=None)], run=(409, {"detail": refusal}))
        page = open_runner_editor(before=script.install)
        page.click("#run-btn")
        expect(page.locator(".toast").first).to_contain_text(refusal)
        line = console_lines(page, refusal)
        expect(line).to_have_count(1)
        assert line.get_attribute("data-level") == "error" and "runner" in line.inner_text()
        assert buttons(page) == (True, False, False) and state_label(page) == "Not running"

    def test_pressing_too_fast_is_explained_kindly(self, open_runner_editor):
        script = Script(
            [a_run(state="none", run=None)], run=(429, {"detail": "Too many requests. Please slow down a little."})
        )
        page = open_runner_editor(before=script.install)
        page.click("#run-btn")
        expect(page.locator(".toast").first).to_contain_text("too many presses")

    def test_a_crash_that_happens_while_looking_away_is_announced(self, open_runner_editor):
        crash = a_run(
            state="crashed",
            reason="The bot stopped because of an error. The red messages above say what went wrong.",
            lines=[a_line(1, "Your bot stopped because of an error.\nValueError: boom", "error", "sff")],
        )
        script = Script([a_run(), crash])
        page = open_runner_editor(before=script.install)
        expect(page.locator("#run-state")).to_have_text("Crashed", timeout=8000)
        expect(page.locator(".toast").first).to_contain_text("stopped because of a problem")
        assert "red messages" in page.get_attribute("#run-state", "title")
        assert buttons(page) == (True, False, False)
        assert_quiet(page)
