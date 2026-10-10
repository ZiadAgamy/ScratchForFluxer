"""The runner's manager with real processes: a script is started the way a bot is, and what it does is read back.

The scripts here are plain Python (the manager is given a code check that lets them through), so each test can make a
process do exactly one thing: print, fail, loop, ignore Ctrl+C, eat memory. The real check on generated bots is
tested in ``test_codecheck.py`` and ``test_runner_e2e.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

from scratch_fluxer.config import Settings
from scratch_fluxer.runner import manager as manager_module
from scratch_fluxer.runner import sandbox
from scratch_fluxer.runner.manager import RunnerManager
from scratch_fluxer.runner.protocol import Busy, Refused, StartRequest, TooMany

TOKEN = "111111111111111111.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1234"
SECRET_PART = TOKEN.split(".")[1]
OWNER = "b" * 32


CAN_GIVE_USERS = sandbox.can_switch_users() and not sandbox.missing_capabilities()


def bot_id(n: int = 1) -> str:
    return f"{n:032x}"


def script(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


SLEEP = script("""
    import time
    time.sleep(120)
""")


# A bot that starts a process and does not wait for it. The process keeps the bot's output open for as long as it lives,
# which used to keep a run from ever ending on Python 3.11 and 3.12. It is in a group of its own, so the signal that
# stops the bot does not reach it.
LEAVES_ONE_BEHIND = script("""
    import subprocess, sys, time
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True)
    print(child.pid, flush=True)
