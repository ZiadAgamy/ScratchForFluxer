"""Login with Fluxer (OAuth2 code flow + PKCE), sessions and logout, against a mock Fluxer API."""

from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from conftest import make_session_cookie
from sqlalchemy import select

from scratch_fluxer import config, deps
from scratch_fluxer.db import _call_with_session
from scratch_fluxer.models import User

API = "https://api.fluxer.app/v1"
TOKEN_URL = f"{API}/oauth2/token"
USERINFO_URL = f"{API}/oauth2/userinfo"
REVOKE_URL = f"{API}/oauth2/token/revoke"
ACCESS_TOKEN = "at-very-secret-access-token-0123456789"

PROFILE = {"id": "100200300400500600", "username": "agamy", "global_name": "AGAMY", "avatar": "abcdef123456"}


@pytest.fixture
def fluxer():
    """A mock Fluxer API. Routes are exposed so tests can inspect or change them."""
    with respx.mock(assert_all_called=False) as router:
        router.token = router.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": ACCESS_TOKEN, "token_type": "Bearer"})
        )
        router.userinfo = router.get(USERINFO_URL).mock(return_value=httpx.Response(200, json=PROFILE))
        router.revoke = router.post(REVOKE_URL).mock(return_value=httpx.Response(200))
        yield router


def start_login(client, **params):
    """Hit /auth/login and return (authorize_url_query, location)."""
    r = client.get("/auth/login", params=params, follow_redirects=False)
    assert r.status_code == 302, r.text
    location = r.headers["location"]
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}, location


def finish_login(client, state, code="the-code-from-fluxer", **extra):
    return client.get("/auth/callback", params={"code": code, "state": state, **extra}, follow_redirects=False)


def users():
    return _call_with_session(lambda db: list(db.execute(select(User)).scalars()), (), {})


# --------------------------------------------------------------------------- step 1: send to Fluxer
class TestLoginStart:
    def test_redirects_to_the_fluxer_consent_screen(self, client, fluxer):
        query, location = start_login(client)
        assert location.startswith("https://api.fluxer.app/v1/oauth2/authorize?")
        assert query["response_type"] == "code"
        assert query["client_id"] == "1111222233334444"
        assert query["redirect_uri"] == "http://testserver/auth/callback"
        assert query["scope"] == "identify"
        assert len(query["state"]) >= 32
        assert query["code_challenge_method"] == "S256" and len(query["code_challenge"]) >= 43
        assert "client_secret" not in location and "client-secret-do-not-leak" not in location

    def test_state_cookie_is_locked_down(self, client, fluxer):
        r = client.get("/auth/login", follow_redirects=False)
        cookie = r.headers["set-cookie"].lower()
        assert deps.OAUTH_COOKIE in cookie
        assert "httponly" in cookie and "samesite=lax" in cookie and "path=/auth" in cookie
        assert "max-age=600" in cookie

    def test_every_attempt_gets_a_fresh_state_and_challenge(self, client, fluxer):
        a, _ = start_login(client)
        b, _ = start_login(client)
        assert a["state"] != b["state"] and a["code_challenge"] != b["code_challenge"]

    def test_pkce_can_be_switched_off_for_other_fluxer_instances(self, client, fluxer, monkeypatch):
        monkeypatch.setenv("FLUXER_OAUTH_PKCE", "false")
        config.reset_settings_cache()
        from fastapi.testclient import TestClient

        from scratch_fluxer.main import create_app

        with TestClient(create_app()) as c:
            query, _ = start_login(c)
        assert "code_challenge" not in query

    def test_not_configured_gives_a_clear_page(self, monkeypatch):
        from fastapi.testclient import TestClient

        from scratch_fluxer.main import create_app

        monkeypatch.setenv("FLUXER_CLIENT_SECRET", "")
        config.reset_settings_cache()
        with TestClient(create_app()) as c:
            r = c.get("/auth/login", follow_redirects=False)
        assert r.status_code == 503 and "FLUXER_CLIENT_SECRET" in r.text

    def test_is_rate_limited(self, client, fluxer):
        codes = [client.get("/auth/login", follow_redirects=False).status_code for _ in range(32)]
        assert codes[:30] == [302] * 30
        assert codes[30:] == [429, 429]


