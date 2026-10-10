"""The site's own log messages can be seen when nobody has set logging up (the way uvicorn and Vercel start it)."""

from __future__ import annotations

import contextlib
import logging

from fastapi.testclient import TestClient

from scratch_fluxer import config, logs


@contextlib.contextmanager
def no_logging_set_up():
    """Logging as it is in a freshly started process. pytest puts handlers on the root logger while a test runs, so
    they are taken off for a moment (inside the test itself) and put back after."""
    root, ours = logging.getLogger(), logging.getLogger(logs.NAME)
    saved = (root.handlers[:], ours.handlers[:], ours.level, ours.propagate)
    root.handlers[:] = []
    ours.handlers[:] = []
    ours.setLevel(logging.NOTSET)
    try:
        yield ours
    finally:
        root.handlers[:] = saved[0]
        ours.handlers[:] = saved[1]
        ours.setLevel(saved[2])
        ours.propagate = saved[3]


class TestShowOurLog:
    def test_notes_are_printed_when_nobody_set_logging_up(self, capsys):
        with no_logging_set_up() as ours:
            logs.show_our_log()
            logging.getLogger("scratch_fluxer.runner").info("a bot was stopped")  # a part of the site, not the top
            ours.debug("too detailed to print")
        printed = capsys.readouterr().err
        assert "INFO:     a bot was stopped" in printed
        assert "too detailed" not in printed

    def test_other_libraries_are_not_made_chatty(self, capsys):
        with no_logging_set_up():
            logs.show_our_log()
            logging.getLogger("httpx").info("HTTP Request: POST https://example.invalid")
        assert capsys.readouterr().err == ""

    def test_it_leaves_logging_alone_when_somebody_set_it_up(self):
        with no_logging_set_up() as ours:
            logging.getLogger().addHandler(logging.NullHandler())
            logs.show_our_log()
            assert ours.handlers == [] and ours.level == logging.NOTSET

    def test_calling_it_again_changes_nothing(self):
        with no_logging_set_up() as ours:
            logs.show_our_log()
            logs.show_our_log()
            assert len(ours.handlers) == 1


class TestTheAppsUseIt:
    def test_the_site_prints_the_redirect_address_when_it_starts(self, monkeypatch, capsys):
        monkeypatch.setenv("APP_BASE_URL", "https://scratch.example")
        config.reset_settings_cache()
        from scratch_fluxer.main import create_app

        with no_logging_set_up(), TestClient(create_app()):
            pass
        assert "redirect address in your Fluxer application: https://scratch.example/auth/callback" in (
            capsys.readouterr().err
        )

    def test_the_runner_prints_its_notes_too(self, monkeypatch, capsys):
        from scratch_fluxer.runner.app import create_runner_app

        monkeypatch.setenv("RUNNER_SECRET", "r" * 40)
        config.reset_settings_cache()
        with no_logging_set_up():
            with TestClient(create_runner_app()):
                logging.getLogger("scratch_fluxer.runner").info("the runner says hello")
        assert "INFO:     the runner says hello" in capsys.readouterr().err
