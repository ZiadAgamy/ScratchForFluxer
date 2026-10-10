"""Shared fixtures.

The environment is set *before* any ``scratch_fluxer`` module is imported, so the app never
touches a developer's real ``.env`` file, database or secrets while testing.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="sff-tests-"))

TEST_ENV = {
    "APP_ENV": "test",
    "SECRET_KEY": "test-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz",
    "APP_BASE_URL": "http://testserver",
    "FLUXER_CLIENT_ID": "1111222233334444",
    "FLUXER_CLIENT_SECRET": "client-secret-do-not-leak",
    "DATABASE_URL": f"sqlite:///{_TMP / 'test.db'}",
    "RUNNER_MODE": "off",
    "RUNNER_DATA_DIR": str(_TMP / "runs"),
}
os.environ.update(TEST_ENV)
for _name in (
    "VERCEL",
    "VERCEL_PROJECT_PRODUCTION_URL",
    "VERCEL_GIT_COMMIT_SHA",
    "DATABASE_URL_UNPOOLED",
    "POSTGRES_URL",
):
    os.environ.pop(_name, None)

from fastapi.testclient import TestClient  # noqa: E402

from scratch_fluxer import config, db, deps  # noqa: E402
from scratch_fluxer.runner import service as runner_service  # noqa: E402
from scratch_fluxer.security import limiter  # noqa: E402


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    """Every test starts with default settings, an empty database and empty rate-limit buckets."""
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    db.reset_engine()
    (_TMP / "test.db").unlink(missing_ok=True)
    config.reset_settings_cache()
    deps.reset_singletons()
    limiter.reset()
    runner_service.set_runner(None)
    yield
    db.reset_engine()
    runner_service.set_runner(None)


@pytest.fixture
def settings():
    return config.get_settings()


@pytest.fixture
def app():
    from scratch_fluxer.main import create_app

    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client


def make_session_cookie(user_id: str, epoch: int = 0, csrf: str = "csrf-token-for-tests") -> str:
    """A valid signed session cookie for ``user_id`` (skips the OAuth dance in unrelated tests)."""
    import time

    return deps.get_codec().dump_session({"u": user_id, "e": epoch, "c": csrf, "t": int(time.time())})


@pytest.fixture
def make_user():
    """Create a user row and return ``(user_id, csrf, cookies)`` ready for ``client.cookies.update``."""

    def _make(fluxer_id: str = "900000000000001", username: str = "tester", global_name: str | None = "Test Er"):
        from scratch_fluxer.models import User

        def _insert(session):
            user = User(fluxer_id=fluxer_id, username=username, global_name=global_name)
            session.add(user)
            session.flush()
            return user.id, user.session_epoch

        from scratch_fluxer.db import _call_with_session

        user_id, epoch = _call_with_session(_insert, (), {})
        csrf = "csrf-token-for-tests"
        return user_id, csrf, {deps.SESSION_COOKIE: make_session_cookie(user_id, epoch, csrf)}

    return _make


@pytest.fixture
def me(client, make_user):
    """A logged-in client. ``me.h`` is the header for state-changing calls, ``me.user_id`` the person's id."""
    from fluxer_mock import OWNER_FLUXER_ID

    user_id, csrf, cookies = make_user(fluxer_id=OWNER_FLUXER_ID, username="agamy", global_name="AGAMY")
    client.cookies.update(cookies)
    client.user_id = user_id
    client.csrf = csrf
    client.h = {"X-CSRF-Token": csrf}
    return client


@pytest.fixture
def stranger(app, make_user):
    """Another logged-in person, with their own client, who owns none of ``me``'s bots."""
    user_id, csrf, cookies = make_user(fluxer_id="900000000000002", username="stranger", global_name="Some One")
    with TestClient(app, base_url="http://testserver") as other:
        other.cookies.update(cookies)
        other.user_id = user_id
        other.csrf = csrf
        other.h = {"X-CSRF-Token": csrf}
        yield other


@pytest.fixture
def new_bot(me, fluxer):
    """``new_bot(token=TOKEN, **extra)`` adds a bot through the API (Fluxer is mocked) and returns its id."""
    from fluxer_mock import TOKEN

    def _add(token: str = TOKEN, **extra) -> str:
        r = me.post("/api/bots", json={"token": token, **extra}, headers=me.h)
        assert r.status_code == 201, r.text
        return r.json()["bot"]["id"]

    return _add