# --------------------------------------------------------------------------- step 2: come back from Fluxer
class TestLoginCallback:
    def test_happy_path(self, client, fluxer):
        query, _ = start_login(client)
        r = finish_login(client, query["state"])

        assert r.status_code == 303
        assert r.headers["location"] == "/bots"

        cookies = r.headers.get_list("set-cookie")
        session = next(c for c in cookies if c.startswith(deps.SESSION_COOKIE + "="))
        assert all(flag in session.lower() for flag in ("httponly", "samesite=lax", "path=/"))
        assert "max-age=" in session.lower()
        assert any(c.startswith(deps.OAUTH_COOKIE + "=") and ("max-age=0" in c.lower() or "1970" in c) for c in cookies)

        rows = users()
        assert len(rows) == 1
        assert (rows[0].fluxer_id, rows[0].username, rows[0].global_name, rows[0].avatar_hash) == (
            "100200300400500600",
            "agamy",
            "AGAMY",
            "abcdef123456",
        )

    def test_code_exchange_carries_the_pkce_verifier(self, client, fluxer):
        query, _ = start_login(client)
        finish_login(client, query["state"], code="abc123")
        sent = parse_qs(fluxer.token.calls.last.request.content.decode())
        assert sent["grant_type"] == ["authorization_code"]
        assert sent["code"] == ["abc123"]
        assert sent["redirect_uri"] == ["http://testserver/auth/callback"]
        assert sent["client_id"] == ["1111222233334444"]
        assert sent["client_secret"] == ["client-secret-do-not-leak"]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(sent["code_verifier"][0].encode()).digest()).rstrip(b"=").decode()
        )
        assert challenge == query["code_challenge"]

    def test_userinfo_is_read_with_the_bearer_token(self, client, fluxer):
        query, _ = start_login(client)
        finish_login(client, query["state"])
        assert fluxer.userinfo.calls.last.request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"

    def test_the_access_token_is_revoked_and_never_kept(self, client, fluxer, tmp_path):
        query, _ = start_login(client)
        r = finish_login(client, query["state"])

        assert fluxer.revoke.called
        revoked = parse_qs(fluxer.revoke.calls.last.request.content.decode())
        assert revoked["token"] == [ACCESS_TOKEN]

        everything = r.text + str(r.headers) + str(client.cookies.items())
        assert ACCESS_TOKEN not in everything
        db_bytes = b"".join(
            p.read_bytes()
            for p in __import__("pathlib")
            .Path(config.get_settings().effective_database_url.removeprefix("sqlite:///"))
            .parent.glob("test.db*")
        )
        assert ACCESS_TOKEN.encode() not in db_bytes

    def test_redirects_to_a_safe_next_page(self, client, fluxer):
        query, _ = start_login(client, next="/bots/new")
        assert finish_login(client, query["state"]).headers["location"] == "/bots/new"

    @pytest.mark.parametrize("evil", ["https://evil.example", "//evil.example", "/\\evil.example", "/api/me"])
    def test_never_redirects_to_an_unsafe_next_page(self, client, fluxer, evil):
        query, _ = start_login(client, next=evil)
        assert finish_login(client, query["state"]).headers["location"] == "/bots"

    def test_logging_in_again_updates_the_profile_instead_of_duplicating(self, client, fluxer):
        query, _ = start_login(client)
        finish_login(client, query["state"])
        fluxer.userinfo.mock(
            return_value=httpx.Response(
                200, json={**PROFILE, "username": "agamy2", "global_name": None, "avatar": None}
            )
        )
        query, _ = start_login(client)
        finish_login(client, query["state"])
        rows = users()
        assert (
            len(rows) == 1
            and rows[0].username == "agamy2"
            and rows[0].global_name is None
            and rows[0].avatar_hash is None
        )

    def test_after_login_the_site_knows_you(self, client, fluxer):
        query, _ = start_login(client)
        finish_login(client, query["state"])
        page = client.get("/")
        assert "AGAMY" in page.text and "My bots" in page.text
        assert client.get("/login", follow_redirects=False).status_code == 303

    def test_a_new_login_replaces_the_session_and_rotates_the_csrf_token(self, client, fluxer):
        query, _ = start_login(client)
        finish_login(client, query["state"])
        csrf_a = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/").text).group(1)
        query, _ = start_login(client)
        fluxer.userinfo.mock(
            return_value=httpx.Response(200, json={**PROFILE, "id": "999", "username": "other", "global_name": "Other"})
        )
        finish_login(client, query["state"])
        page = client.get("/")
        assert "Other" in page.text and "AGAMY" not in page.text
        assert (
            re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1) != csrf_a
        )  # fresh CSRF token per login


