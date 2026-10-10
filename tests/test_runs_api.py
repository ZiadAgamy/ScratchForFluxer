"""Run, restart, stop and read the console: the site's side of running a bot.

The runner itself is a fake here (it writes down what it is given and answers like the real one), so these tests are
about what the site does: who may ask, what it sends, what it keeps, what it says when something goes wrong.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from blocks import command, reply, workspace
from fastapi.testclient import TestClient
from fluxer_mock import TOKEN, TOKEN_1_ROTATED
from sqlalchemy import delete, select, update

from scratch_fluxer import config, deps
from scratch_fluxer.db import _call_with_session
from scratch_fluxer.models import Bot, User
from scratch_fluxer.runner import service
from scratch_fluxer.runner.clients import OFF_MESSAGE, OffRunner
from scratch_fluxer.runner.protocol import Busy, Refused, RunnerError, TooMany, Unavailable
from scratch_fluxer.security import InvalidToken, TokenVault

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
RUN = "e5" * 16
TOKEN_SECRET_PART = TOKEN.split(".")[1]
LONG_AGO = datetime(2020, 1, 1, tzinfo=timezone.utc)


class FakeRunner:
    """Stands in for the runner: writes down what it is given (with the token, as the real one needs it)."""

    mode = "remote"

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.seen: list[SimpleNamespace] = []  # what each start was given, as it was at that moment
        self.requests: list = []  # the start requests themselves (the site wipes the token from them afterwards)
        self.fail_with: RunnerError | Exception | None = None
        self.during_start = None  # something that happens elsewhere while a start is under way: ``hook(request)``
        self.running = False
        self.closed = False

    def view(self, state: str = "starting", **more) -> dict:
        lines = [{"n": 1, "t": 1.0, "level": "system", "source": "system", "text": "Starting your bot…"}]
        return {
            "run": RUN,
            "state": state,
            "reason": "",
            "ready": state == "running",
            "exit_code": None,
            "started_at": 1.0,
            "ended_at": None,
            "lines": lines,
            "next": 1,
            "skipped": 0,
            "reset": False,
            **more,
        }

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    async def start(self, request, *, replace: bool = True) -> dict:
        self.calls.append(("start", request.bot_id, replace))
        self.requests.append(request)
        self.seen.append(
            SimpleNamespace(
                bot_id=request.bot_id,
                owner=request.owner,
                code=request.code,
                token=request.token,
                files=list(request.files),
            )
        )
        if self.during_start is not None:
            self.during_start(request)
        self._maybe_fail()
        if self.running and not replace:
            return self.view("running", already_running=True)
        self.running = True
        return self.view()

    async def stop(self, bot_id: str, why: str = "you") -> dict:
        self.calls.append(("stop", bot_id, why))
        self._maybe_fail()
        self.running = False
        return self.view("stopped", reason="You stopped the bot.")

    async def status(self, bot_id: str, *, after: int = 0, known: str | None = None) -> dict:
        self.calls.append(("status", bot_id, after, known))
        self._maybe_fail()
        return self.view("running" if self.running else "stopped")

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def runner() -> FakeRunner:
    fake = FakeRunner()
    service.set_runner(fake)
    return fake


# ------------------------------------------------------------------------------------------------ database helpers
def stored(bot_id: str) -> SimpleNamespace:
    def _read(db):
        row = db.execute(select(Bot).where(Bot.id == bot_id)).scalar_one()
        return SimpleNamespace(**{column.name: getattr(row, column.name) for column in Bot.__table__.columns})

    return _call_with_session(_read, (), {})


def change(bot_id: str, **values) -> None:
    _call_with_session(lambda db: db.execute(update(Bot).where(Bot.id == bot_id).values(**values)), (), {})


def remove(bot_id: str) -> None:
    _call_with_session(lambda db: db.execute(delete(Bot).where(Bot.id == bot_id)), (), {})


def naive(moment: datetime | None) -> datetime | None:
    return moment.replace(tzinfo=None) if moment else None


def save(me, bot_id, ws):
    r = me.put(f"/api/bots/{bot_id}/workspace", json={"version": 0, "workspace": ws}, headers=me.h)
    assert r.status_code == 200, r.text
    return r


def upload(me, bot_id, name, data=PNG):
    r = me.put(f"/api/bots/{bot_id}/assets/{name}", content=data, headers=me.h)
    assert r.status_code in (200, 201), r.text


STRANGERS_TOKEN = "333333333333333333." + "D" * 36 + "4321"


def add_strangers_bot(stranger, fluxer) -> str:
    """The other person adds a bot of their own (Fluxer says this token's bot belongs to them)."""
    from fluxer_mock import application

    fluxer.apps[STRANGERS_TOKEN] = application("333333333333333333", "Quiz Bot", stranger_fluxer_id(), "quizbot")
    r = stranger.post("/api/bots", json={"token": STRANGERS_TOKEN}, headers=stranger.h)
    assert r.status_code == 201, r.text
    return r.json()["bot"]["id"]


def stranger_fluxer_id() -> str:
    return "900000000000002"  # the id the ``stranger`` fixture gives the other person


def run_url(bot_id: str, action: str = "run") -> str:
    return f"/api/bots/{bot_id}/{action}"


def ask(person, method: str, bot_id: str, action: str):
    """``person`` (``me`` or ``stranger``) makes the call, with the CSRF header when it changes something."""
    if method == "post":
        return person.post(run_url(bot_id, action), headers=person.h)
    return person.get(run_url(bot_id, action))


# ================================================================================================= starting
class TestRun:
    def test_the_runner_gets_the_saved_code_and_the_real_token_and_the_page_gets_a_description(
        self, me, new_bot, runner
    ):
        bot_id = new_bot()
        r = me.post(run_url(bot_id), headers=me.h)
        assert r.status_code == 200
        assert r.json()["state"] == "starting" and r.json()["run"] == RUN
        assert r.json()["lines"][0]["text"] == "Starting your bot…"
        (got,) = runner.seen
        assert got.bot_id == bot_id and got.owner == me.user_id and got.token == TOKEN and got.files == []
        assert got.code == me.get(f"/api/bots/{bot_id}/workspace").json()["compile"]["code"]
        assert runner.calls == [("start", bot_id, False)]

    def test_nothing_the_site_says_holds_the_token(self, me, new_bot, runner):
        bot_id = new_bot()
        answers = [
            me.post(run_url(bot_id), headers=me.h),
            me.post(run_url(bot_id, "restart"), headers=me.h),
            me.get(run_url(bot_id)),
            me.post(run_url(bot_id, "stop"), headers=me.h),
            me.get(f"/bots/{bot_id}/edit"),
        ]
        for r in answers:
            assert r.status_code == 200
            blob = r.text + json.dumps(dict(r.headers))
            assert TOKEN not in blob and TOKEN_SECRET_PART not in blob

    def test_the_site_does_not_keep_the_token_after_handing_it_over(self, me, new_bot, runner):
        bot_id = new_bot()
        me.post(run_url(bot_id), headers=me.h)
        assert runner.seen[0].token == TOKEN
        assert runner.requests[0].token == ""

    def test_running_marks_the_bot_as_run_but_not_as_edited(self, me, new_bot, runner):
        bot_id = new_bot()
        change(bot_id, updated_at=LONG_AGO, last_run_at=None)
        me.post(run_url(bot_id), headers=me.h)
        row = stored(bot_id)
        assert row.last_run_at is not None
        assert naive(row.updated_at) == naive(LONG_AGO)  # the list of bots is still ordered by editing

    def test_pressing_run_twice_changes_nothing_the_second_time(self, me, new_bot, runner):
        bot_id = new_bot()
        first = me.post(run_url(bot_id), headers=me.h).json()
        assert "already_running" not in first
        change(bot_id, last_run_at=LONG_AGO)
        second = me.post(run_url(bot_id), headers=me.h).json()
        assert second["already_running"] is True and second["state"] == "running"
        assert naive(stored(bot_id).last_run_at) == naive(LONG_AGO)  # the second press was not a run

    def test_restart_starts_again_even_if_it_is_running(self, me, new_bot, runner):
        bot_id = new_bot()
        me.post(run_url(bot_id), headers=me.h)
        change(bot_id, last_run_at=LONG_AGO)
        r = me.post(run_url(bot_id, "restart"), headers=me.h)
        assert r.status_code == 200 and "already_running" not in r.json()
        assert runner.calls[-1] == ("start", bot_id, True)
        assert naive(stored(bot_id).last_run_at) != naive(LONG_AGO)

    def test_only_the_files_the_blocks_use_are_sent(self, me, new_bot, runner):
        bot_id = new_bot()
        upload(me, bot_id, "cat.png")
        upload(me, bot_id, "quotes.json", b'["a"]')
        save(
            me,
            bot_id,
            workspace(command("pic", {"type": "sff_send_image", "id": "send", "fields": {"FILE": "cat.png"}})),
        )
        assert me.post(run_url(bot_id), headers=me.h).status_code == 200
        assert runner.seen[0].files == [("cat.png", PNG)]

    def test_blocks_with_errors_do_not_run(self, me, new_bot, runner):
        bot_id = new_bot()
        save(
            me,
            bot_id,
            workspace(command("pic", {"type": "sff_send_image", "id": "send", "fields": {"FILE": "gone.png"}})),
        )
        r = me.post(run_url(bot_id), headers=me.h)
        assert r.status_code == 409 and "Fix the problems" in r.json()["detail"]
        r = me.post(run_url(bot_id, "restart"), headers=me.h)
        assert r.status_code == 409
        assert runner.calls == []

    def test_a_token_that_cannot_be_read_any_more_is_explained(self, me, new_bot, runner, caplog):
        bot_id = new_bot()
        foreign = TokenVault("some-other-secret-key-0123456789-abcdefghijklmnop").encrypt(TOKEN)
        change(bot_id, token_ciphertext=foreign)
        with caplog.at_level(logging.INFO):
            r = me.post(run_url(bot_id), headers=me.h)
        assert r.status_code == 409 and "paste the token again" in r.json()["detail"]
        assert runner.calls == []
        assert TOKEN not in caplog.text and foreign not in caplog.text

    def test_the_code_that_runs_reads_the_token_from_the_environment_and_never_holds_it(self, me, new_bot, runner):
        bot_id = new_bot()
        save(me, bot_id, workspace(command("hello", reply("Hello!"))))
        assert me.post(run_url(bot_id), headers=me.h).status_code == 200
        assert 'os.environ["FLUXER_TOKEN"]' in runner.seen[0].code and TOKEN not in runner.seen[0].code


# ================================================================================================= rotating SECRET_KEY
OLDER_KEY = "an-older-secret-key-0123456789-abcdefghijklmnopqrstuvwx"


class TestKeyRotation:
    def rotate_to_the_current_key(self, monkeypatch) -> None:
        """The site now has the test key as its newest key and ``OLDER_KEY`` as an older one."""
        monkeypatch.setenv("SECRET_KEY_PREVIOUS", OLDER_KEY)
        config.reset_settings_cache()
        deps.reset_singletons()

    def test_a_token_under_an_older_key_moves_to_the_newest_when_its_bot_runs(
        self, me, new_bot, runner, monkeypatch, settings
    ):
        bot_id = new_bot()
        change(bot_id, token_ciphertext=TokenVault(OLDER_KEY).encrypt(TOKEN), updated_at=LONG_AGO)
        self.rotate_to_the_current_key(monkeypatch)
        r = me.post(run_url(bot_id), headers=me.h)
        assert r.status_code == 200 and runner.seen[0].token == TOKEN  # it ran with the real token
        row = stored(bot_id)
        assert TokenVault(settings.secret_key).decrypt(row.token_ciphertext) == TOKEN  # the newest key alone opens it
        with pytest.raises(InvalidToken):
            TokenVault(OLDER_KEY).decrypt(row.token_ciphertext)  # and the old key no longer does
        assert naive(row.updated_at) == naive(LONG_AGO)  # saving it again is not editing the bot
        assert row.last_run_at is not None

    def test_a_token_that_is_already_under_the_newest_key_is_left_alone(self, me, new_bot, runner, monkeypatch):
        bot_id = new_bot()
        before = stored(bot_id).token_ciphertext
        self.rotate_to_the_current_key(monkeypatch)
        assert me.post(run_url(bot_id), headers=me.h).status_code == 200
        assert stored(bot_id).token_ciphertext == before

    def test_the_old_key_can_be_dropped_once_every_bot_has_run(self, me, new_bot, runner, monkeypatch):
        bot_id = new_bot()
        change(bot_id, token_ciphertext=TokenVault(OLDER_KEY).encrypt(TOKEN))
        self.rotate_to_the_current_key(monkeypatch)
        assert me.post(run_url(bot_id), headers=me.h).status_code == 200
        monkeypatch.delenv("SECRET_KEY_PREVIOUS")  # ... and now it is not needed
        config.reset_settings_cache()
        deps.reset_singletons()
        runner.calls.clear()
        assert me.post(run_url(bot_id, "restart"), headers=me.h).status_code == 200
        assert runner.seen[-1].token == TOKEN

    def test_nothing_in_the_log_shows_a_token_or_a_key(self, me, new_bot, runner, monkeypatch, caplog):
        bot_id = new_bot()
        change(bot_id, token_ciphertext=TokenVault(OLDER_KEY).encrypt(TOKEN))
        self.rotate_to_the_current_key(monkeypatch)
        with caplog.at_level(logging.DEBUG):
            me.post(run_url(bot_id), headers=me.h)
        assert TOKEN not in caplog.text and OLDER_KEY not in caplog.text


# ================================================================================================= changed while starting
class TestChangedWhileStarting:
    """A start takes a moment. If the bot is removed, or gets another token, in that moment, whoever did it has already
    asked the runner to stop the bot, which can be before this start got there. The start checks, and stops it."""

    def test_a_bot_removed_while_it_starts_does_not_keep_running(self, me, new_bot, runner):
        bot_id = new_bot()
        runner.during_start = lambda request: remove(bot_id)
        r = me.post(run_url(bot_id), headers=me.h)
        assert r.status_code == 409 and "changed while it was starting" in r.json()["detail"]
        assert ("stop", bot_id, "removed") in runner.calls
        assert runner.running is False

    def test_a_token_replaced_while_it_starts_does_not_keep_running_with_the_old_one(self, me, new_bot, runner):
        bot_id = new_bot()
        replacement = TokenVault(config.get_settings().secret_key).encrypt(TOKEN_1_ROTATED)
        runner.during_start = lambda request: change(bot_id, token_ciphertext=replacement)
        r = me.post(run_url(bot_id), headers=me.h)
        assert r.status_code == 409 and ("stop", bot_id, "removed") in runner.calls
        assert runner.running is False
        assert stored(bot_id).last_run_at is None  # it did not count as a run

    def test_restart_is_checked_the_same_way(self, me, new_bot, runner):
        bot_id = new_bot()
        runner.during_start = lambda request: remove(bot_id)
        assert me.post(run_url(bot_id, "restart"), headers=me.h).status_code == 409
        assert ("stop", bot_id, "removed") in runner.calls

    def test_the_answer_for_a_start_that_was_not_disturbed_is_unchanged(self, me, new_bot, runner):
        bot_id = new_bot()
        runner.during_start = lambda request: None
        r = me.post(run_url(bot_id), headers=me.h)
        assert r.status_code == 200 and r.json()["state"] == "starting"
        assert [c[0] for c in runner.calls] == ["start"]


# ================================================================================================= stopping and reading
class TestStopAndRead:
    def test_stop_asks_the_runner_to_stop_for_this_person(self, me, new_bot, runner):
        bot_id = new_bot()
        me.post(run_url(bot_id), headers=me.h)
        r = me.post(run_url(bot_id, "stop"), headers=me.h)
        assert r.status_code == 200 and r.json()["state"] == "stopped" and r.json()["reason"] == "You stopped the bot."
        assert runner.calls[-1] == ("stop", bot_id, "you")

    def test_reading_passes_on_where_the_reader_is(self, me, new_bot, runner):
        bot_id = new_bot()
        r = me.get(run_url(bot_id), params={"after": 3, "run": RUN})
        assert r.status_code == 200 and r.json()["state"] == "stopped"
        assert runner.calls == [("status", bot_id, 3, RUN)]
        me.get(run_url(bot_id))
        assert runner.calls[-1] == ("status", bot_id, 0, None)

    @pytest.mark.parametrize("query", ["after=-1", "after=abc", "after=99999999999", "run=short", f"run={'Z' * 32}"])
    def test_a_read_that_makes_no_sense_is_refused(self, me, new_bot, runner, query):
        bot_id = new_bot()
        assert me.get(f"{run_url(bot_id)}?{query}").status_code == 422
        assert runner.calls == []

    def test_what_is_read_is_never_cached(self, me, new_bot, runner):
        bot_id = new_bot()
        assert me.get(run_url(bot_id)).headers["cache-control"] == "no-store"


# ================================================================================================= who may
class TestWhoMay:
    @pytest.mark.parametrize(
        ("method", "action", "reaches_the_runner"),
        [
            ("post", "run", ("start", False)),
            ("post", "restart", ("start", True)),
            ("post", "stop", ("stop", "you")),
            ("get", "run", ("status", 0, None)),
        ],
    )
    def test_somebody_elses_bot_looks_like_a_bot_that_does_not_exist(
        self, me, stranger, new_bot, runner, method, action, reaches_the_runner
    ):
        bot_id = new_bot()
        mine = ask(me, method, bot_id, action)
        theirs = ask(stranger, method, bot_id, action)
        nothing = ask(stranger, method, "f" * 32, action)
        assert mine.status_code == 200
        assert theirs.status_code == nothing.status_code == 404 and theirs.json() == nothing.json()
        name, *rest = reaches_the_runner
        assert runner.calls == [(name, bot_id, *rest)]  # only the owner's call got through

    @pytest.mark.parametrize(
        ("method", "action"), [("post", "run"), ("post", "restart"), ("post", "stop"), ("get", "run")]
    )
    def test_it_needs_a_login(self, me, client, new_bot, runner, method, action):
        bot_id = new_bot()
        client.cookies.clear()
        assert getattr(client, method)(run_url(bot_id, action)).status_code == 401
        assert runner.calls == []

    @pytest.mark.parametrize("action", ["run", "restart", "stop"])
    def test_a_change_needs_the_csrf_header(self, me, new_bot, runner, action):
        bot_id = new_bot()
        assert me.post(run_url(bot_id, action)).status_code == 403
        assert me.post(run_url(bot_id, action), headers={"X-CSRF-Token": "wrong"}).status_code == 403
        assert me.post(run_url(bot_id, action), headers={**me.h, "Origin": "https://evil.example"}).status_code == 403
        assert runner.calls == []

    def test_two_people_can_each_run_their_own(self, me, stranger, new_bot, runner, fluxer):
        mine = new_bot()
        theirs = add_strangers_bot(stranger, fluxer)
        assert me.post(run_url(mine), headers=me.h).status_code == 200
        assert stranger.post(run_url(theirs), headers=stranger.h).status_code == 200
        owners = {got.bot_id: got.owner for got in runner.seen}
        assert owners == {mine: me.user_id, theirs: stranger.user_id}
        assert {got.bot_id: got.token for got in runner.seen}[theirs] == STRANGERS_TOKEN


# ================================================================================================= when it goes wrong
class TestWhenItGoesWrong:
    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (Refused("This code failed the safety check, so it was not run."), 422),
            (TooMany("You can run 2 bots at the same time. Stop one of them first."), 409),
            (Busy("The runner is full right now. Try again in a few minutes."), 503),
            (Unavailable("The bot runner can't be reached right now. Try again in a moment."), 503),
        ],
    )
    @pytest.mark.parametrize("action", ["run", "restart", "stop"])
    def test_what_the_runner_says_is_said_in_words_with_a_fitting_status(
        self, me, new_bot, runner, action, error, status
    ):
        bot_id = new_bot()
        runner.fail_with = error
        r = me.post(run_url(bot_id, action), headers=me.h)
        assert r.status_code == status and r.json() == {"detail": error.message}
        assert stored(bot_id).last_run_at is None  # a run that never started is not a run

    def test_reading_while_the_runner_is_away_is_a_503_not_a_crash(self, me, new_bot, runner):
        bot_id = new_bot()
        runner.fail_with = Unavailable("The bot runner can't be reached right now. Try again in a moment.")
        r = me.get(run_url(bot_id))
        assert r.status_code == 503 and "can't be reached" in r.json()["detail"]

    def test_the_site_is_still_there_afterwards(self, me, new_bot, runner):
        bot_id = new_bot()
        runner.fail_with = Unavailable("The bot runner can't be reached right now. Try again in a moment.")
        assert me.post(run_url(bot_id), headers=me.h).status_code == 503
        runner.fail_with = None
        assert me.post(run_url(bot_id), headers=me.h).status_code == 200
        assert me.get("/bots").status_code == 200


