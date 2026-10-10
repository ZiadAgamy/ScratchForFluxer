"""A real bot, start to finish: the code the compiler writes runs in a real process with the real fluxer.py, the runner
reads what it says, and a pretend Fluxer (a real server on a local port) is on the other end.

Nothing here is faked on the bot's side of the network: the safety check is the real one, the process is started the way
a visitor's bot is, and it connects, logs in, receives messages and answers them over real web sockets and HTTP.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from blocks import block, change_var, command, forever, on_ready, reply, var_id, workspace
from fluxer_mock import TOKEN, TOKEN_2, application
from mock_fluxer_server import MockFluxer

from scratch_fluxer.compiler import compile_workspace, default_workspace
from scratch_fluxer.config import Settings
from scratch_fluxer.runner import manager as manager_module
from scratch_fluxer.runner.manager import RunnerManager
from scratch_fluxer.runner.protocol import StartRequest

pytestmark = pytest.mark.e2e

OWNER = "b" * 32
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
QUOTES = b'[{"text": "Keep going"}, {"text": "Keep going"}]'
SECRET_PART = TOKEN.split(".")[1]


def bot_id(n: int = 1) -> str:
    return f"{n:032x}"


@pytest.fixture
def fluxer_server():
    server = MockFluxer()
    server.start()
    yield server
    server.stop()


@pytest.fixture
async def manager(tmp_path, fluxer_server):
    settings = Settings(
        _env_file=None,
        runner_mode="local",
        runner_max_concurrent=4,
        runner_max_per_user=3,
        fluxer_api_url=fluxer_server.api_url,
    )
    made = RunnerManager(settings, base=tmp_path / "runs")  # with the real safety check
    yield made
    await made.shutdown()


# ------------------------------------------------------------------------------------------------------ helpers
def request_for(ws, n: int = 1, token: str = TOKEN, files=()) -> StartRequest:
    kinds = {name: ("image" if name.endswith(".png") else "json") for name, _ in files}
    compiled = compile_workspace(ws, assets=kinds)
    assert compiled.ok, compiled.diagnostics
    return StartRequest(bot_id=bot_id(n), owner=OWNER, code=compiled.code, token=token, files=list(files))


async def eventually(condition, seconds: float = 20.0, what: str = "the condition") -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return
        await asyncio.sleep(0.05)  # noqa: ASYNC110 - a test that waits for a bot in another process
    assert condition(), f"gave up waiting for {what}"


async def until(manager: RunnerManager, test, n: int = 1, seconds: float = 20.0) -> dict:
    end = time.monotonic() + seconds
    while True:
        snap = manager.snapshot(bot_id(n), 0, None)
        if test(snap):
            return snap
        if time.monotonic() > end:
            lines = [(line["level"], line["text"]) for line in snap["lines"]]
            raise AssertionError(f"gave up waiting; the run is {snap['state']}: {snap['reason']!r} {lines}")
        await asyncio.sleep(0.05)


async def online(manager: RunnerManager, n: int = 1) -> dict:
    return await until(manager, lambda snap: snap["state"] == "running" and snap["ready"], n)


async def finished(manager: RunnerManager, n: int = 1, seconds: float = 20.0) -> dict:
    return await until(manager, lambda snap: snap["state"] in ("stopped", "crashed"), n, seconds)


def texts(snap: dict, level: str | None = None, source: str | None = None) -> list[str]:
    return [
        line["text"]
        for line in snap["lines"]
        if (level is None or line["level"] == level) and (source is None or line["source"] == source)
    ]


def nothing_secret(snap: dict, *others: str) -> None:
    everything = "\n".join(line["text"] + line.get("hint", "") for line in snap["lines"]) + snap["reason"]
    for secret in (TOKEN, SECRET_PART, *others):
        assert secret not in everything


# ------------------------------------------------------------------------------------------------------ the starting bot
class TestTheStartingBot:
    async def test_it_comes_online_answers_a_command_and_stops_when_told_to(self, manager, fluxer_server):
        await manager.start(request_for(default_workspace()))
        snap = await online(manager)
        assert texts(snap, "info", "print") == ["Bot is online!"]
        assert snap["reason"] == "" and snap["exit_code"] is None

        fluxer_server.say("!ping")
        await eventually(lambda: fluxer_server.replies() == ["Pong!"], what="the answer to !ping")
        assert fluxer_server.requests[0]["token"] == TOKEN  # the bot logged in with the token it was given

        stopped = await manager.stop(bot_id())
        assert stopped["state"] == "stopped" and stopped["exit_code"] == 0
        assert stopped["reason"] == "You stopped the bot."
        assert texts(stopped, "system")[-1] == "Your bot stopped."
        assert not texts(stopped, "error") and not texts(stopped, "warning")
        assert "Unclosed" not in "\n".join(texts(stopped))
        nothing_secret(stopped)

    async def test_the_bots_files_exist_only_while_it_runs(self, manager, tmp_path):
        await manager.start(request_for(default_workspace()))
        await online(manager)
        assert (tmp_path / "runs" / bot_id() / "main.py").is_file()
        await manager.stop(bot_id())
        assert not (tmp_path / "runs" / bot_id()).exists()

    async def test_the_bot_stays_connected_until_it_is_stopped_and_then_leaves(self, manager, fluxer_server):
        await manager.start(request_for(default_workspace()))
        await online(manager)
        assert fluxer_server.connected == 1
        await manager.stop(bot_id())
        await eventually(lambda: fluxer_server.connected == 0, what="the bot to leave")

    async def test_restarting_starts_a_new_run_and_the_console_begins_again(self, manager, fluxer_server):
        first = await manager.start(request_for(default_workspace()))
        await online(manager)
        again = await manager.start(request_for(default_workspace(ready="Back again!")))
        assert again["run"] != first["run"]
        snap = await online(manager)
        assert "Back again!" in texts(snap, "info", "print") and "Bot is online!" not in texts(snap)
        reading = manager.snapshot(bot_id(), 5, first["run"])
        assert reading["reset"] is True and reading["lines"][0]["n"] == 1
        fluxer_server.say("!ping")
        await eventually(lambda: fluxer_server.replies() == ["Pong!"], what="one answer, not two")
        await asyncio.sleep(0.5)
        assert fluxer_server.replies() == ["Pong!"]  # the first copy of the bot is gone
        assert fluxer_server.connected == 1


# ------------------------------------------------------------------------------------------------------ files
class TestUploadedFiles:
    async def test_a_bot_sends_a_picture_and_reads_a_json_file_from_its_own_folder(self, manager, fluxer_server):
        load = block("sff_json_load", fields={"FILE": "quotes.json", "VAR": {"id": var_id("quotes")}})
        get = block("sff_json_get", fields={"VAR": {"id": var_id("quotes")}}, inputs={"KEY": "1.text"})
        ws = workspace(
            command("pic", block("sff_reply_image", fields={"FILE": "cat.png"})),
            command("quote", load, reply(get)),
            variables=("quotes",),
        )
        files = [("cat.png", PNG), ("quotes.json", QUOTES)]
        await manager.start(request_for(ws, files=files))
        await online(manager)
        fluxer_server.say("!pic")
        await eventually(lambda: fluxer_server.uploads() == [("cat.png", PNG)], what="the picture")
        fluxer_server.say("!quote")
        await eventually(lambda: "Keep going" in fluxer_server.replies(), what="the quote")
        snap = manager.snapshot(bot_id(), 0, None)
        assert snap["state"] == "running" and not texts(snap, "error")


# ------------------------------------------------------------------------------------------------------ status
class TestStatus:
    async def test_a_status_and_a_custom_status_reach_fluxer(self, manager, fluxer_server):
        ws = workspace(
            on_ready(
                block("sff_set_custom_status", inputs={"TEXT": "Learning blocks"}),
                block("sff_set_status", fields={"STATUS": "idle"}),
            )
        )
        await manager.start(request_for(ws))
        await online(manager)
        await eventually(lambda: len(fluxer_server.presences) >= 2, what="the status")
        assert fluxer_server.presences[0]["custom_status"] == {"text": "Learning blocks"}
        assert fluxer_server.presences[-1]["status"] == "idle"
        assert fluxer_server.presences[-1]["custom_status"] == {"text": "Learning blocks"}
        snap = manager.snapshot(bot_id(), 0, None)
        assert snap["state"] == "running" and not texts(snap, "error") and not texts(snap, "warning")


# ------------------------------------------------------------------------------------------------------ trouble
class TestWhenSomethingGoesWrong:
    async def test_a_token_fluxer_does_not_accept_is_explained(self, manager):
        wrong = "999999999999999999." + "Z" * 40
        await manager.start(request_for(default_workspace(), token=wrong))
        snap = await finished(manager)
        assert snap["state"] == "crashed" and snap["exit_code"] != 0
        hints = [line.get("hint", "") for line in snap["lines"] if line["level"] == "error"]
        assert any("did not accept the bot's token" in hint for hint in hints), snap["lines"]
        assert texts(snap, "system")[-1] == "Your bot stopped unexpectedly."  # the red message says why
        nothing_secret(snap, wrong, "Z" * 40)

    async def test_being_closed_for_good_by_fluxer_is_a_crash_with_a_reason(self, manager, fluxer_server):
        fluxer_server.close_with = 4004
        await manager.start(request_for(default_workspace()))
        snap = await finished(manager)
        assert snap["state"] == "crashed" and "closed the connection for good" in snap["reason"]
        assert any("did not accept the bot's token" in line.get("hint", "") for line in snap["lines"])

    async def test_a_bot_that_fluxer_never_welcomes_is_called_running_after_a_while(
        self, manager, fluxer_server, monkeypatch
    ):
        monkeypatch.setattr(manager_module, "READY_FALLBACK_SECONDS", 2)
        fluxer_server.hold_ready = True
        await manager.start(request_for(default_workspace()))
        snap = await until(manager, lambda snap: snap["state"] == "running")
        assert snap["ready"] is False and "Bot is online!" not in texts(snap)

    async def test_a_loop_without_a_wait_is_stopped_and_explained(self, manager, monkeypatch):
        monkeypatch.setattr(manager_module, "CPU_WINDOW_SECONDS", 3)
        monkeypatch.setattr(manager_module, "TICK_SECONDS", 0.25)
        ws = workspace(on_ready(forever(change_var("n", 1))), variables=("n",))
        await manager.start(request_for(ws))
        snap = await finished(manager, seconds=30)
        assert snap["state"] == "crashed" and "all of its processor time" in snap["reason"]
        assert "wait" in snap["reason"]

    async def test_fluxer_refusing_something_shows_up_as_a_red_message_with_help_and_the_bot_goes_on(
        self, manager, fluxer_server
    ):
        # The pretend Fluxer has no reactions, so the answer to this one is "not found", like a deleted message would be.
        await manager.start(request_for(workspace(command("react", block("sff_react", fields={"EMOJI": "👍"})))))
        await online(manager)
        fluxer_server.say("!react")
        snap = await until(manager, lambda snap: any(line.get("hint") for line in snap["lines"]))
        mistake = next(line for line in snap["lines"] if line.get("hint"))
        assert mistake["level"] == "error" and "could not find what the bot asked for" in mistake["hint"]
        assert 'File "main.py", line ' in mistake["text"] and "NotFound" in mistake["text"]
        assert "/runs/" not in mistake["text"] and "site-packages" not in mistake["text"]
        assert snap["state"] == "running"  # one command failing does not end the bot


# ------------------------------------------------------------------------------------------------------ more than one
class TestTwoBotsAtOnce:
    async def test_each_bot_has_its_own_token_and_its_own_console(self, tmp_path):
        server = MockFluxer(
            {TOKEN: application(), TOKEN_2: application("222222222222222222", "Quiz Bot", bot_username="quizbot")}
        )
        server.start()
        settings = Settings(_env_file=None, runner_mode="local", runner_max_per_user=3, fluxer_api_url=server.api_url)
        manager = RunnerManager(settings, base=tmp_path / "runs")
        try:
            await manager.start(request_for(default_workspace(), n=1))
            await manager.start(
                request_for(default_workspace(ready="Quiz bot is here!", reply="Quiz time!"), n=2, token=TOKEN_2)
            )
            first, second = await online(manager, 1), await online(manager, 2)
            assert texts(first, "info", "print") == ["Bot is online!"]
            assert texts(second, "info", "print") == ["Quiz bot is here!"]
            server.say("!ping")
            await eventually(lambda: len(server.replies()) == 2, what="both answers")
            answers = {call["token"]: call["json"]["content"] for call in server.requests if call["method"] == "POST"}
            assert answers == {TOKEN: "Pong!", TOKEN_2: "Quiz time!"}
            await manager.stop(bot_id(1))
            assert manager.snapshot(bot_id(1), 0, None)["state"] == "stopped"
            assert manager.snapshot(bot_id(2), 0, None)["state"] == "running"
            for n in (1, 2):
                nothing_secret(manager.snapshot(bot_id(n), 0, None), TOKEN_2, TOKEN_2.split(".")[1])
        finally:
            await manager.shutdown()
            server.stop()