# --------------------------------------------------------------------------- failures
class TestLoginCallbackFailures:
    def assert_no_session(self, client, response, status):
        assert response.status_code == status
        assert "text/html" in response.headers["content-type"]
        assert not any(c.startswith(deps.SESSION_COOKIE + "=") for c in response.headers.get_list("set-cookie"))
        assert users() == []
        assert "Back to login" in response.text

    def test_wrong_state_is_refused_before_talking_to_fluxer(self, client, fluxer):
        start_login(client)
        r = finish_login(client, "not-the-state-we-issued")
        self.assert_no_session(client, r, 400)
        assert not fluxer.token.called

    def test_missing_state_cookie_is_refused(self, client, fluxer):
        query, _ = start_login(client)
        client.cookies.clear()
        r = finish_login(client, query["state"])
        self.assert_no_session(client, r, 400)
        assert not fluxer.token.called

    def test_tampered_state_cookie_is_refused(self, client, fluxer):
        query, _ = start_login(client)
        damaged = client.cookies[deps.OAUTH_COOKIE][:-3] + "xyz"
        client.cookies.delete(
            deps.OAUTH_COOKIE
        )  # the damaged cookie must be the only one sent, not one beside the real one
        client.cookies.set(deps.OAUTH_COOKIE, damaged, path="/auth")
        assert [c.value for c in client.cookies.jar if c.name == deps.OAUTH_COOKIE] == [damaged]
        self.assert_no_session(client, finish_login(client, query["state"]), 400)

    def test_state_is_required(self, client, fluxer):
        start_login(client)
        r = client.get("/auth/callback", params={"code": "abc"}, follow_redirects=False)
        self.assert_no_session(client, r, 400)

    def test_missing_code(self, client, fluxer):
        query, _ = start_login(client)
        r = client.get("/auth/callback", params={"state": query["state"]}, follow_redirects=False)
        self.assert_no_session(client, r, 400)
        assert not fluxer.token.called

    def test_absurdly_long_code(self, client, fluxer):
        query, _ = start_login(client)
        self.assert_no_session(client, finish_login(client, query["state"], code="x" * 600), 400)
        assert not fluxer.token.called

    def test_user_cancelled(self, client, fluxer):
        query, _ = start_login(client)
        r = client.get(
            "/auth/callback", params={"error": "access_denied", "state": query["state"]}, follow_redirects=False
        )
        self.assert_no_session(client, r, 400)
        assert "cancelled" in r.text

    def test_other_oauth_error_does_not_echo_fluxers_text(self, client, fluxer):
        query, _ = start_login(client)
        r = client.get(
            "/auth/callback",
            params={"error": "server_error", "error_description": "<b>boo</b>", "state": query["state"]},
            follow_redirects=False,
        )
        self.assert_no_session(client, r, 400)
        assert "<b>boo</b>" not in r.text

    def test_fluxer_rejects_the_code(self, client, fluxer):
        fluxer.token.mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
        query, _ = start_login(client)
        self.assert_no_session(client, finish_login(client, query["state"]), 400)
        assert not fluxer.userinfo.called

    def test_token_response_without_a_token(self, client, fluxer):
        fluxer.token.mock(return_value=httpx.Response(200, json={"token_type": "Bearer"}))
        query, _ = start_login(client)
        self.assert_no_session(client, finish_login(client, query["state"]), 400)

    def test_fluxer_is_down(self, client, fluxer):
        fluxer.token.mock(return_value=httpx.Response(503))
        query, _ = start_login(client)
        r = finish_login(client, query["state"])
        self.assert_no_session(client, r, 502)
        assert "Fluxer" in r.text

    def test_network_failure(self, client, fluxer):
        fluxer.token.mock(side_effect=httpx.ConnectTimeout("timed out"))
        query, _ = start_login(client)
        self.assert_no_session(client, finish_login(client, query["state"]), 502)

    def test_userinfo_failure_still_revokes_the_token(self, client, fluxer):
        fluxer.userinfo.mock(return_value=httpx.Response(401, json={"message": "nope"}))
        query, _ = start_login(client)
        self.assert_no_session(client, finish_login(client, query["state"]), 400)
        assert fluxer.revoke.called

    def test_userinfo_without_a_username_is_rejected(self, client, fluxer):
        fluxer.userinfo.mock(return_value=httpx.Response(200, json={"id": "1"}))
        query, _ = start_login(client)
        self.assert_no_session(client, finish_login(client, query["state"]), 400)

    def test_a_failing_revoke_call_does_not_block_login(self, client, fluxer):
        fluxer.revoke.mock(return_value=httpx.Response(500))
        query, _ = start_login(client)
        assert finish_login(client, query["state"]).status_code == 303
        fluxer.revoke.mock(side_effect=httpx.ConnectTimeout("slow"))
        query, _ = start_login(client)
        assert finish_login(client, query["state"]).status_code == 303

    def test_error_pages_never_leak_the_client_secret(self, client, fluxer):
        fluxer.token.mock(
            return_value=httpx.Response(
                400, json={"error": "invalid_client", "error_description": "client-secret-do-not-leak"}
            )
        )
        query, _ = start_login(client)
        r = finish_login(client, query["state"])
        assert "client-secret-do-not-leak" not in r.text