class TestRunningIsSwitchedOff:
    @pytest.fixture(autouse=True)
    def _off(self):
        service.set_runner(OffRunner())

    def test_a_run_is_refused_with_the_reason(self, me, new_bot):
        bot_id = new_bot()
        for action in ("run", "restart"):
            r = me.post(run_url(bot_id, action), headers=me.h)
            assert r.status_code == 503 and r.json() == {"detail": OFF_MESSAGE}
        assert stored(bot_id).last_run_at is None

    def test_a_stranger_still_learns_nothing(self, me, stranger, new_bot):
        bot_id = new_bot()
        assert stranger.post(run_url(bot_id), headers=stranger.h).status_code == 404

    def test_reading_and_stopping_say_nothing_is_running(self, me, new_bot):
        bot_id = new_bot()
        for r in (me.get(run_url(bot_id)), me.post(run_url(bot_id, "stop"), headers=me.h)):
            assert r.status_code == 200 and r.json()["state"] == "none" and r.json()["lines"] == []

    def test_the_editor_is_told_so(self, me, new_bot):
        bot_id = new_bot()
        assert 'data-runner="off"' in me.get(f"/bots/{bot_id}/edit").text


class TestTheEditorPage:
    def test_the_page_says_which_kind_of_runner_there_is(self, me, new_bot, monkeypatch):
        from scratch_fluxer import config

        bot_id = new_bot()
        monkeypatch.setenv("RUNNER_MODE", "local")
        config.reset_settings_cache()
        assert 'data-runner="local"' in me.get(f"/bots/{bot_id}/edit").text