""")


async def the_one_left_behind(manager: RunnerManager) -> int:
    snap = await until(manager, lambda snap: texts(snap, "info", "print"))
    return int(texts(snap, "info", "print")[0])


async def put_an_end_to(pid: int) -> None:
    """End what a bot left behind, and give asyncio the moment it needs to see the bot's output close, so that nothing is
    half closed when the test (and its event loop) ends."""
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)
    await gone(pid)
    await asyncio.sleep(0.2)


def request(code: str, n: int = 1, owner: str = OWNER, files=()) -> StartRequest:
    return StartRequest(bot_id=bot_id(n), owner=owner, code=code, token=TOKEN, files=list(files))


def mode_of(path: Path) -> int:
    return path.stat().st_mode & 0o777


@pytest.fixture
def shared_runs_folder():
    """A runs folder that any user on the computer can walk into (pytest's own temporary folders are private)."""
    top = Path(tempfile.mkdtemp(prefix="sff-uid-"))
    top.chmod(0o755)
    base = top / "runs"
    base.mkdir(mode=0o755)
    yield base
    shutil.rmtree(top, ignore_errors=True)


@pytest.fixture
async def manager(tmp_path):
    settings = Settings(_env_file=None, runner_mode="local", runner_max_concurrent=3, runner_max_per_user=2)
    made = RunnerManager(settings, check=lambda code: None, base=tmp_path / "runs")
    yield made
    await made.shutdown()


async def until(manager: RunnerManager, test, n: int = 1, seconds: float = 20.0) -> dict:
    """The newest snapshot of the bot, once ``test(snapshot)`` is true."""
    end = time.monotonic() + seconds
    while True:
        snap = manager.snapshot(bot_id(n), 0, None)
        if test(snap):
            return snap
        if time.monotonic() > end:
            raise AssertionError(f"gave up waiting; the run is {snap['state']}: {snap['reason']!r} {snap['lines']}")
        await asyncio.sleep(0.05)


async def finished(manager: RunnerManager, n: int = 1, seconds: float = 20.0) -> dict:
    return await until(manager, lambda snap: snap["state"] in ("stopped", "crashed"), n, seconds)


def texts(snap: dict, level: str | None = None, source: str | None = None) -> list[str]:
    return [
        line["text"]
        for line in snap["lines"]
        if (level is None or line["level"] == level) and (source is None or line["source"] == source)
    ]


async def gone(pid: int, seconds: float = 5.0) -> bool:
    """Whether the process has ended, within ``seconds``."""
    end = time.monotonic() + seconds
    while True:
        if not is_alive(pid):
            return True
        if time.monotonic() > end:
            return False
        await asyncio.sleep(0.05)


def is_alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"  # a zombie has already finished
    except FileNotFoundError:
        return False
    except OSError:
        return False


# ======================================================================================================= messages
class TestWhatABotWrites:
    async def test_what_it_prints_is_an_info_message(self, manager):
        await manager.start(request('print("hello")\nprint("second")'))
        snap = await finished(manager)
        assert texts(snap, "info", "print") == ["hello", "second"]

    async def test_the_levels_of_the_log_become_the_levels_of_the_messages(self, manager):
        await manager.start(
            request(
                script("""
                    import logging
                    log = logging.getLogger("bot")
                    log.info("one")
                    log.warning("two")
                    log.error("three")
                    log.critical("four")
                """)
            )
        )
        snap = await finished(manager)
        got = [(line["level"], line["source"], line["text"]) for line in snap["lines"] if line["source"] == "bot"]
        assert got == [
            ("info", "bot", "one"),
            ("warning", "bot", "two"),
            ("error", "bot", "three"),
            ("error", "bot", "four"),
        ]

    async def test_a_message_with_several_lines_stays_one_message(self, manager):
        await manager.start(
            request('import logging\nlogging.getLogger("bot").warning("first\\nsecond\\nERROR\\tbot\\tnot a new one")')
        )
        snap = await finished(manager)
        assert texts(snap, "warning") == ["first\nsecond\nERROR\tbot\tnot a new one"]
        assert not texts(snap, "error")

    async def test_a_traceback_is_one_error_and_names_main_py_not_the_runner_s_folders(self, manager, tmp_path):
        await manager.start(
            request(
                script("""
                    import logging
                    def work():
                        return 1 / 0
                    try:
                        work()
                    except ZeroDivisionError:
                        logging.getLogger("bot").exception("work failed")
                """)
            )
        )
        snap = await finished(manager)
        (error,) = texts(snap, "error")
        assert error.startswith("work failed\nTraceback (most recent call last):")
        assert 'File "main.py", line 5, in <module>' in error and 'File "main.py", line 3, in work' in error
        assert "ZeroDivisionError: division by zero" in error
        assert str(tmp_path) not in error

    async def test_the_message_that_ends_a_bot_has_no_frames_of_the_runner_in_it(self, manager):
        await manager.start(request('def go():\n    raise ValueError("boom")\ngo()\n'))
        snap = await finished(manager)
        (error,) = texts(snap, "error")
        assert error.startswith("Your bot stopped because of an error.")
        assert "ValueError: boom" in error and 'File "main.py", line 2, in go' in error
        for inside in ("child.py", "runpy"):
            assert inside not in error
        assert snap["state"] == "crashed" and snap["exit_code"] == 1

    async def test_the_token_is_hidden_wherever_it_turns_up(self, manager):
        await manager.start(
            request(
                script(f"""
                    import logging, os
                    token = os.environ["FLUXER_TOKEN"]
                    print(token)
                    print("only the secret part:", {SECRET_PART!r})
                    print(f"in the middle {{token}} of a line")
                    logging.getLogger("bot").error("failed with %s", token)
                    raise RuntimeError(token)
                """)
            )
        )
        snap = await finished(manager)
        everything = "\n".join(line["text"] for line in snap["lines"])
        assert TOKEN not in everything and SECRET_PART not in everything
        assert everything.count("[token hidden]") >= 5

    async def test_something_written_to_the_error_output_without_logging_is_an_error(self, manager):
        await manager.start(request('import sys\nsys.stderr.write("something odd\\n")'))
        snap = await finished(manager)
        assert [(line["level"], line["source"]) for line in snap["lines"] if line["text"] == "something odd"] == [
            ("error", "python")
        ]

    async def test_control_characters_and_direction_overrides_are_taken_out(self, manager):
        await manager.start(request(r'print("a\x1b[31mred\x07‮evil‬\x00end")'))
        snap = await finished(manager)
        (line,) = texts(snap, "info", "print")
        assert line == "a[31mredevilend"

    async def test_arabic_and_emoji_are_left_alone(self, manager):
        await manager.start(request('print("مرحبا 👋 hello")'))
        snap = await finished(manager)
        assert texts(snap, "info", "print") == ["مرحبا 👋 hello"]

    async def test_a_line_that_is_far_too_long_is_cut_and_the_next_one_is_fine(self, manager):
        await manager.start(request('print("x" * 200000)\nprint("after")'))
        snap = await finished(manager)
        printed = texts(snap, "info", "print")
        assert len(printed) == 2 and printed[1] == "after"
        assert len(printed[0]) < manager_module.MAX_RECORD_CHARS + 20 and printed[0].endswith("(cut)")

    async def test_only_the_newest_messages_are_kept(self, manager, monkeypatch):
        monkeypatch.setattr(manager_module, "MAX_LINES", 20)
        monkeypatch.setattr(manager_module, "FLOOD_LINES_PER_SECOND", 10**6)
        await manager.start(request('for n in range(100):\n    print(f"line {n}")'))
        snap = await finished(manager)
        assert len(snap["lines"]) == 20
        assert texts(snap, "info", "print")[-1] == "line 99"
        assert snap["skipped"] == snap["lines"][0]["n"] - 1 > 0

    async def test_the_messages_have_numbers_and_times_that_only_go_up(self, manager):
        await manager.start(request("for n in range(5):\n    print(n)"))
        snap = await finished(manager)
        numbers = [line["n"] for line in snap["lines"]]
        times = [line["t"] for line in snap["lines"]]
        assert numbers == list(range(1, len(numbers) + 1))
        assert times == sorted(times) and abs(times[-1] - time.time()) < 30
        assert snap["next"] == numbers[-1]

    async def test_asking_for_what_is_new_gives_only_what_is_new(self, manager):
        await manager.start(request('print("one")\nimport time\ntime.sleep(0.5)\nprint("two")\ntime.sleep(60)'))
        first = await until(manager, lambda snap: "one" in texts(snap))
        newer = manager.snapshot(bot_id(), first["next"], first["run"])
        assert "one" not in texts(newer) and newer["reset"] is False and newer["run"] == first["run"]
        both = await until(manager, lambda snap: "two" in texts(snap))
        assert texts(manager.snapshot(bot_id(), first["next"], first["run"]), "info", "print") == ["two"]
        assert both["next"] > first["next"]

    async def test_a_bot_that_never_ran_has_no_state(self, manager):
        snap = manager.snapshot(bot_id(9), 0, None)
        assert snap["state"] == "none" and snap["lines"] == [] and snap["run"] is None
        assert manager.snapshot(bot_id(9), 5, "f" * 32)["reset"] is True

    async def test_errors_come_with_a_hint_but_ordinary_messages_do_not(self, manager):
        await manager.start(
            request(
                script("""
                    import logging
                    log = logging.getLogger("bot")
                    log.error("fluxer.errors.Forbidden: 403 MISSING_PERMISSIONS: Missing Permissions")
                    log.warning("Gateway connection error: cannot connect")
                    log.error("a mistake nobody has a hint for")
                    print("403 MISSING_PERMISSIONS in a print")
                    log.info("Forbidden fluxer.errors.Forbidden in an info message")
                """)
            )
        )
        snap = await finished(manager)
        hints = {line["text"]: line.get("hint", "") for line in snap["lines"] if line["source"] != "system"}
        assert "permission" in hints["fluxer.errors.Forbidden: 403 MISSING_PERMISSIONS: Missing Permissions"]
        assert "reach Fluxer" in hints["Gateway connection error: cannot connect"]
        assert hints["a mistake nobody has a hint for"] == ""
        assert hints["403 MISSING_PERMISSIONS in a print"] == ""
        assert hints["Forbidden fluxer.errors.Forbidden in an info message"] == ""


# ======================================================================================================= states
class TestHowARunGoes:
    async def test_it_starts_by_saying_so_and_ends_by_saying_so(self, manager):
        started = await manager.start(request('print("hi")'))
        assert started["state"] == "starting" and started["lines"][0]["text"] == "Starting your bot…"
        snap = await finished(manager)
        assert snap["lines"][0]["level"] == "system" and snap["lines"][-1]["text"] == "Your bot stopped."
        assert snap["state"] == "stopped" and snap["reason"] == "The bot finished by itself." and snap["exit_code"] == 0
        assert snap["started_at"] <= snap["ended_at"]

    async def test_fluxer_saying_ready_is_the_moment_it_is_running(self, manager):
        await manager.start(
            request(
                script("""
                    import logging, time
                    time.sleep(0.3)
                    logging.getLogger("fluxer.gateway").info("READY: session=abc, user=pingbot")
                    time.sleep(60)
                """)
            )
        )
        assert manager.snapshot(bot_id(), 0, None)["state"] == "starting"
        snap = await until(manager, lambda snap: snap["state"] == "running")
        assert snap["ready"] is True
        assert "@@sff ready" not in "\n".join(texts(snap))  # a marker for the runner, not a message for people

    async def test_it_counts_as_running_even_if_ready_is_never_said(self, manager, monkeypatch):
        monkeypatch.setattr(manager_module, "READY_FALLBACK_SECONDS", 1)
        await manager.start(request(SLEEP))
        snap = await until(manager, lambda snap: snap["state"] == "running")
        assert snap["ready"] is False

    async def test_a_bot_that_fails_has_crashed_and_the_reason_points_at_the_red_messages(self, manager):
        await manager.start(request('raise ValueError("boom")'))
        snap = await finished(manager)
        assert snap["state"] == "crashed" and snap["exit_code"] == 1
        assert snap["reason"] == manager_module.ERROR_REASON
        # the red message already says what happened, so the console does not say it a second time
        assert texts(snap, "system")[-1] == "Your bot stopped unexpectedly."
        assert texts(snap, "system").count(snap["reason"]) == 0

    async def test_a_bot_that_ends_with_a_code_and_no_error_says_the_code(self, manager):
        await manager.start(request("raise SystemExit(3)"))
        snap = await finished(manager)
        assert snap["state"] == "crashed" and snap["exit_code"] == 3 and not texts(snap, "error")
        assert snap["reason"] == "The bot ended on its own with the code 3."
        assert snap["lines"][-1]["text"] == snap["reason"]

    async def test_exit_zero_after_a_fatal_close_is_a_crash_not_a_success(self, manager):
        await manager.start(
            request(
                script("""
                    import logging
                    logging.getLogger("fluxer.gateway").error("Fatal close code 4004 (AUTHENTICATION_FAILED), not reconnecting")
                """)
            )
        )
        snap = await finished(manager)
        assert snap["state"] == "crashed" and snap["exit_code"] == 0 and "closed the connection" in snap["reason"]
        (error,) = texts(snap, "error")
        assert "token" in next(line["hint"] for line in snap["lines"] if line["text"] == error)

    async def test_a_bot_the_system_kills_is_a_crash_with_a_plain_reason(self, manager):
        await manager.start(request("import os, signal\nos.kill(os.getpid(), signal.SIGKILL)"))
        snap = await finished(manager)
        assert snap["state"] == "crashed" and snap["exit_code"] == -9 and "ran out of memory" in snap["reason"]

    async def test_a_process_the_bot_leaves_behind_does_not_keep_the_run_open(self, manager):
        await manager.start(request(LEAVES_ONE_BEHIND))
        leftover = await the_one_left_behind(manager)
        try:
            snap = await finished(manager, seconds=manager_module.DRAIN_SECONDS + 4)
            assert snap["state"] == "stopped" and snap["reason"] == "The bot finished by itself."
        finally:
            await put_an_end_to(leftover)

    async def test_the_run_folder_is_gone_when_the_run_is_over(self, manager, tmp_path):
        await manager.start(request('print("hi")'))
        await finished(manager)
        assert not (tmp_path / "runs" / bot_id()).exists()

    async def test_finished_runs_are_forgotten_after_a_while(self, manager, monkeypatch):
        await manager.start(request('print("hi")'))
        await finished(manager)
        monkeypatch.setattr(manager_module, "KEEP_FINISHED_SECONDS", -1)
        assert manager.snapshot(bot_id(), 0, None)["state"] == "none"


# ======================================================================================================= stopping
class TestStopping:
    async def test_stopping_is_asking_politely_so_the_bot_can_close_its_connection(self, manager):
        await manager.start(
            request(
                script("""
                    import time
                    print("up", flush=True)
                    try:
                        time.sleep(120)
                    except KeyboardInterrupt:
                        print("closing my connection", flush=True)
                """)
            )
        )
        await until(manager, lambda snap: "up" in texts(snap))
        started = time.monotonic()
        snap = await manager.stop(bot_id())
        assert time.monotonic() - started < 3
        assert snap["state"] == "stopped" and snap["reason"] == "You stopped the bot." and snap["exit_code"] == 0
        assert "closing my connection" in texts(snap) and snap["lines"][-1]["text"] == "Your bot stopped."

    async def test_stopping_a_bot_that_left_a_process_behind_ends_the_run_at_once(self, manager):
        await manager.start(request(LEAVES_ONE_BEHIND + "time.sleep(120)\n"))
        leftover = await the_one_left_behind(manager)
        try:
            started = time.monotonic()
            snap = await manager.stop(bot_id())
            # It takes as long as the last of the bot's output is waited for, and no longer: the grace period before the
            # bot is ended by force is not used up.
            assert time.monotonic() - started < manager_module.STOP_GRACE_SECONDS
            assert snap["state"] == "stopped" and snap["reason"] == "You stopped the bot." and snap["exit_code"] == 0
        finally:
            await put_an_end_to(leftover)

    async def test_a_bot_that_ignores_the_request_is_ended_after_a_few_seconds(self, manager, monkeypatch):
        monkeypatch.setattr(manager_module, "STOP_GRACE_SECONDS", 0.6)
        await manager.start(
            request(
                script("""
                    import signal, time
                    signal.signal(signal.SIGINT, signal.SIG_IGN)
                    print("not listening", flush=True)
                    while True:
                        time.sleep(0.05)
                """)
            )
        )
        await until(manager, lambda snap: "not listening" in texts(snap))
        started = time.monotonic()
        snap = await manager.stop(bot_id())
        assert 0.5 < time.monotonic() - started < 5
        assert snap["state"] == "stopped" and snap["exit_code"] == -9

    async def test_a_bot_stuck_in_a_loop_can_still_be_stopped(self, manager):
        await manager.start(request('print("busy", flush=True)\nwhile True:\n    pass'))
        await until(manager, lambda snap: "busy" in texts(snap))
        snap = await manager.stop(bot_id())
        assert snap["state"] == "stopped"

    async def test_stopping_what_is_not_running_is_fine(self, manager):
        assert (await manager.stop(bot_id(5)))["state"] == "none"
        await manager.start(request('print("hi")'))
        await finished(manager)
        again = await manager.stop(bot_id())
        assert again["state"] == "stopped" and again["reason"] == "The bot finished by itself."

    async def test_stopping_twice_at_once_is_fine(self, manager):
        await manager.start(request(SLEEP))
        first, second = await asyncio.gather(manager.stop(bot_id()), manager.stop(bot_id()))
        assert first["state"] == second["state"] == "stopped"

    async def test_starting_again_replaces_the_run_and_its_messages(self, manager):
        await manager.start(request('import os, time\nprint("pid", os.getpid(), flush=True)\ntime.sleep(120)'))
        old = await until(manager, lambda snap: any(t.startswith("pid") for t in texts(snap)))
        old_pid = int(next(t for t in texts(old) if t.startswith("pid")).split()[1])
        again = await manager.start(request('import os, time\nprint("pid", os.getpid(), flush=True)\ntime.sleep(120)'))
        assert again["run"] != old["run"]
        new = await until(manager, lambda snap: any(t.startswith("pid") for t in texts(snap)))
        new_pid = int(next(t for t in texts(new) if t.startswith("pid")).split()[1])
        assert new_pid != old_pid and not is_alive(old_pid) and is_alive(new_pid)
        assert texts(new, "info", "print") == [f"pid {new_pid}"]  # the old run's messages are gone
        stale = manager.snapshot(bot_id(), old["next"], old["run"])
        assert stale["reset"] is True and stale["run"] == new["run"]

    async def test_starting_without_replacing_leaves_a_running_bot_alone(self, manager):
        first = await manager.start(request(SLEEP))
        second = await manager.start(request(SLEEP), replace=False)
        assert second["run"] == first["run"] and second["already_running"] is True

    async def test_two_starts_at_the_same_moment_end_up_with_one_bot(self, manager):
        first, second = await asyncio.gather(
            manager.start(request(SLEEP), replace=False), manager.start(request(SLEEP), replace=False)
        )
        assert first["run"] == second["run"]
        assert len(manager.active()) == 1

    async def test_shutting_the_runner_down_stops_every_bot(self, manager):
        for n in (1, 2):
            await manager.start(request(SLEEP, n, owner=f"{n:032x}"))
        pids = [run.process.pid for run in manager.active()]
        await manager.shutdown()
        assert manager.active() == [] and not any(is_alive(pid) for pid in pids)


# ======================================================================================================= limits
class TestLimits:
    async def test_there_is_room_for_only_so_many_bots(self, manager):
        for n in (1, 2, 3):
            await manager.start(request(SLEEP, n, owner=f"{n:032x}"))
        with pytest.raises(Busy):
            await manager.start(request(SLEEP, 4, owner=f"{4:032x}"))
        await manager.stop(bot_id(1))
        await manager.start(request(SLEEP, 4, owner=f"{4:032x}"))

    async def test_one_person_can_run_only_so_many_at_a_time(self, manager):
        await manager.start(request(SLEEP, 1))
        await manager.start(request(SLEEP, 2))
        with pytest.raises(TooMany) as caught:
            await manager.start(request(SLEEP, 3))
        assert "2 bots" in caught.value.message
        await manager.start(request(SLEEP, 3, owner="c" * 32))  # somebody else is not affected
        await manager.start(request(SLEEP, 1))  # starting a bot again is not running one more
        await manager.stop(bot_id(2))
        await manager.start(request(SLEEP, 2))

    async def test_a_failed_start_leaves_no_trace_and_costs_no_place(self, manager):
        with pytest.raises(Refused):
            await manager.start(request(""))
        assert manager.active() == [] and manager.snapshot(bot_id(), 0, None)["state"] == "none"

    async def test_a_bot_that_hogs_the_processor_is_stopped_and_told_why(self, manager, monkeypatch):
        monkeypatch.setattr(manager_module, "CPU_WINDOW_SECONDS", 3)
        monkeypatch.setattr(manager_module, "CPU_BUSY_SHARE", 0.3)
        await manager.start(request('print("looping", flush=True)\nwhile True:\n    pass'))
        snap = await finished(manager, seconds=25)
        assert snap["state"] == "crashed" and "processor" in snap["reason"] and "wait" in snap["reason"]

    async def test_a_quiet_bot_is_left_alone(self, manager, monkeypatch):
        monkeypatch.setattr(manager_module, "CPU_WINDOW_SECONDS", 2)
        await manager.start(request(SLEEP))
        await asyncio.sleep(4.5)
        assert manager.snapshot(bot_id(), 0, None)["state"] in ("starting", "running")

    async def test_a_bot_that_writes_far_too_fast_is_stopped(self, manager, monkeypatch):
        monkeypatch.setattr(manager_module, "FLOOD_SECONDS_TO_STOP", 2)
        await manager.start(request('while True:\n    print("spam spam spam")'))
        snap = await finished(manager, seconds=25)
        assert snap["state"] == "crashed" and "hundreds of messages" in snap["reason"]
        assert len(snap["lines"]) <= manager_module.MAX_LINES
        assert any("faster than they can be shown" in text for text in texts(snap, "system"))

    async def test_a_bot_that_eats_memory_is_stopped(self, manager):
        await manager.start(request('data = b"x" * (900 * 1024 * 1024)\nimport time\ntime.sleep(60)'))
        snap = await finished(manager, seconds=30)
        # Either the process was not allowed the memory (MemoryError) or the runner noticed how much it held.
        assert snap["state"] == "crashed"
        assert any("MemoryError" in text for text in texts(snap, "error")) or "memory" in snap["reason"]

    async def test_a_bot_runs_for_a_limited_time(self, manager):
        manager._limits = sandbox.Limits(max_run_seconds=2)  # noqa: SLF001
        await manager.start(request(SLEEP))
        snap = await finished(manager, seconds=20)
        assert snap["state"] == "stopped" and "Press Run to start it again" in snap["reason"]


# ======================================================================================================= the sandbox
class TestInsideTheProcess:
    async def test_the_bot_sees_its_token_and_its_limits_and_nothing_else(self, manager, monkeypatch):
        for name in ("RUNNER_SECRET", "DATABASE_URL", "SECRET_KEY", "FLUXER_CLIENT_SECRET"):
            monkeypatch.setenv(name, "must-not-reach-the-bot")
        await manager.start(
            request(
                script(f"""
                    import json, os
                    print(json.dumps(sorted(os.environ)))
                    print(os.environ["FLUXER_TOKEN"] == {TOKEN!r})
                """)
            )
        )
        snap = await finished(manager)
        printed = texts(snap, "info", "print")
        names = json.loads(printed[0])
        allowed = {
            "FLUXER_TOKEN",
            "HOME",
            "LANG",
            "LC_CTYPE",  # Python sets this one itself when it starts in the C locale
            "PATH",
            "SFF_CPU_SECONDS",
            "SFF_FILE_MB",
            "SFF_MEMORY_MB",
            "SSL_CERT_DIR",  # the certificates a computer trusts, when it has been told where they are
            "SSL_CERT_FILE",
            "TMPDIR",
        }
        assert set(names) <= allowed
        assert {"FLUXER_TOKEN", "PATH", "HOME"} <= set(names)
        assert printed[1] == "True"
        assert "must-not-reach-the-bot" not in "\n".join(texts(snap))

    async def test_the_limits_are_in_force(self, manager):
        await manager.start(
            request(
                script("""
                    import resource
                    for name in ("CORE", "NOFILE", "FSIZE", "DATA", "CPU"):
                        print(name, resource.getrlimit(getattr(resource, "RLIMIT_" + name))[0])
                """)
            )
        )
        got = dict(text.split() for text in texts(await finished(manager), "info", "print"))
        mb = 1024 * 1024
        assert got["CORE"] == "0" and got["NOFILE"] == "256"
        assert got["FSIZE"] == str(8 * mb) and got["DATA"] == str(384 * mb) and got["CPU"] == "3600"

    async def test_python_runs_isolated_with_the_run_folder_off_its_path(self, manager):
        await manager.start(
            request(
                "import sys\nprint(sys.flags.isolated, sys.flags.utf8_mode, sys.flags.dont_write_bytecode)\nprint(sys.path[0])"
            )
        )
        flags, first = texts(await finished(manager), "info", "print")
        assert flags == "1 1 1" and "runs" not in first

    async def test_the_folder_holds_main_py_and_the_files_in_assets_and_is_private(self, manager):
        await manager.start(
            request(
                script("""
                    import os
                    from pathlib import Path
                    here = Path(__file__).parent
                    print(oct(os.stat(here).st_mode & 0o777), oct(os.stat(__file__).st_mode & 0o777))
                    print(sorted(p.name for p in here.iterdir()))
                    print((here / "assets" / "data.json").read_text())
                    print((here / "assets" / "notes.txt").read_bytes())
                """),
                files=[("data.json", b'{"a": 1}'), ("notes.txt", b"hello")],
            )
        )
        modes, listing, data, notes = texts(await finished(manager), "info", "print")
        assert modes == "0o700 0o600" and listing == "['assets', 'main.py']"
        assert data == '{"a": 1}' and notes == "b'hello'"

    async def test_a_bot_without_files_has_no_assets_folder(self, manager):
        await manager.start(request("import os\nprint(os.listdir(os.path.dirname(__file__)))"))
        assert texts(await finished(manager), "info", "print") == ["['main.py']"]

    async def test_leftovers_of_an_earlier_runner_are_cleared_but_only_bot_folders(self, tmp_path):
        base = tmp_path / "runs"
        (base / bot_id(1)).mkdir(parents=True)
        (base / bot_id(1) / "main.py").write_text("old")
        (base / "keep-me").mkdir()
        (base / ("f" * 32)).write_text("a file, not a folder")
        RunnerManager(Settings(_env_file=None), check=lambda code: None, base=base)
        assert not (base / bot_id(1)).exists()
        assert (base / "keep-me").is_dir() and (base / ("f" * 32)).is_file()

    def test_a_bot_is_stopped_if_the_runner_disappears(self, tmp_path):
        folder = tmp_path / "x"
        folder.mkdir()
        (folder / "main.py").write_text("import time\ntime.sleep(300)\n")
        # A stand-in for the runner: it starts the bot the way the manager does (telling it which process the runner
        # is) and ends straight away, long before the bot has finished starting.
        parent_code = textwrap.dedent(f"""
            import os, subprocess, sys
            child = subprocess.Popen({sandbox.command(folder / "main.py")!r}, start_new_session=True,
                                     env={{**os.environ, "SFF_PARENT_PID": str(os.getpid())}},
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(child.pid, flush=True)
        """)
        parent = subprocess.run(  # noqa: S603 - our own interpreter, running a script written above
            [sys.executable, "-c", parent_code], capture_output=True, text=True, timeout=30, check=True
        )
        pid = int(parent.stdout.strip())
        deadline = time.monotonic() + 8
        while is_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if is_alive(pid):
            os.kill(pid, signal.SIGKILL)
            pytest.fail("the bot kept running after the process that started it was gone")

    @pytest.mark.skipif(
        not CAN_GIVE_USERS, reason="needs the runner to be started as root, with the usual capabilities"
    )
    async def test_each_bot_can_run_as_a_user_of_its_own(self, shared_runs_folder, monkeypatch):
        python = "/usr/bin/python3"
        if not os.access(python, os.X_OK) or not os.access(sandbox.CHILD, os.R_OK):
            pytest.skip("no interpreter that every user can run")
        monkeypatch.setattr(sandbox.sys, "executable", python)
        base = shared_runs_folder
        settings = Settings(_env_file=None, runner_mode="local", runner_uid_base=41000, runner_max_per_user=5)
        made = RunnerManager(settings, check=lambda code: None, base=base)
        try:
            code = script("""
                import os
                print(os.getuid(), os.getgid(), os.getgroups())
                try:
                    open(f"/proc/{os.getppid()}/environ", "rb").read()
                    print("could read the runner's environment")
                except OSError:
                    print("cannot read the runner's environment")
                try:
                    print(sorted(os.listdir(os.path.dirname(os.path.dirname(__file__)))))
                except OSError:
                    print("cannot list the folders of the other bots")
            """)
            await made.start(request(code, 1))
            await made.start(request("import time\ntime.sleep(60)", 2))
            first = await finished(made, 1)
            uids, environment, listing = texts(first, "info", "print")
            assert uids == "41000 41000 []"
            assert environment == "cannot read the runner's environment"
            assert listing == "cannot list the folders of the other bots"
            other = made.run_of(bot_id(2))
            assert other is not None and other.slot == 1  # the second bot has the next user id
            assert mode_of(base) == 0o711  # walk through, but don't list
        finally:
            await made.shutdown()

    @pytest.mark.skipif(
        not CAN_GIVE_USERS, reason="needs the runner to be started as root, with the usual capabilities"
    )
    async def test_what_a_bot_leaves_running_ends_with_it(self, shared_runs_folder, monkeypatch):
        python = "/usr/bin/python3"
        if not os.access(python, os.X_OK) or not os.access(sandbox.CHILD, os.R_OK):
            pytest.skip("no interpreter that every user can run")
        monkeypatch.setattr(sandbox.sys, "executable", python)
        settings = Settings(_env_file=None, runner_mode="local", runner_uid_base=41000)
        made = RunnerManager(settings, check=lambda code: None, base=shared_runs_folder)
        try:
            await made.start(
                request(
                    script("""
                        import os, time
                        pid = os.fork()
                        if pid == 0:
                            os.setsid()  # a group of its own: the stop signal for the bot's group never reaches it
                            time.sleep(300)
                            os._exit(0)
                        print(pid, flush=True)
                        time.sleep(300)
                    """)
                )
            )
            leftover = int(texts(await until(made, lambda snap: texts(snap, "info", "print")), "info", "print")[0])
            assert is_alive(leftover)
            await made.stop(bot_id())
            assert await gone(leftover), "a process the bot started went on running after the bot was stopped"
        finally:
            await made.shutdown()

    async def test_a_runner_that_is_root_without_the_capabilities_does_not_start(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sandbox, "can_switch_users", lambda: True)
        monkeypatch.setattr(sandbox, "missing_capabilities", lambda: ["KILL", "SETUID"])
        settings = Settings(_env_file=None, runner_mode="local", runner_uid_base=41000)
        with pytest.raises(RuntimeError, match="KILL, SETUID"):
            RunnerManager(settings, check=lambda code: None, base=tmp_path / "runs")

    async def test_without_root_bots_share_the_runners_user(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sandbox, "can_switch_users", lambda: False)
        monkeypatch.setattr(sandbox, "missing_capabilities", lambda: ["KILL"])  # nobody asks: it only matters as root
        settings = Settings(_env_file=None, runner_mode="local", runner_uid_base=41000)
        made = RunnerManager(settings, check=lambda code: None, base=tmp_path / "runs")
        assert made.user_per_bot is False

    @pytest.mark.skipif(
        not CAN_GIVE_USERS, reason="needs the runner to be started as root, with the usual capabilities"
    )
    def test_a_bot_gets_its_own_user_when_the_runner_uses_uvloop_too(self, shared_runs_folder, monkeypatch):
        """uvicorn[standard] runs the real runner on uvloop, which can't start a process as another user by itself."""
        uvloop = pytest.importorskip("uvloop")
        python = "/usr/bin/python3"
        if not os.access(python, os.X_OK) or not os.access(sandbox.CHILD, os.R_OK):
            pytest.skip("no interpreter that every user can run")
        monkeypatch.setattr(sandbox.sys, "executable", python)

        async def go() -> dict:
            assert "uvloop" in type(asyncio.get_running_loop()).__module__
            settings = Settings(_env_file=None, runner_mode="local", runner_uid_base=41000)
            made = RunnerManager(settings, check=lambda code: None, base=shared_runs_folder)
            try:
                await made.start(request("import os\nprint(os.getuid(), os.getgid(), os.getgroups())\n"))
                return await finished(made)
            finally:
                await made.shutdown()

        assert texts(uvloop.run(go()), "info", "print") == ["41000 41000 []"]


# ======================================================================================================= becoming a user
def run_child(where: Path, main_py: str, run_as: str | None, *, python: str = "/usr/bin/python3"):
    """``child.py`` started the way the manager starts it, as root, for a file in a folder everybody can read."""
    folder = where / "bot"
    folder.mkdir(mode=0o755)
    (folder / "main.py").write_text(main_py)
    (folder / "main.py").chmod(0o644)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(folder), "SFF_PARENT_PID": str(os.getpid())}
    if run_as is not None:
        env["SFF_RUN_AS"] = run_as
    return subprocess.run(  # noqa: S603 - our own child.py, with the interpreter of the system
        [python, "-I", "-u", "-B", str(sandbox.CHILD), str(folder / "main.py")],
        env=env,
        cwd=folder,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.skipif(not CAN_GIVE_USERS, reason="needs the runner to be started as root, with the usual capabilities")
class TestBecomingTheBotsUser:
    @pytest.fixture(autouse=True)
    def _an_interpreter_every_user_can_run(self):
        if not os.access("/usr/bin/python3", os.X_OK):
            pytest.skip("no interpreter that every user can run")

    def test_the_bot_runs_as_that_user_with_no_groups_and_no_way_back(self, shared_runs_folder):
        done = run_child(
            shared_runs_folder,
            script("""
                import os
                print(os.getuid(), os.geteuid(), os.getgid(), os.getegid(), os.getgroups())
                print("SFF_RUN_AS" in os.environ)
                try:
                    os.setuid(0)
                    print("back to root")
                except PermissionError:
                    print("cannot go back")
                print(open("/proc/self/status").read().count("Cap"), "capability lines")
                print([line for line in open("/proc/self/status") if line.startswith(("CapEff", "CapPrm"))])
            """),
            "41234:41235",
        )
        lines = done.stdout.splitlines()
        assert done.returncode == 0, done.stderr
        assert lines[0] == "41234 41234 41235 41235 []"
        assert lines[1] == "False" and lines[2] == "cannot go back"
        assert "0000000000000000" in lines[4] and lines[4].count("0000000000000000") == 2  # no capabilities are left

    @pytest.mark.parametrize(
        "wrong",
        [
            "x",
            "41234",
            "41234:",
            ":41234",
            "a:b",
            "41234:41235:1",
            "-1:-1",
            "0:0",
            "0:41235",
            "41234:0",
            "9" * 30 + ":1",
        ],
    )
    def test_a_bot_is_never_run_as_root_when_the_change_cannot_be_made(self, shared_runs_folder, wrong):
        done = run_child(shared_runs_folder, 'open("ran.txt", "w").write("the bot ran")\n', wrong)
        assert done.returncode == 1
        assert done.stderr.startswith("ERROR\tsff\tThe runner could not start your bot as a user of its own")
        assert not (shared_runs_folder / "bot" / "ran.txt").exists()

    def test_without_the_variable_nothing_changes(self, shared_runs_folder):
        done = run_child(shared_runs_folder, "import os\nprint(os.getuid())\n", None)
        assert done.returncode == 0 and done.stdout.strip() == "0"


# ======================================================================================================= the code check
class TestTheCodeCheck:
    async def test_code_that_fails_the_safety_check_is_never_started(self, tmp_path):
        made = RunnerManager(Settings(_env_file=None), base=tmp_path / "runs")  # the real check
        with pytest.raises(Refused) as caught:
            await made.start(request('import subprocess\nsubprocess.run(["id"])\n'))
        assert "safety check" in caught.value.message
        assert made.active() == []
        assert not (tmp_path / "runs" / bot_id()).exists()


# ======================================================================================================= the helpers
class TestSandboxHelpers:
    def test_limits_stay_within_sensible_bounds(self):
        low = sandbox.Limits(memory_mb=1, cpu_seconds=0, file_mb=0, max_run_seconds=1).clamped()
        assert (low.memory_mb, low.cpu_seconds, low.file_mb, low.max_run_seconds) == (128, 60, 1, 60)
        high = sandbox.Limits(memory_mb=10**6, cpu_seconds=10**9, file_mb=10**6, max_run_seconds=10**9).clamped()
        assert (high.memory_mb, high.cpu_seconds, high.file_mb) == (4096, 24 * 3600, 64)

    def test_the_command_runs_python_isolated(self):
        command = sandbox.command(Path("/x/main.py"))
        assert command[:2] == [sys.executable, "-I"] and "-u" in command and "-B" in command
        assert command[-2:] == [str(sandbox.CHILD), "/x/main.py"] and sandbox.CHILD.is_file()

    def test_the_environment_is_built_from_scratch(self, monkeypatch):
        monkeypatch.setenv("RUNNER_SECRET", "no")
        monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/ca.pem")
        env = sandbox.environment(TOKEN, sandbox.Limits(), Path("/runs/x"), "http://127.0.0.1:1/v1")
        assert env["FLUXER_TOKEN"] == TOKEN and env["SFF_FLUXER_API_URL"] == "http://127.0.0.1:1/v1"
        assert env["HOME"] == env["TMPDIR"] == "/runs/x" and env["SSL_CERT_FILE"] == "/etc/ssl/ca.pem"
        assert "RUNNER_SECRET" not in env
        assert "SFF_FLUXER_API_URL" not in sandbox.environment(TOKEN, sandbox.Limits(), Path("/x"), None)

    @pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")
    def test_processor_and_memory_use_can_be_read(self):
        assert sandbox.cpu_seconds_used(os.getpid()) > 0
        assert sandbox.memory_mb_used(os.getpid()) > 5
        assert sandbox.cpu_seconds_used(2**22 + 1) is None and sandbox.memory_mb_used(2**22 + 1) is None

    def test_a_run_folder_can_be_written_twice(self, tmp_path):
        first = sandbox.write_run_folder(tmp_path / "runs", bot_id(), "one", [("a.txt", b"1")])
        second = sandbox.write_run_folder(tmp_path / "runs", bot_id(), "two", [])
        assert first == second and (second / "main.py").read_text() == "two" and not (second / "assets").exists()

    def test_only_a_bot_with_a_user_of_its_own_is_told_to_become_that_user_and_gets_a_process_limit(self):
        shared = sandbox.environment(TOKEN, sandbox.Limits(), Path("/x"), None)
        own = sandbox.environment(TOKEN, sandbox.Limits(), Path("/x"), None, run_as=(41000, 41001))
        assert "SFF_MAX_PROCESSES" not in shared and "SFF_RUN_AS" not in shared
        assert own["SFF_MAX_PROCESSES"] == str(sandbox.MAX_PROCESSES) and own["SFF_RUN_AS"] == "41000:41001"


# ======================================================================================================= root, in a container
def pretend_capabilities(monkeypatch, held: str | None) -> None:
    """What /proc/self/status says about this process (``None``: it has no such line)."""
    lines = ["Name:\tpython", "CapInh:\t0000000000000000"]
    if held is not None:
        lines += [f"CapPrm:\t{held}", f"CapEff:\t{held}", f"CapBnd:\t{held}"]
    text = "\n".join(lines) + "\n"
    monkeypatch.setattr(sandbox, "_read", lambda path: text if path == "/proc/self/status" else None)


class TestWhatRootNeedsToGiveBotsTheirOwnUsers:
    @pytest.mark.parametrize(
        ("held", "missing"),
        [
            ("00000000000000e3", []),  # the five and nothing else: docker run --cap-drop=ALL --cap-add=...
            ("00000000a80425fb", []),  # Docker's usual set
            ("000001ffffffffff", []),  # everything
            ("00000000000000c1", ["DAC_OVERRIDE", "KILL"]),  # setuid, setgid and chown only
            ("00000000000000c3", ["KILL"]),
            ("0000000000000000", ["CHOWN", "DAC_OVERRIDE", "KILL", "SETGID", "SETUID"]),
        ],
    )
    def test_the_missing_capabilities_are_named(self, monkeypatch, held, missing):
        pretend_capabilities(monkeypatch, held)
        assert sandbox.missing_capabilities() == missing

    @pytest.mark.parametrize("held", [None, "not-hex", ""])
    def test_when_it_cannot_tell_it_does_not_complain(self, monkeypatch, held):
        pretend_capabilities(monkeypatch, held)
        assert sandbox.missing_capabilities() == []
        sandbox.require_user_switching()

    def test_the_error_says_what_to_add(self, monkeypatch):
        pretend_capabilities(monkeypatch, "00000000000000c3")
        with pytest.raises(RuntimeError) as caught:
            sandbox.require_user_switching()
        assert (
            "KILL" in str(caught.value) and "--cap-add" in str(caught.value) and "RUNNER_UID_BASE" in str(caught.value)
        )

    def test_the_real_capabilities_of_this_process_can_be_read(self):
        if not Path("/proc/self/status").exists():
            pytest.skip("needs /proc")
        assert set(sandbox.missing_capabilities()) <= set(sandbox.USER_SWITCHING_CAPABILITIES)

    def test_the_sweep_never_touches_root_or_the_runner_itself(self, monkeypatch):
        killed = []
        monkeypatch.setattr(sandbox.os, "kill", lambda pid, sig: killed.append(pid))
        assert sandbox.kill_everything_of(0) == 0
        assert sandbox.kill_everything_of(-1) == 0
        assert sandbox.kill_everything_of(os.getuid()) == 0
        assert sandbox.kill_everything_of(os.geteuid()) == 0
        assert killed == []

    @pytest.mark.skipif(
        not CAN_GIVE_USERS, reason="needs the runner to be started as root, with the usual capabilities"
    )
    def test_the_sweep_ends_the_processes_of_one_user_and_no_others(self):
        def sleeper(uid: int) -> subprocess.Popen:
            return subprocess.Popen(  # noqa: S603 - a plain sleep, as another user
                ["/bin/sleep", "60"], user=uid, group=uid, extra_groups=[], start_new_session=True
            )

        target, other, same_again = sleeper(41901), sleeper(41902), sleeper(41901)
        try:
            time.sleep(0.2)
            assert sandbox.kill_everything_of(41901) == 2
            assert target.wait(timeout=5) == -signal.SIGKILL and same_again.wait(timeout=5) == -signal.SIGKILL
            assert other.poll() is None
        finally:
            for process in (target, other, same_again):
                if process.poll() is None:
                    process.kill()
                process.wait()