# --------------------------------------------------------------------------- sessions
class TestSessions:
    def logged_in(self, client, fluxer):
        query, _ = start_login(client)
        finish_login(client, query["state"])

    def test_anonymous_without_cookie(self, client):
        assert client.get("/login", follow_redirects=False).status_code == 200

    def test_tampered_cookie_is_anonymous(self, client, fluxer):
        self.logged_in(client, fluxer)
        damaged = client.cookies[deps.SESSION_COOKIE] + "x"
        client.cookies.delete(
            deps.SESSION_COOKIE
        )  # the damaged cookie must be the only one sent, not one beside the real one
        client.cookies.set(deps.SESSION_COOKIE, damaged)
        assert [c.value for c in client.cookies.jar if c.name == deps.SESSION_COOKIE] == [damaged]
        assert client.get("/login", follow_redirects=False).status_code == 200

    def test_cookie_for_a_deleted_user_is_anonymous(self, client, fluxer):
        self.logged_in(client, fluxer)
        _call_with_session(lambda db: [db.delete(u) for u in db.execute(select(User)).scalars()], (), {})
        assert client.get("/login", follow_redirects=False).status_code == 200

    def test_bumping_the_epoch_logs_out_every_device(self, client, fluxer):
        self.logged_in(client, fluxer)
        assert client.get("/login", follow_redirects=False).status_code == 303

        def bump(db):
            for user in db.execute(select(User)).scalars():
                user.session_epoch += 1

        _call_with_session(bump, (), {})
        assert client.get("/login", follow_redirects=False).status_code == 200

    def test_expired_sessions_are_rejected(self, client, fluxer, monkeypatch):
        self.logged_in(client, fluxer)
        monkeypatch.setattr(deps, "SESSION_MAX_AGE", -1)
        assert client.get("/login", follow_redirects=False).status_code == 200

    def test_a_cookie_signed_with_another_secret_is_rejected(self, client, make_user):
        from scratch_fluxer.security import SessionCodec

        user_id, csrf, _ = make_user()
        forged = SessionCodec("attacker-secret-attacker-secret-attacker!!").dump_session(
            {"u": user_id, "e": 0, "c": csrf}
        )
        client.cookies.set(deps.SESSION_COOKIE, forged)
        assert client.get("/login", follow_redirects=False).status_code == 200