@pytest.fixture
def fluxer():
    """Mock Fluxer: a token is valid for the application registered in ``router.apps`` under that token.

    respx patches httpx for the whole process, so this also answers the server inside ``live_server``.
    """
    import httpx
    import respx
    from fluxer_mock import APP_ME, TOKEN, TOKEN_1_ROTATED, TOKEN_2, application

    with respx.mock(assert_all_called=False) as router:
        router.apps = {
            TOKEN: application(),
            TOKEN_2: application("222222222222222222", "Quiz Bot", bot_username="quizbot"),
        }
        router.apps[TOKEN_1_ROTATED] = application()  # same bot, new token

        def respond(request: httpx.Request) -> httpx.Response:
            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("Bot ")
            if not auth.startswith("Bot ") or token not in router.apps:
                return httpx.Response(401, json={"code": "INVALID_TOKEN", "message": "Invalid token"})
            return httpx.Response(200, json=router.apps[token])

        router.me = router.get(APP_ME).mock(side_effect=respond)
        yield router


# --------------------------------------------------------------------------- browser tests
def _chromium_options() -> dict:
    """Use a Chromium the environment names (or the sandbox's preinstalled one); otherwise Playwright's own."""
    options: dict = {"args": ["--no-sandbox"]}
    explicit = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if explicit:
        options["executable_path"] = explicit
    elif Path("/opt/pw-browsers/chromium").exists():
        options["executable_path"] = "/opt/pw-browsers/chromium"
    return options


@pytest.fixture
def live_server(monkeypatch):
    """The real app on a real port (for Playwright). Yields its base URL, e.g. http://127.0.0.1:53211."""
    import socket
    import threading
    import time

    import uvicorn

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("APP_BASE_URL", base)  # same-origin checks compare against it
    config.reset_settings_cache()
    deps.reset_singletons()

    from scratch_fluxer.main import create_app

    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("the test server did not start")
        time.sleep(0.02)
    yield base
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(**_chromium_options())
        yield chromium
        chromium.close()


@pytest.fixture
def new_page(browser, live_server):
    """``new_page(**context_options)`` -> a page on the live server. ``page.problems`` lists JavaScript errors."""
    contexts = []

    def _open(cookies: dict[str, str] | None = None, viewport: dict | None = None, **options):
        context = browser.new_context(viewport=viewport or {"width": 1280, "height": 900}, **options)
        contexts.append(context)
        if cookies:
            context.add_cookies([{"name": k, "value": v, "url": live_server} for k, v in cookies.items()])
        page = context.new_page()
        page.problems = []
        page.on("pageerror", lambda error: page.problems.append(f"pageerror: {error}"))

        def on_console(message):
            # Avatars come from Fluxer's CDN, which the sandbox can't reach; that is not our bug.
            origin = (message.location or {}).get("url", "")
            if message.type == "error" and "fluxer" not in origin:
                page.problems.append(f"console error: {message.text} ({origin})")

        page.on("console", on_console)
        return page

    yield _open
    for context in contexts:
        context.close()


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Browser tests go last, and are skipped when Playwright/Chromium isn't installed.

    Playwright's sync API keeps an event loop "running" in the main thread for as long as its (session-wide) browser is
    open, and pytest-asyncio refuses to start a test while a loop is running. So every test that needs the browser has to
    come after the last asyncio test. (The sort is stable: tests keep their order within each group.)
    """
    items.sort(key=lambda item: "browser" in item.keywords)
    try:
        import playwright  # noqa: F401

        have_playwright = Path("/opt/pw-browsers").exists() or bool(os.environ.get("PLAYWRIGHT_BROWSERS_PATH"))
        have_playwright = have_playwright or Path.home().joinpath(".cache/ms-playwright").exists()
    except ImportError:
        have_playwright = False
    if have_playwright:
        return
    skip = pytest.mark.skip(reason="Playwright/Chromium not available")
    for item in items:
        if "browser" in item.keywords:
            item.add_marker(skip)