# ================================================================================================= limits
class TestLimits:
    def test_changes_are_limited_per_person_and_reading_is_not_affected(self, me, new_bot, runner):
        bot_id = new_bot()
        codes = [me.post(run_url(bot_id), headers=me.h).status_code for _ in range(22)]
        assert codes[:20] == [200] * 20 and codes[20:] == [429, 429]
        assert me.get(run_url(bot_id)).status_code == 200

    def test_the_other_person_has_a_limit_of_their_own(self, me, stranger, new_bot, runner, fluxer):
        mine = new_bot()
        theirs = add_strangers_bot(stranger, fluxer)
        for _ in range(21):
            me.post(run_url(mine), headers=me.h)
        assert me.post(run_url(mine), headers=me.h).status_code == 429
        assert stranger.post(run_url(theirs), headers=stranger.h).status_code == 200

    def test_reading_is_limited_too_but_generously(self, me, new_bot, runner):
        bot_id = new_bot()
        codes = [me.get(run_url(bot_id)).status_code for _ in range(402)]
        assert codes.count(200) == 400 and codes[-2:] == [429, 429]


# ================================================================================================= clean-up
class TestRemovingThings:
    def test_removing_a_bot_stops_it(self, me, new_bot, runner):
        bot_id = new_bot()
        me.post(run_url(bot_id), headers=me.h)
        assert me.delete(f"/api/bots/{bot_id}", headers=me.h).status_code == 200
        assert runner.calls[-1] == ("stop", bot_id, "removed") and runner.running is False

    def test_replacing_the_token_stops_it_so_the_next_run_uses_the_new_one(self, me, new_bot, runner, fluxer):
        from fluxer_mock import TOKEN_1_ROTATED

        bot_id = new_bot()
        me.post(run_url(bot_id), headers=me.h)
        r = me.put(f"/api/bots/{bot_id}/token", json={"token": TOKEN_1_ROTATED}, headers=me.h)
        assert r.status_code == 200
        assert runner.calls[-1] == ("stop", bot_id, "removed")
        me.post(run_url(bot_id), headers=me.h)
        assert runner.seen[-1].token == TOKEN_1_ROTATED

    def test_deleting_the_account_stops_every_bot(self, me, new_bot, runner, fluxer):
        from fluxer_mock import TOKEN_2

        first = new_bot()
        second = me.post("/api/bots", json={"token": TOKEN_2}, headers=me.h).json()["bot"]["id"]
        for bot_id in (first, second):
            me.post(run_url(bot_id), headers=me.h)
        r = me.request("DELETE", "/api/me", json={"confirm": "agamy"}, headers=me.h)
        assert r.status_code == 200
        stopped = {call[1] for call in runner.calls if call[0] == "stop" and call[2] == "removed"}
        assert stopped == {first, second}
        assert _call_with_session(lambda db: db.execute(select(User)).first(), (), {}) is None

    def test_a_runner_that_cannot_be_reached_does_not_stop_a_bot_from_being_removed(self, me, new_bot, runner, caplog):
        bot_id = new_bot()
        runner.fail_with = RuntimeError(f"cannot reach {TOKEN}")
        with caplog.at_level(logging.INFO):
            r = me.delete(f"/api/bots/{bot_id}", headers=me.h)
        assert r.status_code == 200
        assert "RuntimeError" in caplog.text and TOKEN not in caplog.text

    def test_nothing_is_asked_of_a_runner_that_is_switched_off(self, me, new_bot):
        off = OffRunner()
        calls: list = []
        off.stop = lambda *args, **kwargs: calls.append(args)  # type: ignore[method-assign]
        service.set_runner(off)
        bot_id = new_bot()
        assert me.delete(f"/api/bots/{bot_id}", headers=me.h).status_code == 200
        assert calls == []


class TestTheSiteShuttingDown:
    def test_the_runner_is_closed_when_the_site_stops(self, app, runner):
        with TestClient(app, base_url="http://testserver") as client:
            assert client.get("/healthz").status_code == 200
            assert runner.closed is False
        assert runner.closed is True