# --------------------------------------------------------------------------- logout
class TestLogout:
    def test_logout_clears_the_session(self, client, make_user):
        _, csrf, cookies = make_user()
        client.cookies.update(cookies)
        r = client.post("/auth/logout", data={"csrf_token": csrf}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
        assert any(
            c.startswith(deps.SESSION_COOKIE + "=") and ("max-age=0" in c.lower() or "1970" in c)
            for c in r.headers.get_list("set-cookie")
        )
        client.cookies.clear()
        assert "Log in with Fluxer" in client.get("/").text

    def test_logout_needs_the_csrf_token(self, client, make_user):
        _, _, cookies = make_user()
        client.cookies.update(cookies)
        for data in ({}, {"csrf_token": "wrong"}, {"csrf_token": ""}):
            r = client.post("/auth/logout", data=data, follow_redirects=False)
            assert r.status_code == 403, data
        assert client.get("/login", follow_redirects=False).status_code == 303  # still logged in

    def test_logout_refuses_cross_site_posts(self, client, make_user):
        _, csrf, cookies = make_user()
        client.cookies.update(cookies)
        r = client.post(
            "/auth/logout",
            data={"csrf_token": csrf},
            headers={"Origin": "https://evil.example"},
            follow_redirects=False,
        )
        assert r.status_code == 403
        r = client.post(
            "/auth/logout", data={"csrf_token": csrf}, headers={"Sec-Fetch-Site": "cross-site"}, follow_redirects=False
        )
        assert r.status_code == 403

    def test_a_copy_of_the_cookie_is_useless_after_logging_out(self, client, make_user):
        _, csrf, cookies = make_user()
        client.cookies.update(cookies)
        assert client.get("/login", follow_redirects=False).status_code == 303  # logged in
        assert client.post("/auth/logout", data={"csrf_token": csrf}, follow_redirects=False).status_code == 303
        client.cookies.clear()
        client.cookies.update(cookies)  # somebody kept the cookie from before
        assert client.get("/login", follow_redirects=False).status_code == 200  # not logged in
        sent_away = client.get("/bots", follow_redirects=False)
        assert sent_away.status_code == 303 and sent_away.headers["location"].startswith("/login")

    def test_logging_in_again_after_logging_out_gives_a_cookie_that_works(self, client, make_user):
        user_id, csrf, cookies = make_user()
        client.cookies.update(cookies)
        client.post("/auth/logout", data={"csrf_token": csrf}, follow_redirects=False)
        client.cookies.clear()
        epoch = _call_with_session(lambda db: db.get(User, user_id).session_epoch, (), {})
        assert epoch == 1
        client.cookies.set(deps.SESSION_COOKIE, make_session_cookie(user_id, epoch, csrf))  # what a new login gives
        assert client.get("/login", follow_redirects=False).status_code == 303

    def test_a_wrong_origin_is_refused_before_the_body_is_looked_at(self, client, make_user):
        _, csrf, cookies = make_user()
        client.cookies.update(cookies)
        r = client.post(
            "/auth/logout",
            content=b"x" * 3_000_000,
            headers={"Origin": "https://evil.example", "Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert r.status_code == 403

    def test_a_big_body_is_refused_and_does_not_log_out(self, client, make_user):
        _, csrf, cookies = make_user()
        client.cookies.update(cookies)
        r = client.post(
            "/auth/logout",
            content=b"csrf_token=" + csrf.encode() + b"&junk=" + b"x" * 10_000,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert r.status_code == 413
        assert client.get("/login", follow_redirects=False).status_code == 303  # still logged in

    def test_logging_out_when_already_logged_out_is_harmless(self, client):
        r = client.post("/auth/logout", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"

    def test_logout_is_post_only(self, client):
        assert client.get("/auth/logout").status_code == 405
