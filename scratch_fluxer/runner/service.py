"""The one runner the website talks to, chosen from the settings the first time it is needed."""

from __future__ import annotations

import logging
import threading

from ..config import Settings, get_settings
from .clients import LocalRunner, OffRunner, RemoteRunner, RunnerClient
from .manager import RunnerManager

log = logging.getLogger("scratch_fluxer.runner")

_runner: RunnerClient | None = None
_lock = threading.Lock()


def build(settings: Settings) -> RunnerClient:
    mode = settings.runner_effective_mode
    if mode == "local":
        return LocalRunner(RunnerManager(settings))
    if mode == "remote":
        assert settings.runner_url and settings.runner_secret  # runner_effective_mode checked both
        return RemoteRunner(settings.runner_url, settings.runner_secret)
    return OffRunner()


def get_runner() -> RunnerClient:
    global _runner
    if _runner is None:
        with _lock:
            if _runner is None:
                _runner = build(get_settings())
    return _runner


def set_runner(runner: RunnerClient | None) -> None:
    """Use this runner from now on (tests put a fake one here; ``None`` goes back to the settings)."""
    global _runner
    with _lock:
        _runner = runner


async def stop_quietly(bot_id: str) -> None:
    """Stop a bot's run because the bot was removed or its token changed. Never raises: the page must still answer."""
    try:
        runner = get_runner()  # a site on Vercel starts fresh for most calls, so this can't wait for "already built"
        if runner.mode == "off":
            return
        await runner.stop(bot_id, "removed")
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not stop a removed bot's run: %s", type(exc).__name__)


async def shutdown() -> None:
    """The site is going away: stop what it started itself and close the connection to a remote runner."""
    global _runner
    runner, _runner = _runner, None
    if runner is not None:
        try:
            await runner.close()
        except Exception:  # noqa: BLE001
            log.exception("Could not shut the runner down cleanly")
