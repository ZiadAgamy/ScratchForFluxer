"""Starts, watches and stops bots on this machine, and keeps what they write.

There is one ``RunnerManager`` per process. It remembers the latest run of each bot (its state and its messages), and at
most ``runner_max_concurrent`` bots run at once. What a bot writes is read line by line, labelled (info, warning, error),
cleaned (no token, no folder names) and kept in a small buffer that the website polls for new lines.

A run ends in one of two ways. ``stopped`` means it ended the way it was meant to (the person stopped it, or the bot
finished by itself). ``crashed`` means it ended because of an error or because it went over a limit; ``reason`` then says
why, in a sentence written for a beginner.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import codecheck
from ..config import Settings
from . import sandbox
from .protocol import Busy, Refused, StartRequest, TooMany, Unavailable, validate_start

log = logging.getLogger("scratch_fluxer.runner")

# --------------------------------------------------------------------------------------------- what is kept
MAX_LINES = 1000  # messages kept per run (older ones are dropped)
MAX_RECORD_CHARS = 8000  # one message, traceback included
MAX_KEPT_CHARS = 1_500_000  # all the messages of one run together
MAX_RAW_LINE = 16_000  # bytes read of a single line before the rest of it is thrown away
FLOOD_LINES_PER_SECOND = 300
FLOOD_SECONDS_TO_STOP = 8

# --------------------------------------------------------------------------------------------- timing
READY_FALLBACK_SECONDS = 12  # "running" even if Fluxer's ready message was never seen
STOP_GRACE_SECONDS = 5
KILL_WAIT_SECONDS = 5
TICK_SECONDS = 1.0
CPU_WINDOW_SECONDS = 25
CPU_BUSY_SHARE = 0.9
KEEP_FINISHED_SECONDS = 3600
DRAIN_SECONDS = 3
EXIT_POLL_SECONDS = 0.05  # how often a bot that may have ended is looked at (see ``_exit_code``)

HIDDEN = "[token hidden]"
READY_MARK = "@@sff ready"
LEVELS = {"DEBUG": "info", "INFO": "info", "WARNING": "warning", "ERROR": "error", "CRITICAL": "error"}
RECORD_START = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)\t([A-Za-z0-9_.\-]{1,60})\t(.*)$")
_UNWANTED = re.compile("[\x00-\x08\x0b-\x1f\x7f‪-‮⁦-⁩]")  # control characters and bidi overrides
_LIBRARY_PATH = re.compile(r'File "[^"\n]*/(?:lib/python3\.\d+/)?(?:site-packages|dist-packages)/')
_STDLIB_PATH = re.compile(r'File "[^"\n]*/lib/python3\.\d+/')

#: Plain-language help for the errors a new developer is most likely to meet, matched against a message's text.
HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"fluxer\.errors\.Unauthorized|\b401 [A-Z_]+:|Fatal close code 4004|AUTHENTICATION_FAILED"),
        "Fluxer did not accept the bot's token. Open My bots, choose the bot's menu and replace its token.",
    ),
    (
        re.compile(r"Fatal close code 401[34]|INVALID_INTENTS|DISALLOWED_INTENTS"),
        "Fluxer will not let this bot use the features it asked for. Check the bot's settings on Fluxer.",
    ),
    (
        re.compile(r"fluxer\.errors\.Forbidden|\b403 [A-Z_]+:"),
        "Fluxer refused because the bot is missing a permission. Check the bot's role and the channel's permissions.",
    ),
    (
        re.compile(r"fluxer\.errors\.NotFound|\b404 [A-Z_]+:"),
        "Fluxer could not find what the bot asked for (a channel, a message or a person). "
        "It may have been deleted, or the bot may not be able to see it.",
    ),
    (
        re.compile(r"fluxer\.errors\.RateLimited|[Rr]ate limit"),
        "The bot is sending too fast, so Fluxer makes it wait. Add a “wait” block or send less.",
    ),
    (
        re.compile(r"Gateway connection error|ClientConnectorError|Cannot connect to host|Connection error"),
        "The bot could not reach Fluxer just now. It keeps trying by itself.",
    ),
    (
        re.compile(r"\bMemoryError\b"),
        "The bot ran out of memory. A list that keeps growing is the usual cause.",
    ),
)

ERROR_REASON = "The bot stopped because of an error. The red messages above say what went wrong."

#: Why the manager stopped a run itself -> (state, what the person is told)
REASONS: dict[str, tuple[str, str]] = {
    "you": ("stopped", "You stopped the bot."),
    "again": ("stopped", "Stopped, to start again."),
    "removed": ("stopped", "This bot was removed, so it was stopped."),
    "shutdown": ("stopped", "The runner is shutting down, so the bot was stopped."),
    "time": (
        "stopped",
        "Bots run for a few hours at a time, so this one was stopped. Press Run to start it again.",
    ),
    "cpu": (
        "crashed",
        "The bot was using all of its processor time, so it was stopped. "
        "A loop with no “wait” block inside is the usual cause.",
    ),
    "memory": ("crashed", "The bot used far more memory than it is allowed, so it was stopped."),
    "flood": (
        "crashed",
        "The bot was writing hundreds of messages every second, so it was stopped. "
        "A loop with no “wait” block inside is the usual cause.",
    ),
}


def hint_for(level: str, source: str, text: str) -> str:
    if level == "info" or source == "print":
        return ""
    for pattern, hint in HINTS:
        if pattern.search(text):
            return hint
    return ""


@dataclass
class Line:
    n: int
    t: float
    level: str  # info | warning | error | system
    source: str  # print | bot | fluxer.gateway | ... | system
    text: str
    hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"n": self.n, "t": round(self.t, 3), "level": self.level, "source": self.source}
        out["text"] = self.text
        if self.hint:
            out["hint"] = self.hint
        return out


class Run:
    """One start of one bot, from the moment it was asked for until its messages are forgotten."""

    def __init__(self, bot_id: str, owner: str, token: str, limits: sandbox.Limits) -> None:
        self.id = uuid.uuid4().hex
        self.bot_id = bot_id
        self.owner = owner
        self.limits = limits
        self.state = "starting"  # starting | running | stopping | stopped | crashed
        self.reason = ""
        self.exit_code: int | None = None
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.ready = False
        self.stop_reason: str | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.directory: Path | None = None
        self.slot: int | None = None
        self.exited = asyncio.Event()
        self.tasks: list[asyncio.Task[Any]] = []
        self.lines: deque[Line] = deque()
        self.next_n = 1
        self._kept = 0
        self._secrets = sorted({part for part in (token, token.split(".", 1)[-1]) if len(part) >= 8}, key=len)[::-1]
        self._pending: list[str] | None = None  # [level, source, text] of the record still being read
        self._folder = ""
        self.second_lines = 0
        self.flood_streak = 0
        self.fatal_seen = False
        self.error_seen = False
        self._cpu: deque[tuple[float, float]] = deque()

    # ------------------------------------------------------------------------------- state
    @property
    def alive(self) -> bool:
        return self.state in ("starting", "running", "stopping")

    def use_folder(self, directory: Path) -> None:
        self.directory = directory
        self._folder = str(directory) + os.sep

    # ------------------------------------------------------------------------------- messages
    def tidy(self, text: str) -> str:
        """No token, no folder names on the runner, no control characters."""
        for secret in self._secrets:
            text = text.replace(secret, HIDDEN)
        if self._folder:
            text = text.replace(self._folder, "")
        text = _LIBRARY_PATH.sub('File "', text)
        text = _STDLIB_PATH.sub('File "', text)
        return _UNWANTED.sub("", text)

    def add(self, level: str, source: str, text: str, hint: str = "") -> None:
        text = self.tidy(text)
        if len(text) > MAX_RECORD_CHARS:
            text = text[:MAX_RECORD_CHARS] + "… (cut)"
        self.lines.append(Line(self.next_n, time.time(), level, source, text, hint))
        self.next_n += 1
        self._kept += len(text)
        while len(self.lines) > MAX_LINES or self._kept > MAX_KEPT_CHARS:
            self._kept -= len(self.lines.popleft().text)

    def say(self, text: str) -> None:
        """A line from the runner itself (starting, stopping), not from the bot."""
        self.add("system", "system", text)

    def from_bot(self, level: str, source: str, text: str) -> None:
        self.second_lines += 1
        if self.second_lines > FLOOD_LINES_PER_SECOND:
            if self.second_lines == FLOOD_LINES_PER_SECOND + 1:
                self.say("Your bot is writing messages faster than they can be shown. Some are skipped.")
            return
        if "Fatal close code" in text:
            self.fatal_seen = True
        if level == "error":
            self.error_seen = True
        self.add(level, source, text, hint_for(level, source, text))

    def commit(self) -> None:
        """The record that was being read from the error output is complete."""
        if self._pending is not None:
            level, source, text = self._pending
            self._pending = None
            self.from_bot(level, source, text)

    def pending(self) -> bool:
        return self._pending is not None

    def feed(self, stream: str, raw: bytes) -> None:
        """One complete line from the bot's normal output (``out``) or error output (``err``)."""
        text = _UNWANTED.sub("", raw.decode("utf-8", "replace").rstrip("\r"))
        if stream == "out":
            self.commit()
            self.from_bot("info", "print", text)
            return
        if text == READY_MARK:
            self.mark_ready()
            return
        start = RECORD_START.match(text)
        if start:
            self.commit()
            level, name, message = start.groups()
            self._pending = [LEVELS[level], name, message]
        elif text.startswith("    ") and self._pending is not None:
            self._pending[2] += "\n" + text[4:]
        else:
            self.commit()
            self.from_bot("error", "python", text)  # something written to the error output outside of logging

    def mark_ready(self) -> None:
        self.ready = True
        if self.state == "starting":
            self.state = "running"

    def forget_secret(self) -> None:
        self._secrets = []

    # ------------------------------------------------------------------------------- watching
    def processor_share(self, now: float, used: float) -> float | None:
        """Share of one processor the bot used over the last ``CPU_WINDOW_SECONDS`` (``None`` until there is a window)."""
        self._cpu.append((now, used))
        while len(self._cpu) > 2 and now - self._cpu[1][0] >= CPU_WINDOW_SECONDS:
            self._cpu.popleft()
        first = self._cpu[0]
        span = now - first[0]
        if span < CPU_WINDOW_SECONDS * 0.8:
            return None
        return (used - first[1]) / span


