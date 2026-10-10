"""How a bot's process is set up.

A bot gets its own folder (its file and the uploaded files it uses, nothing else), a clean environment that holds the
bot token and nothing else the runner knows, and limits on memory, processor time, file size and open files. The limits
are applied by ``child.py`` inside the process; the manager adds a wall-clock limit and watches the processor and memory
use from outside.

If the runner is started as root and given ``RUNNER_UID_BASE``, every running bot also gets a user id of its own, so one
bot can't read another bot's folder or the runner's own files and environment. When the bot ends, everything else that
runs under that user id is ended too, because the id is handed to the next bot.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

CHILD = Path(__file__).with_name("child.py")
_PASS_THROUGH = ("SSL_CERT_FILE", "SSL_CERT_DIR")  # a runner behind its own certificate authority needs these
#: Processes (and threads) one bot may have when it has a user id of its own. Python's own helper threads stay far below.
MAX_PROCESSES = 128
#: What a runner that is root needs to give every bot a user of its own, as Linux capability numbers: to start the bot as
#: that user (SETUID, SETGID), to give it its folder (CHOWN), to clear that folder afterwards whatever the bot left in
#: it (DAC_OVERRIDE) and to stop the bot (KILL). Docker's default set has all five; ``--cap-drop=ALL`` takes them away.
USER_SWITCHING_CAPABILITIES = {"CHOWN": 0, "DAC_OVERRIDE": 1, "KILL": 5, "SETGID": 6, "SETUID": 7}


@dataclass(frozen=True)
class Limits:
    memory_mb: int = 384
    cpu_seconds: int = 3600
    file_mb: int = 8
    max_run_seconds: int = 6 * 60 * 60

    def clamped(self) -> Limits:
        """Sensible bounds, so a typo in the settings can't make every bot fail to start (or never stop)."""
        return Limits(
            memory_mb=min(max(self.memory_mb, 128), 4096),
            cpu_seconds=min(max(self.cpu_seconds, 60), 24 * 3600),
            file_mb=min(max(self.file_mb, 1), 64),
            max_run_seconds=min(max(self.max_run_seconds, 60), 7 * 24 * 3600),
        )


def command(main_path: Path) -> list[str]:
    """``python -I -u -B -X utf8 child.py main.py``: isolated from the runner's environment and folders, no buffering,
    no ``.pyc`` files, and UTF-8 everywhere."""
    return [sys.executable, "-I", "-u", "-B", "-X", "utf8", str(CHILD), str(main_path)]


def environment(
    token: str, limits: Limits, directory: Path, api_url: str | None, run_as: tuple[int, int] | None = None
) -> dict[str, str]:
    """The only variables the bot's process can see. With ``run_as`` (a user id and a group id) the process is started
    as root and, as its first act, becomes that user (``child.py`` removes the variable)."""
    env = {
        "FLUXER_TOKEN": token,
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(directory),
        "TMPDIR": str(directory),
        "LANG": "C.UTF-8",
        "SFF_MEMORY_MB": str(limits.memory_mb),
        "SFF_CPU_SECONDS": str(limits.cpu_seconds),
        "SFF_FILE_MB": str(limits.file_mb),
        "SFF_PARENT_PID": str(os.getpid()),  # the bot ends when this process does (the child removes it again)
    }
    if api_url:
        env["SFF_FLUXER_API_URL"] = api_url
    if run_as is not None:
        env["SFF_RUN_AS"] = f"{run_as[0]}:{run_as[1]}"
        env["SFF_MAX_PROCESSES"] = str(MAX_PROCESSES)  # per user id, so only meaningful when the id is the bot's own
    if os.name == "nt":  # Python can't start on Windows without these
        for name in ("SYSTEMROOT", "TEMP", "TMP"):
            if os.environ.get(name):
                env[name] = os.environ[name]
    for name in _PASS_THROUGH:
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


def write_run_folder(
    base: Path, bot_id: str, code: str, files: Sequence[tuple[str, bytes]], owner: tuple[int, int] | None = None
) -> Path:
    """Make ``<base>/<bot id>/`` with ``main.py`` and ``assets/<name>``, replacing whatever an earlier run left there.
    The folder is private to the bot's user (and so to the runner)."""
    directory = base / bot_id
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    directory.mkdir(mode=0o700)
    paths: list[tuple[Path, bytes]] = [(directory / "main.py", code.encode("utf-8"))]
    if files:
        (directory / "assets").mkdir(mode=0o700)
        paths.extend((directory / "assets" / name, data) for name, data in files)
    for path, data in paths:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    if owner is not None:
        for path in [directory, *directory.rglob("*")]:
            os.chown(path, *owner)
        keep_the_list_of_folders_private(base)
    return directory


