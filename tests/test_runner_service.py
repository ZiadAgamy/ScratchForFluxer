"""The runner as a web service, and the website's way of calling it: who may call, what is accepted, what is never said."""

from __future__ import annotations

import json
import logging
import time

import httpx
import pytest
from starlette.testclient import TestClient

from scratch_fluxer.config import Settings
from scratch_fluxer.runner import sandbox
from scratch_fluxer.runner.app import create_runner_app
from scratch_fluxer.runner.clients import DOWN_MESSAGE, RemoteRunner
from scratch_fluxer.runner.manager import Run, view
from scratch_fluxer.runner.protocol import Busy, Refused, StartRequest, TooMany, Unavailable, Verifier, sign_headers

SECRET = "s" * 40
BOT = "a1" * 16
OTHER_BOT = "c3" * 16
OWNER = "b2" * 16
RUN = "d4" * 16
TOKEN = "111111111111111111.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1234"
TOKEN_SECRET_PART = TOKEN.split(".")[1]


class FakeManager:
    """Stands in for the real manager: writes down what it is asked and answers the way the real one does."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fail_with: BaseException | None = None
        self.shut_down = False
        self.run = Run(BOT, OWNER, TOKEN, sandbox.Limits())
        self.run.say("Starting your bot…")

    def active(self) -> list[Run]:
        return []

    def _answer(self, after: int = 0, known: str | None = None) -> dict:
        if self.fail_with is not None:
            raise self.fail_with
        return view(self.run, after, known)

    async def start(self, request: StartRequest, *, replace: bool = True) -> dict:
        self.calls.append(("start", request, replace))
        return self._answer()

    async def stop(self, bot_id: str, why: str = "you") -> dict:
        self.calls.append(("stop", bot_id, why))
        return self._answer()

    def snapshot(self, bot_id: str, after: int = 0, known: str | None = None) -> dict:
        self.calls.append(("status", bot_id, after, known))
        return self._answer(after, known)

    async def shutdown(self) -> None:
        self.shut_down = True


def make_settings(**changes) -> Settings:
    values = {"runner_secret": SECRET, "runner_max_concurrent": 5, "max_assets_total_bytes": 1000}
    values.update(changes)
    return Settings(_env_file=None, **values)


def start_request(**changes) -> StartRequest:
    values = {"bot_id": BOT, "owner": OWNER, "code": 'print("hi")\n', "token": TOKEN, "files": []}
    values.update(changes)
    return StartRequest(**values)


@pytest.fixture
def manager() -> FakeManager:
    return FakeManager()


@pytest.fixture
def app(manager):
    return create_runner_app(make_settings(), manager)  # type: ignore[arg-type]


@pytest.fixture
async def http(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://runner.test") as client:
        yield client


@pytest.fixture
async def remote(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    client = RemoteRunner("http://runner.test", SECRET, transport=transport)
    yield client
    await client.close()


async def send(http, method, target, body=b"", *, secret=SECRET, now=None, signed_for=None):
    """A call signed the way the site signs it. ``signed_for`` signs something else than what is sent (a forgery)."""
    method_signed, target_signed, body_signed = signed_for or (method, target, body)
    headers = sign_headers(secret, method_signed, target_signed, body_signed, now)
    if body:
        headers["Content-Type"] = "application/json"
    return await http.request(method, target, content=body or None, headers=headers)


def remote_with(handler) -> RemoteRunner:
    return RemoteRunner("http://runner.test", SECRET, transport=httpx.MockTransport(handler))


# ======================================================================================================= setting up
class TestSettingUp:
    @pytest.mark.parametrize("secret", [None, "", "short", "x" * 31])
    def test_a_runner_without_a_proper_secret_does_not_start(self, secret):
        with pytest.raises(RuntimeError, match="RUNNER_SECRET"):
            create_runner_app(Settings(_env_file=None, runner_secret=secret), FakeManager())  # type: ignore[arg-type]

    async def test_the_health_check_is_open_and_says_nothing_private(self, http):
        response = await http.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "running": 0, "room": 5, "user_per_bot": False}

    def test_the_health_check_says_whether_every_bot_has_a_user_of_its_own(self, monkeypatch):
        monkeypatch.setattr(sandbox, "can_switch_users", lambda: True)
        monkeypatch.setattr(sandbox, "missing_capabilities", lambda: [])
        own = TestClient(create_runner_app(make_settings(runner_uid_base=41000), FakeManager()))  # type: ignore[arg-type]
        assert own.get("/healthz").json()["user_per_bot"] is True
        shared = TestClient(create_runner_app(make_settings(), FakeManager()))  # type: ignore[arg-type]
        assert shared.get("/healthz").json()["user_per_bot"] is False
        monkeypatch.setattr(sandbox, "can_switch_users", lambda: False)  # not root: the number is ignored
        not_root = TestClient(create_runner_app(make_settings(runner_uid_base=41000), FakeManager()))  # type: ignore[arg-type]
        assert not_root.get("/healthz").json()["user_per_bot"] is False

    def test_a_runner_that_is_root_without_what_it_needs_refuses_to_start(self, monkeypatch):
        monkeypatch.setattr(sandbox, "can_switch_users", lambda: True)
        monkeypatch.setattr(sandbox, "missing_capabilities", lambda: ["KILL"])
        with pytest.raises(RuntimeError, match="KILL"):
            create_runner_app(make_settings(runner_uid_base=41000), FakeManager())  # type: ignore[arg-type]
        create_runner_app(make_settings(), FakeManager())  # type: ignore[arg-type]  # without the setting nothing is needed

    async def test_no_answer_is_cached_or_guessed_at(self, http):
        for response in (await http.get("/healthz"), await http.get(f"/v1/runs/{BOT}"), await http.get("/nothing")):
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/", "/v1", "/v1/runs", "/v1/runs/"])
    async def test_nothing_else_is_there(self, http, path):
        response = await http.get(path)
        assert response.status_code == 404 and response.json()["kind"] == "error"

    async def test_a_wrong_method_is_told_so_in_the_same_way(self, http):
        response = await http.post(f"/v1/runs/{BOT}")
        assert response.status_code == 405 and response.json()["kind"] == "error"

    def test_a_health_check_does_not_touch_the_disk_and_a_first_call_makes_the_manager(self, tmp_path):
        folder = tmp_path / "runs"
        app = create_runner_app(make_settings(runner_data_dir=str(folder), runner_mode="local"))
        client = TestClient(app)
        assert client.get("/healthz").json()["running"] == 0
        assert not folder.exists()
        target = f"/v1/runs/{BOT}"
        response = client.get(target, headers=sign_headers(SECRET, "GET", target))
        assert response.status_code == 200 and response.json()["state"] == "none"

    def test_bots_are_stopped_when_the_runner_service_is_shut_down(self):
        manager = FakeManager()
        with TestClient(create_runner_app(make_settings(), manager)) as client:  # type: ignore[arg-type]
            assert client.get("/healthz").json()["ok"] is True
            assert manager.shut_down is False
        assert manager.shut_down is True


# ======================================================================================================= who may call
class TestWhoMayCall:
    @pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
    async def test_a_call_that_is_not_signed_gets_nothing(self, http, manager, method):
        response = await http.request(
            method, f"/v1/runs/{BOT}", content=start_request().to_json() if method == "PUT" else None
        )
        assert response.status_code == 401 and response.json()["kind"] == "not_allowed"
        assert manager.calls == []

    async def test_another_secret_gets_nothing_and_learns_nothing(self, http, manager):
        response = await send(http, "GET", f"/v1/runs/{BOT}", secret="z" * 40)
        assert response.status_code == 401 and manager.calls == []
        assert SECRET not in response.text and "z" * 40 not in response.text

    @pytest.mark.parametrize("age", [-120, 120])
    async def test_a_call_from_long_ago_or_the_future_gets_nothing(self, http, manager, age):
        response = await send(http, "GET", f"/v1/runs/{BOT}", now=time.time() + age)
        assert response.status_code == 401 and manager.calls == []

    async def test_a_copy_of_a_call_is_not_accepted_a_second_time(self, http, manager):
        target = f"/v1/runs/{BOT}"
        headers = sign_headers(SECRET, "GET", target)
        first = await http.get(target, headers=headers)
        second = await http.get(target, headers=headers)
        assert first.status_code == 200 and second.status_code == 401
        assert len(manager.calls) == 1

    @pytest.mark.parametrize(
        ("method", "target", "body", "signed_for"),
        [
            # a signature for reading the messages can't be used to stop the bot
            ("DELETE", f"/v1/runs/{BOT}?why=you", b"", ("GET", f"/v1/runs/{BOT}?why=you", b"")),
            # ...or for another bot
            ("GET", f"/v1/runs/{OTHER_BOT}", b"", ("GET", f"/v1/runs/{BOT}", b"")),
            # ...or with other numbers in the query
            ("GET", f"/v1/runs/{BOT}?after=9", b"", ("GET", f"/v1/runs/{BOT}?after=0", b"")),
            ("GET", f"/v1/runs/{BOT}?run={RUN}", b"", ("GET", f"/v1/runs/{BOT}", b"")),
            # ...and a start can't be changed on its way
            ("PUT", f"/v1/runs/{BOT}", b'{"x": 2}', ("PUT", f"/v1/runs/{BOT}", b'{"x": 1}')),
            ("PUT", f"/v1/runs/{BOT}?replace=0", b"{}", ("PUT", f"/v1/runs/{BOT}?replace=1", b"{}")),
        ],
    )
    async def test_a_signature_is_good_for_exactly_one_call(self, http, manager, method, target, body, signed_for):
        response = await send(http, method, target, body, signed_for=signed_for)
        assert response.status_code == 401 and response.json()["kind"] == "not_allowed"
        assert manager.calls == []

    async def test_an_oversized_body_is_refused_before_it_is_read(self, http, manager):
        response = await send(http, "PUT", f"/v1/runs/{BOT}", b"x" * 3_000_000)
        assert response.status_code == 413 and manager.calls == []


# ======================================================================================================= what it does
class TestWhatItDoes:
    async def test_a_start_reaches_the_manager_as_it_was_sent(self, remote, manager):
        wanted = start_request(files=[("data.json", b'{"a": 1}'), ("pic.png", bytes(range(256)))])
        answer = await remote.start(wanted)
        ((name, got, replace),) = manager.calls
        assert name == "start" and replace is True
        assert (got.bot_id, got.owner, got.code, got.token) == (BOT, OWNER, wanted.code, TOKEN)
        assert got.files == wanted.files
        assert answer["state"] == "starting" and answer["lines"][0]["text"] == "Starting your bot…"

    async def test_a_start_can_ask_not_to_replace_a_bot_that_is_running(self, remote, manager):
        await remote.start(start_request(), replace=False)
        assert manager.calls[0][2] is False

    async def test_stopping_says_why(self, remote, manager):
        await remote.stop(BOT)
        await remote.stop(BOT, "removed")
        assert manager.calls == [("stop", BOT, "you"), ("stop", BOT, "removed")]

    async def test_the_status_carries_where_the_reader_is(self, remote, manager):
        answer = await remote.status(BOT, after=3, known=RUN)
        assert manager.calls == [("status", BOT, 3, RUN)]
        assert answer["reset"] is True  # RUN is not the run the manager has, so the reader starts over
        await remote.status(BOT, after=-5)
        assert manager.calls[-1] == ("status", BOT, 0, None)

    async def test_an_answer_is_a_run_as_the_site_expects_it(self, remote):
        answer = await remote.status(BOT)
        assert {"now", "run", "state", "reason", "ready", "exit_code", "started_at", "ended_at", "lines"} <= set(answer)
        assert {"next", "skipped", "reset"} <= set(answer)


# ======================================================================================================= mistakes
class TestMistakes:
    @pytest.mark.parametrize(
        ("method", "target"),
        [
            ("GET", "/v1/runs/not-an-id"),
            ("DELETE", "/v1/runs/" + "A" * 32),
            ("GET", f"/v1/runs/{BOT}?after=-1"),
            ("GET", f"/v1/runs/{BOT}?after=abc"),
            ("GET", f"/v1/runs/{BOT}?after=99999999999"),
            ("GET", f"/v1/runs/{BOT}?run=short"),
            ("GET", f"/v1/runs/{BOT}?run={'Z' * 32}"),
            ("DELETE", f"/v1/runs/{BOT}?why=hax"),
            ("PUT", f"/v1/runs/{BOT}?replace=maybe"),
        ],
    )
    async def test_a_call_that_makes_no_sense_is_refused_and_the_manager_never_hears_of_it(
        self, http, manager, method, target
    ):
        body = start_request().to_json() if method == "PUT" else b""
        response = await send(http, method, target, body)
        assert response.status_code == 422 and response.json() == {
            "detail": "That request wasn't valid.",
            "kind": "refused",
        }
        assert manager.calls == []

    @pytest.mark.parametrize(
        "body",
        [
            b"[]",
            b"null",
            b'"text"',
            b'{"bot_id": "x"}',
            json.dumps({"bot_id": OTHER_BOT, "owner": OWNER, "code": "x", "token": TOKEN, "files": []}).encode(),
        ],
    )
    async def test_a_start_that_is_not_one_is_refused(self, http, manager, body):
        response = await send(http, "PUT", f"/v1/runs/{BOT}", body)
        assert response.status_code == 422 and response.json()["kind"] == "refused"
        assert manager.calls == []

    @pytest.mark.parametrize("body", [b"not json", b"{", b'{"a": NaN}', b"\xff\xfe"])
    async def test_a_start_that_is_not_even_json_is_a_plain_400(self, http, manager, body):
        response = await send(http, "PUT", f"/v1/runs/{BOT}", body)
        assert response.status_code == 400 and response.json()["kind"] == "error"
        assert manager.calls == []

    @pytest.mark.parametrize(
        ("error", "status", "kind"),
        [
            (Refused("This code failed the safety check, so it was not run."), 422, "refused"),
            (TooMany("You can run 2 bots at the same time. Stop one of them first."), 409, "too_many"),
            (Busy("The runner is full right now. Try again in a few minutes."), 503, "busy"),
        ],
    )
    async def test_what_the_manager_refuses_is_passed_on_in_words_and_comes_back_as_the_same_error(
        self, http, remote, manager, error, status, kind
    ):
        manager.fail_with = error
        response = await send(http, "PUT", f"/v1/runs/{BOT}", start_request().to_json())
        assert response.status_code == status and response.json() == {"detail": error.message, "kind": kind}
        with pytest.raises(type(error)) as caught:
            await remote.start(start_request())
        assert caught.value.message == error.message

    async def test_an_unexpected_failure_is_a_plain_500_and_the_log_has_no_secrets(self, http, remote, manager, caplog):
        manager.fail_with = RuntimeError(f"boom {TOKEN} {SECRET}")
        with caplog.at_level(logging.INFO):
            response = await send(http, "PUT", f"/v1/runs/{BOT}", start_request().to_json())
            with pytest.raises(Unavailable) as caught:
                await remote.start(start_request())
        assert response.status_code == 500
        assert response.json() == {"detail": "Something went wrong on the runner.", "kind": "error"}
        assert caught.value.message == DOWN_MESSAGE
        assert "RuntimeError" in caplog.text
        for secret in (TOKEN, TOKEN_SECRET_PART, SECRET):
            assert secret not in caplog.text and secret not in response.text


# ======================================================================================================= the site's side
class TestTheSitesSide:
    async def test_every_call_is_signed_for_exactly_what_it_sends_and_only_a_start_carries_the_token(self):
        verifier = Verifier(SECRET)
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            target = request.url.raw_path.decode()
            verifier.check(request.method, target, request.content, request.headers)
            seen.append((request.method, target))
            carries_token = TOKEN.encode() in request.content
            assert carries_token == (request.method == "PUT")
            assert TOKEN not in target and all(TOKEN not in value for value in request.headers.values())
            return httpx.Response(200, json=view(None, 0, None))

        runner = remote_with(handler)
        await runner.start(start_request())
        await runner.status(BOT, after=2, known=RUN)
        await runner.stop(BOT, "removed")
        await runner.close()
        assert seen == [
            ("PUT", f"/v1/runs/{BOT}?replace=1"),
            ("GET", f"/v1/runs/{BOT}?after=2&run={RUN}"),
            ("DELETE", f"/v1/runs/{BOT}?why=removed"),
        ]

    async def test_a_runner_that_cannot_be_reached_is_told_plainly_and_without_the_request(self, caplog):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot connect {TOKEN}", request=request)

        runner = remote_with(handler)
        with caplog.at_level(logging.INFO), pytest.raises(Unavailable) as caught:
            await runner.start(start_request())
        await runner.close()
        assert caught.value.message == DOWN_MESSAGE and caught.value.__cause__ is None
        assert "ConnectError" in caplog.text
        assert TOKEN not in caplog.text and TOKEN_SECRET_PART not in caplog.text

    async def test_a_redirect_is_not_followed(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(307, headers={"Location": "http://elsewhere.test/v1/runs"})

        runner = remote_with(handler)
        with pytest.raises(Unavailable):
            await runner.start(start_request())
        await runner.close()
        assert len(requests) == 1 and requests[0].url.host == "runner.test"

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(200, text="<html>hello</html>"),
            httpx.Response(200, json={"hello": "there"}),
            httpx.Response(200, json=["state"]),
            httpx.Response(204),
            httpx.Response(502, text="Bad gateway"),
            httpx.Response(500, json={"detail": "Traceback in /srv/runner/app.py", "kind": "error"}),
            httpx.Response(503, json={"detail": "Everything is on fire: /srv/secret", "kind": "unavailable"}),
            httpx.Response(401, json={"detail": "The signature doesn't match.", "kind": "not_allowed"}),
            httpx.Response(418, json={"detail": "I'm a teapot", "kind": "something-new"}),
        ],
    )
    async def test_anything_but_a_run_or_an_error_the_runner_knows_is_just_not_available(self, response):
        runner = remote_with(lambda request: response)
        with pytest.raises(Unavailable) as caught:
            await runner.status(BOT)
        await runner.close()
        assert caught.value.message == DOWN_MESSAGE

    async def test_a_runner_with_another_secret_is_reported_in_the_log_for_the_operator_only(self, app, caplog):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        wrong = RemoteRunner("http://runner.test", "q" * 40, transport=transport)
        with caplog.at_level(logging.INFO), pytest.raises(Unavailable) as caught:
            await wrong.start(start_request())
        await wrong.close()
        assert caught.value.message == DOWN_MESSAGE
        assert "RUNNER_SECRET" in caplog.text and TOKEN not in caplog.text and "q" * 40 not in caplog.text

    async def test_a_refusal_from_the_runner_is_relayed_but_kept_short(self):
        runner = remote_with(lambda request: httpx.Response(422, json={"detail": "x" * 5000, "kind": "refused"}))
        with pytest.raises(Refused) as caught:
            await runner.start(start_request())
        await runner.close()
        assert caught.value.message == "x" * 300