class RunnerManager:
    def __init__(
        self,
        settings: Settings,
        *,
        check: Callable[[str], None] = codecheck.check,
        base: Path | None = None,
    ) -> None:
        self.settings = settings
        self._check = check
        self._base = base or settings.runner_data_path
        self._limits = sandbox.Limits(
            memory_mb=settings.runner_memory_mb, max_run_seconds=settings.runner_max_run_seconds
        ).clamped()
        self._runs: dict[str, Run] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        default_api = Settings.model_fields["fluxer_api_url"].default
        self._api_url = settings.fluxer_api_url if settings.fluxer_api_url != default_api else None
        self._uid_base = settings.runner_uid_base if sandbox.can_switch_users() else None
        if settings.runner_uid_base is not None and self._uid_base is None:
            log.warning("RUNNER_UID_BASE is set, but the runner isn't running as root, so bots share its user.")
        if self._uid_base is not None:
            sandbox.require_user_switching()
        sandbox.clear_old_folders(self._base)

    # ------------------------------------------------------------------------------- looking
    @property
    def user_per_bot(self) -> bool:
        """Whether every running bot has a user id of its own (so it can't read the runner or the other bots)."""
        return self._uid_base is not None

    def run_of(self, bot_id: str) -> Run | None:
        return self._runs.get(bot_id)

    def active(self) -> list[Run]:
        return [run for run in self._runs.values() if run.alive]

    def snapshot(self, bot_id: str, after: int = 0, known: str | None = None) -> dict[str, Any]:
        """The state of the latest run of ``bot_id`` and the messages after number ``after``."""
        self._sweep()
        return view(self._runs.get(bot_id), after, known)

    # ------------------------------------------------------------------------------- starting
    async def start(self, request: StartRequest, *, replace: bool = True) -> dict[str, Any]:
        """Start the bot. With ``replace`` a copy that is already running is stopped first; without it the copy that is
        running is left alone and described (``already_running``)."""
        validate_start(request, self.settings)
        try:
            await asyncio.to_thread(self._check, request.code)
        except codecheck.UnsafeCode as exc:
            log.error("refused to run a bot: %s", exc)
            raise Refused("This code failed the safety check, so it was not run.") from None
        async with self._lock_for(request.bot_id):
            self._sweep()
            current = self._runs.get(request.bot_id)
            if current is not None and current.alive:
                if not replace:
                    return {**view(current, 0, None), "already_running": True}
                await self._terminate(current, "again")
            run = self._admit(request)
            try:
                await self._launch(run, request)
            except BaseException:
                self._runs.pop(request.bot_id, None)
                raise
            return view(run, 0, None)

    def _admit(self, request: StartRequest) -> Run:
        others = [run for run in self.active() if run.bot_id != request.bot_id]
        if len(others) >= self.settings.runner_max_concurrent:
            raise Busy("The runner is full right now. Try again in a few minutes.")
        mine = sum(1 for run in others if run.owner == request.owner)
        if mine >= self.settings.runner_max_per_user:
            many = self.settings.runner_max_per_user
            raise TooMany(f"You can run {many} bot{'' if many == 1 else 's'} at the same time. Stop one of them first.")
        run = Run(request.bot_id, request.owner, request.token, self._limits)
        self._runs[request.bot_id] = run
        return run

    async def _launch(self, run: Run, request: StartRequest) -> None:
        ids = None
        if self._uid_base is not None:
            used = {other.slot for other in self.active() if other.slot is not None}
            run.slot = next(slot for slot in range(len(used) + 1) if slot not in used)
            ids = (self._uid_base + run.slot, self._uid_base + run.slot)
        try:
            directory = await asyncio.to_thread(
                sandbox.write_run_folder, self._base, request.bot_id, request.code, request.files, ids
            )
            run.use_folder(directory)
            run.say("Starting your bot…")
            run.process = await asyncio.create_subprocess_exec(
                *sandbox.command(directory / "main.py"),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=directory,
                env=sandbox.environment(request.token, run.limits, directory, self._api_url, run_as=ids),
                start_new_session=True,
            )
        except OSError as exc:
            log.error("could not start a bot process: %s", exc)
            self._cleanup(run)
            raise Unavailable("The bot could not be started on this runner. Please try again later.") from exc
        process = run.process
        assert process.stdout is not None and process.stderr is not None
        pumps = [
            asyncio.create_task(self._pump(run, process.stdout, "out")),
            asyncio.create_task(self._pump(run, process.stderr, "err")),
        ]
        run.tasks = [
            *pumps,
            asyncio.create_task(self._reap(run, pumps)),
            asyncio.create_task(self._supervise(run)),
        ]

    # ------------------------------------------------------------------------------- reading what the bot writes
    async def _pump(self, run: Run, stream: asyncio.StreamReader, name: str) -> None:
        buffer = b""
        skipping = False  # inside a line that was too long: its rest is thrown away
        try:
            while True:
                wait = 0.05 if name == "err" and run.pending() else None
                try:
                    chunk = await asyncio.wait_for(stream.read(65536), wait)
                except asyncio.TimeoutError:
                    run.commit()
                    continue
                if not chunk:
                    break
                *lines, buffer = (buffer + chunk).split(b"\n")
                for line in lines:
                    if skipping:
                        skipping = False
                        continue
                    run.feed(name, line[:MAX_RAW_LINE])
                if len(buffer) > MAX_RAW_LINE:
                    if not skipping:
                        run.feed(name, buffer[:MAX_RAW_LINE] + b" [line cut]")
                        skipping = True
                    buffer = b""
            if buffer and not skipping:
                run.feed(name, buffer)
        finally:
            if name == "err":
                run.commit()

    @staticmethod
    async def _exit_code(process: asyncio.subprocess.Process) -> int:
        """The exit code, as soon as the process itself has ended.

        ``process.wait()`` is not used here: on Python 3.11 and 3.12 it also waits until every pipe of the process has
        been closed, and a process that the bot started and left running holds them open. The run would never end, and
        the clean-up that ends such leftovers (see ``_cleanup``) would never be reached.
        """
        while process.returncode is None:  # noqa: ASYNC110 - asyncio gives nothing else to wait on, as explained above
            await asyncio.sleep(EXIT_POLL_SECONDS)
        return process.returncode

    async def _reap(self, run: Run, pumps: list[asyncio.Task[Any]]) -> None:
        """Wait for the process to end, take the last of its messages, and settle what the run was."""
        assert run.process is not None
        try:
            run.exit_code = await self._exit_code(run.process)
            try:
                await asyncio.wait_for(asyncio.gather(*pumps, return_exceptions=True), DRAIN_SECONDS)
            except asyncio.TimeoutError:
                for pump in pumps:
                    pump.cancel()
            self._settle(run)
        except Exception:  # noqa: BLE001 - the run must always end up finished
            log.exception("could not settle a run")
            run.state, run.reason = "crashed", "The runner lost track of this bot."
        finally:
            self._cleanup(run)
            run.exited.set()

    def _settle(self, run: Run) -> None:
        code = run.exit_code
        why = run.stop_reason
        if why in REASONS:
            state, reason = REASONS[why]
        elif code == 0 and run.fatal_seen:
            state, reason = "crashed", "Fluxer closed the connection for good. The red message above says why."
        elif code == 0:
            state, reason = "stopped", "The bot finished by itself."
        elif code is not None and code > 0 and run.error_seen:
            state, reason = "crashed", ERROR_REASON
        elif code is not None and code > 0:
            state, reason = "crashed", f"The bot ended on its own with the code {code}."
        elif code == -signal.SIGKILL:
            state, reason = "crashed", "The system shut the bot down, most likely because it ran out of memory."
        elif code == -getattr(signal, "SIGXCPU", 24):
            state, reason = "crashed", "The bot used up all the processor time it is allowed."
        else:
            state, reason = "crashed", f"The bot was ended by the system (code {code})."
        run.state, run.reason = state, reason
        run.ended_at = time.time()
        run.say("Your bot stopped." if state == "stopped" else "Your bot stopped unexpectedly.")
        # The red messages already tell what an error was; every other reason is only known to the runner.
        if reason != ERROR_REASON and (state == "crashed" or why in ("time", "cpu", "memory", "flood")):
            run.say(reason)

    def _cleanup(self, run: Run) -> None:
        if run.slot is not None and self._uid_base is not None:
            sandbox.kill_everything_of(self._uid_base + run.slot)  # before the folder goes and the user id is handed on
        if run.directory is not None:
            sandbox.remove_run_folder(run.directory)
            run.directory = None
        run.slot = None
        run.forget_secret()
        if run.ended_at is None:
            run.ended_at = time.time()

    # ------------------------------------------------------------------------------- watching from outside
    async def _supervise(self, run: Run) -> None:
        """Every second: the time limit, the processor and memory use, and a bot that floods its own console."""
        started = time.monotonic()
        try:
            while True:
                try:
                    await asyncio.wait_for(run.exited.wait(), TICK_SECONDS)
                    return
                except asyncio.TimeoutError:
                    pass
                if not run.alive or run.process is None:
                    return
                age = time.monotonic() - started
                if run.state == "starting" and age >= READY_FALLBACK_SECONDS:
                    run.state = "running"
                run.flood_streak = run.flood_streak + 1 if run.second_lines > FLOOD_LINES_PER_SECOND else 0
                run.second_lines = 0
                why = self._over_a_limit(run, age)
                if why and run.state != "stopping":
                    log.info("stopping a bot: %s", why)
                    await self._terminate(run, why)
                    return
        except Exception:  # noqa: BLE001
            log.exception("watching a bot failed")

    def _over_a_limit(self, run: Run, age: float) -> str | None:
        assert run.process is not None
        if age > run.limits.max_run_seconds:
            return "time"
        if run.flood_streak >= FLOOD_SECONDS_TO_STOP:
            return "flood"
        pid = run.process.pid
        used = sandbox.cpu_seconds_used(pid)
        if used is not None:
            share = run.processor_share(time.monotonic(), used)
            if share is not None and share > CPU_BUSY_SHARE:
                return "cpu"
        memory = sandbox.memory_mb_used(pid)
        if memory is not None and memory > run.limits.memory_mb + 100:
            return "memory"
        return None

    # ------------------------------------------------------------------------------- stopping
    async def stop(self, bot_id: str, why: str = "you") -> dict[str, Any]:
        async with self._lock_for(bot_id):
            run = self._runs.get(bot_id)
            if run is not None and run.alive:
                await self._terminate(run, why)
            return view(self._runs.get(bot_id), 0, None)

    async def shutdown(self) -> None:
        """Stop every bot (the runner is going away)."""
        await asyncio.gather(*(self._terminate(run, "shutdown") for run in self.active()), return_exceptions=True)

    async def _terminate(self, run: Run, why: str) -> None:
        if not run.alive:
            return
        if run.stop_reason is None:
            run.stop_reason = why
        run.state = "stopping"
        self._send(run, signal.SIGINT)  # like Ctrl+C: the bot closes its connection to Fluxer properly
        if not await self._exited(run, STOP_GRACE_SECONDS):
            self._send(run, signal.SIGKILL)
            await self._exited(run, KILL_WAIT_SECONDS)

    @staticmethod
    async def _exited(run: Run, seconds: float) -> bool:
        try:
            await asyncio.wait_for(run.exited.wait(), seconds)
            return True
        except asyncio.TimeoutError:
            return False

    @staticmethod
    def _send(run: Run, sig: signal.Signals) -> None:
        process = run.process
        if process is None or process.returncode is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, sig)  # the bot was started as the leader of its own group
            elif sig == signal.SIGINT:
                process.terminate()
            else:
                process.kill()
        except ProcessLookupError:
            pass  # it has already ended
        except PermissionError:
            # Not something a bot can cause: the runner itself is missing a permission (see sandbox.require_user_switching).
            log.error("the runner isn't allowed to stop a bot process (signal %s)", sig.name)

    # ------------------------------------------------------------------------------- housekeeping
    def _lock_for(self, bot_id: str) -> asyncio.Lock:
        lock = self._locks.get(bot_id)
        if lock is None:
            lock = self._locks[bot_id] = asyncio.Lock()
        return lock

    def _sweep(self) -> None:
        """Forget the messages of runs that ended a long time ago."""
        now = time.time()
        for bot_id, run in list(self._runs.items()):
            if not run.alive and run.ended_at is not None and now - run.ended_at > KEEP_FINISHED_SECONDS:
                del self._runs[bot_id]
                self._locks.pop(bot_id, None)


def view(run: Run | None, after: int, known: str | None) -> dict[str, Any]:
    """What the website gets for a run: its state and the messages after number ``after``.

    ``known`` is the run the caller has been reading. If it isn't this one, the caller starts over (``reset``).
    """
    now = (
        time.time()
    )  # the runner's own clock, so the page can show "running for 2 minutes" whatever its own clock says
    if run is None:
        return {
            "now": now,
            "run": None,
            "state": "none",
            "reason": "",
            "ready": False,
            "exit_code": None,
            "started_at": None,
            "ended_at": None,
            "lines": [],
            "next": 0,
            "skipped": 0,
            "reset": bool(known),
        }
    reset = known is not None and known != run.id
    if reset:
        after = 0
    first = run.lines[0].n if run.lines else run.next_n
    return {
        "now": now,
        "run": run.id,
        "state": run.state,
        "reason": run.reason,
        "ready": run.ready,
        "exit_code": run.exit_code,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "lines": [line.as_dict() for line in run.lines if line.n > after],
        "next": run.next_n - 1,
        "skipped": max(0, first - after - 1),
        "reset": reset,
    }