def keep_the_list_of_folders_private(base: Path) -> None:
    """A bot with a user of its own has to be able to walk through ``base`` to its folder, but not to list it: the names
    in it are the ids of the other bots that are running. Only the runner's own folder is changed, never a shared one."""
    with contextlib.suppress(OSError):
        info = base.stat()
        if info.st_uid == os.geteuid() and not info.st_mode & 0o002:
            base.chmod(0o711)


def remove_run_folder(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)


def clear_old_folders(base: Path) -> None:
    """Folders of bots that were running when the runner last stopped. Only folders named like a bot id are touched."""
    if not base.is_dir():
        return
    for child in base.iterdir():
        if child.is_dir() and len(child.name) == 32 and all(ch in "0123456789abcdef" for ch in child.name):
            shutil.rmtree(child, ignore_errors=True)


# --------------------------------------------------------------------------------------------- watching a process
def _read(path: str) -> str | None:
    try:
        with open(path, encoding="ascii", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def cpu_seconds_used(pid: int) -> float | None:
    """Processor time the process has used so far (Linux only; ``None`` elsewhere)."""
    text = _read(f"/proc/{pid}/stat")
    if not text:
        return None
    try:
        fields = text[text.rindex(")") + 2 :].split()  # the name in brackets may hold spaces
        ticks = int(fields[11]) + int(fields[12])  # user + system time
        return ticks / os.sysconf("SC_CLK_TCK")
    except (ValueError, IndexError, OSError):
        return None


def memory_mb_used(pid: int) -> float | None:
    """Memory the process holds in RAM (Linux only; ``None`` elsewhere)."""
    text = _read(f"/proc/{pid}/status")
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            try:
                return int(line.split()[1]) / 1024
            except (ValueError, IndexError):
                return None
    return None


def can_switch_users() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def missing_capabilities() -> list[str]:
    """Which of ``USER_SWITCHING_CAPABILITIES`` this process doesn't have (Linux only; an empty list where it can't tell)."""
    text = _read("/proc/self/status")
    for line in (text or "").splitlines():
        if line.startswith("CapEff:"):
            try:
                held = int(line.split()[1], 16)
            except (ValueError, IndexError):
                return []
            return [name for name, number in USER_SWITCHING_CAPABILITIES.items() if not held >> number & 1]
    return []


def require_user_switching() -> None:
    """Say so, loudly and before any bot runs, if the runner is root but can't do what a user id per bot needs. Without
    this, the first sign would be a bot that can't be stopped."""
    lacking = missing_capabilities()
    if lacking:
        raise RuntimeError(
            "RUNNER_UID_BASE is set and the runner is root, but it lacks the Linux capabilities "
            + ", ".join(lacking)
            + " that a user id per bot needs. Add them (docker run --cap-add=...), or leave RUNNER_UID_BASE empty."
        )


def _user_ids_of(pid: str) -> tuple[int, ...]:
    """The real, effective and saved user ids of a process that is still running (nothing for one that has ended and
    only waits to be collected)."""
    text = _read(f"/proc/{pid}/status")
    ids: tuple[int, ...] = ()
    for line in (text or "").splitlines():
        if line.startswith("State:") and line.split()[1:2] == ["Z"]:
            return ()
        if line.startswith("Uid:"):
            try:
                ids = tuple(int(part) for part in line.split()[1:4])
            except ValueError:
                return ()
    return ids


def kill_everything_of(uid: int) -> int:
    """End every process that runs under the user id ``uid``, and say how many there were (Linux only).

    Only meant for the made-up user id of one bot: whatever that bot started and left running (in a group of its own, or
    after it was told to stop) must not outlive it, because the next bot to get this user id could read its environment.
    """
    if uid <= 0 or uid in (os.getuid(), os.geteuid()) or not Path("/proc/self").exists():
        return 0
    count = 0
    for _ in range(3):  # a process that starts another in the meantime is caught on the next round
        found = []
        with contextlib.suppress(OSError):
            found = [
                entry.name for entry in os.scandir("/proc") if entry.name.isdigit() and uid in _user_ids_of(entry.name)
            ]
        if not found:
            break
        for pid in found:
            with contextlib.suppress(OSError):
                os.kill(int(pid), signal.SIGKILL)
                count += 1
    return count
