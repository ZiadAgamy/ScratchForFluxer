"""Bots API: verify a token, add, rename, replace the token, remove, and delete the account.

Fluxer is mocked (``GET /v1/applications/@me`` with ``Authorization: Bot <token>``), shaped like the
real response documented in Fluxer's OpenAPI schema (``ApplicationsMeResponse``).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import HTTPException
from fluxer_mock import OWNER_FLUXER_ID, TOKEN, TOKEN_1_ROTATED, TOKEN_2, application
from sqlalchemy import func, select

from scratch_fluxer import deps
from scratch_fluxer import fluxer_api as fx
from scratch_fluxer.config import get_settings
from scratch_fluxer.db import _call_with_session
from scratch_fluxer.models import Asset, Bot, User


def all_bots():
    return _call_with_session(lambda db: list(db.execute(select(Bot)).scalars()), (), {})


def add(me, token=TOKEN, **extra):
    return me.post("/api/bots", json={"token": token, **extra}, headers=me.h)


# --------------------------------------------------------------------------- verify
class TestVerify:
    def test_valid_token(self, me, fluxer):
        r = me.post("/api/bots/verify", json={"token": TOKEN}, headers=me.h)
        assert r.status_code == 200
        body = r.json()
        assert body["bot"]["application_name"] == "Ping Bot"
        assert body["bot"]["bot_username"] == "pingbot"
        assert body["bot"]["default_name"] == "Ping Bot"
        assert body["bot"]["avatar_url"].startswith("https://fluxerusercontent.com/avatars/111111111111111111/abc123")
        assert body["already_added"] is None
        assert all_bots() == []  # verifying stores nothing

    def test_fluxer_is_asked_with_the_bot_scheme(self, me, fluxer):
        me.post("/api/bots/verify", json={"token": f"Bot {TOKEN}"}, headers=me.h)  # pasted with the "Bot " prefix
        assert fluxer.me.calls.last.request.headers["authorization"] == f"Bot {TOKEN}"

    def test_the_token_is_not_echoed_back(self, me, fluxer):
        assert TOKEN not in me.post("/api/bots/verify", json={"token": TOKEN}, headers=me.h).text

    def test_reports_a_bot_that_is_already_added(self, me, fluxer):
        bot_id = add(me).json()["bot"]["id"]
        body = me.post("/api/bots/verify", json={"token": TOKEN}, headers=me.h).json()
        assert body["already_added"] == {"id": bot_id, "name": "Ping Bot"}

    @pytest.mark.parametrize("pasted", ["", "   ", "not a token", "abc", "123456789012345678", "x" * 400])
    def test_obvious_mistakes_never_reach_fluxer(self, me, fluxer, pasted):
        r = me.post("/api/bots/verify", json={"token": pasted}, headers=me.h)
        assert r.status_code == 422 and isinstance(r.json()["detail"], str) and r.json()["detail"]
        assert not fluxer.me.called

    @pytest.mark.parametrize("value", [None, 123, ["a"], {"a": 1}, True])
    def test_token_must_be_text(self, me, fluxer, value):
        assert me.post("/api/bots/verify", json={"token": value}, headers=me.h).status_code == 422

    def test_fluxer_rejects_the_token(self, me, fluxer):
        r = me.post("/api/bots/verify", json={"token": "333333333333333333.ZZZZZZZZZZZZZZZZZZZZZZZZZZ"}, headers=me.h)
        assert r.status_code == 400 and "rejected" in r.json()["detail"]

    def test_a_token_that_is_not_a_bot_token(self, me, fluxer):
        fluxer.apps[TOKEN] = {**application(), "bot": None}
        r = me.post("/api/bots/verify", json={"token": TOKEN}, headers=me.h)
        assert r.status_code == 400 and "isn't a bot token" in r.json()["detail"]

    def test_fluxer_down(self, me, fluxer):
        fluxer.me.mock(return_value=httpx.Response(503))
        assert me.post("/api/bots/verify", json={"token": TOKEN}, headers=me.h).status_code == 502
        fluxer.me.mock(side_effect=httpx.ConnectTimeout("slow"))
        assert me.post("/api/bots/verify", json={"token": TOKEN}, headers=me.h).status_code == 502

    def test_fluxer_rate_limit_is_explained(self, me, fluxer):
        fluxer.me.mock(return_value=httpx.Response(429))
        r = me.post("/api/bots/verify", json={"token": TOKEN}, headers=me.h)
        assert r.status_code == 502 and "rate-limiting" in r.json()["detail"]

    def test_only_your_own_bots_can_be_added(self, me, fluxer):
        fluxer.apps[TOKEN] = application(owner_id="555555555555555555")
        r = me.post("/api/bots/verify", json={"token": TOKEN}, headers=me.h)
        assert r.status_code == 403 and "different Fluxer account" in r.json()["detail"]
        assert add(me).status_code == 403
        assert all_bots() == []

    def test_is_rate_limited_per_person(self, me, fluxer):
        codes = [me.post("/api/bots/verify", json={"token": TOKEN}, headers=me.h).status_code for _ in range(14)]
        assert codes[:12] == [200] * 12 and codes[12:] == [429, 429]


# --------------------------------------------------------------------------- add
class TestAdd:
    def test_adds_a_bot_and_points_to_the_editor(self, me, fluxer):
        r = add(me)
        assert r.status_code == 201
        body = r.json()
        assert body["redirect"] == f"/bots/{body['bot']['id']}/edit"
        assert body["bot"]["name"] == "Ping Bot" and body["bot"]["token_last4"] == "1234"
        rows = all_bots()
        assert len(rows) == 1 and rows[0].user_id == me.user_id
        assert (rows[0].application_id, rows[0].bot_user_id, rows[0].bot_username) == ("111111111111111111",) * 2 + (
            "pingbot",
        )

    def test_a_custom_name(self, me, fluxer):
        assert add(me, name="  Pong   machine  ").json()["bot"]["name"] == "Pong machine"

    def test_the_token_is_encrypted_at_rest_and_never_returned(self, me, fluxer):
        response = add(me)
        assert TOKEN not in response.text
        row = all_bots()[0]
        assert TOKEN not in row.token_ciphertext and "AAAAAAAAAA" not in row.token_ciphertext
        assert deps.get_vault().decrypt(row.token_ciphertext) == TOKEN
        # ...and not on any page or API answer either.
        for path in ("/bots", f"/bots/{row.id}", "/api/bots", "/"):
            assert TOKEN not in me.get(path).text and row.token_ciphertext not in me.get(path).text

    def test_the_database_file_never_contains_the_plain_token(self, me, fluxer):
        from pathlib import Path

        add(me)
        db_dir = Path(get_settings().effective_database_url.removeprefix("sqlite:///")).parent
        assert TOKEN.encode() not in b"".join(p.read_bytes() for p in db_dir.glob("test.db*"))

    def test_a_bot_can_be_added_only_once(self, me, fluxer):
        first = add(me).json()["bot"]["id"]
        again = add(me, token=TOKEN_1_ROTATED)  # same bot, newer token
        assert again.status_code == 409
        assert again.json()["existing_id"] == first and "already in your list" in again.json()["detail"]
        assert len(all_bots()) == 1

    def test_two_different_bots(self, me, fluxer):
        assert add(me).status_code == 201 and add(me, token=TOKEN_2).status_code == 201
        assert sorted(b.name for b in all_bots()) == ["Ping Bot", "Quiz Bot"]

    def test_bot_limit(self, me, fluxer, monkeypatch):
        from scratch_fluxer import config

        monkeypatch.setenv("MAX_BOTS_PER_USER", "1")
        config.reset_settings_cache()
        assert add(me).status_code == 201
        r = add(me, token=TOKEN_2)
        assert r.status_code == 409 and "limit of 1 bots" in r.json()["detail"]

    def test_the_same_bot_can_belong_to_two_people_only_if_both_own_it(self, client, make_user, fluxer):
        # Ownership is checked against Fluxer, so a second account can't claim the first person's bot.
        _, csrf, cookies = make_user(fluxer_id="777", username="other")
        client.cookies.update(cookies)
        r = client.post("/api/bots", json={"token": TOKEN}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 403

    @pytest.mark.parametrize("name", ["x" * 81, "", "   ", "​​", "\x00\x01", None])
    def test_bad_names_use_the_default_or_are_refused(self, me, fluxer, name):
        r = add(me, name=name)
        if name is None:
            assert r.status_code == 201 and r.json()["bot"]["name"] == "Ping Bot"
        else:
            assert r.status_code == 422, name

    def test_names_cannot_hide_or_flip_text(self, me, fluxer):
        name = add(me, name="evil‮gnp.exe​ bot").json()["bot"]["name"]
        assert "‮" not in name and "​" not in name

    def test_arabic_names_are_kept_intact(self, me, fluxer):
        assert add(me, name="بوت التسلية ‏").json()["bot"]["name"] == "بوت التسلية ‏".strip()

    @staticmethod
    def with_raw_name(me, method, path, name_json: str, **fields):
        """Send a JSON body whose name is written exactly as given (httpx can't be made to send a lone surrogate)."""
        extra = "".join(f', "{key}": "{value}"' for key, value in fields.items())
        body = f'{{"name": "{name_json}"{extra}}}'.encode("ascii")
        return me.request(method, path, content=body, headers={**me.h, "Content-Type": "application/json"})

    def test_a_lone_surrogate_in_a_name_is_dropped_instead_of_crashing(self, me, fluxer):
        """JSON can spell a character that no file or database can hold ("\\ud800"). That used to be a server error."""
        r = self.with_raw_name(me, "POST", "/api/bots", "Ping\\ud800 Bot\\udfff", token=TOKEN)
        assert r.status_code == 201 and r.json()["bot"]["name"] == "Ping Bot"
        assert all_bots()[0].name == "Ping Bot"
        bot_id = r.json()["bot"]["id"]
        r = self.with_raw_name(me, "PATCH", f"/api/bots/{bot_id}", "\\udc00New\\ud800")
        assert r.status_code == 200 and r.json()["bot"]["name"] == "New"

    @pytest.mark.parametrize("name", ["\\ud800", "\\udfff\\ud800", " \\ud800 "])
    def test_a_name_that_is_nothing_but_lone_surrogates_is_refused(self, me, fluxer, name):
        assert self.with_raw_name(me, "POST", "/api/bots", name, token=TOKEN).status_code == 422
        assert all_bots() == []

    def test_a_real_surrogate_pair_is_just_an_emoji_and_stays(self, me, fluxer):
        r = self.with_raw_name(me, "POST", "/api/bots", "Party \\ud83c\\udf89 bot", token=TOKEN)
        assert r.status_code == 201 and r.json()["bot"]["name"] == "Party \U0001f389 bot"

    def test_an_application_without_an_owner_is_accepted_and_the_skipped_check_is_logged(self, me, fluxer, caplog):
        """Fluxer is supposed to say who owns an application. If it doesn't, the owner check can't be made."""
        fluxer.apps[TOKEN] = {**application(), "owner": None}
        with caplog.at_level(logging.WARNING, logger="scratch_fluxer.bots"):
            assert add(me).status_code == 201
        assert "did not say who owns" in caplog.text
        assert TOKEN not in caplog.text

    def test_malformed_requests(self, me, fluxer):
        assert (
            me.post("/api/bots", content=b"not json", headers={**me.h, "Content-Type": "application/json"}).status_code
            == 400
        )
        assert (
            me.post("/api/bots", content=b"[1,2]", headers={**me.h, "Content-Type": "application/json"}).status_code
            == 400
        )
        assert (
            me.post(
                "/api/bots", content=b"token=abc", headers={**me.h, "Content-Type": "application/x-www-form-urlencoded"}
            ).status_code
            == 415
        )
        big = b'{"token": "' + b"1" * 40_000 + b'"}'
        assert (
            me.post("/api/bots", content=big, headers={**me.h, "Content-Type": "application/json"}).status_code == 413
        )


# --------------------------------------------------------------------------- who may do what
WRITE_ROUTES = [
    ("POST", "/api/bots/verify", {"token": TOKEN}),
    ("POST", "/api/bots", {"token": TOKEN}),
    ("PATCH", "/api/bots/{id}", {"name": "x"}),
    ("PUT", "/api/bots/{id}/token", {"token": TOKEN}),
    ("DELETE", "/api/bots/{id}", None),
    ("DELETE", "/api/me", {"confirm": "agamy"}),
]


class TestAccessControl:
    @pytest.mark.parametrize("method,path,body", WRITE_ROUTES)
    def test_anonymous_requests_get_a_401(self, client, fluxer, method, path, body):
        r = client.request(method, path.format(id="0" * 32), json=body)
        assert r.status_code == 401 and r.json() == {"detail": "Please log in again."}

    @pytest.mark.parametrize("method,path,body", WRITE_ROUTES)
    def test_state_changing_calls_need_the_csrf_header(self, me, fluxer, method, path, body):
        bot_id = add(me).json()["bot"]["id"]
        for headers in ({}, {"X-CSRF-Token": "wrong"}):
            r = me.request(method, path.format(id=bot_id), json=body, headers=headers)
            assert r.status_code == 403, (method, path, headers)
        assert len(all_bots()) == 1 and me.get("/bots").status_code == 200  # nothing changed, still logged in

    @pytest.mark.parametrize("method,path,body", WRITE_ROUTES)
    def test_cross_site_requests_are_refused(self, me, fluxer, method, path, body):
        bot_id = add(me).json()["bot"]["id"]
        for extra in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            r = me.request(method, path.format(id=bot_id), json=body, headers={**me.h, **extra})
            assert r.status_code == 403, (method, path, extra)
        assert len(all_bots()) == 1

    def test_pages_send_anonymous_visitors_to_the_login_page(self, client):
        for path in ("/bots", "/bots/new"):
            r = client.get(path, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == f"/login?next={path}"

    def test_nobody_can_touch_someone_elses_bot(self, client, make_user, fluxer):
        _, csrf_a, cookies_a = make_user(fluxer_id=OWNER_FLUXER_ID, username="alice")
        client.cookies.update(cookies_a)
        bot_id = client.post("/api/bots", json={"token": TOKEN}, headers={"X-CSRF-Token": csrf_a}).json()["bot"]["id"]
        client.cookies.clear()

        _, csrf_b, cookies_b = make_user(fluxer_id="888", username="mallory")
        client.cookies.update(cookies_b)
        headers = {"X-CSRF-Token": csrf_b}
        fluxer.apps[TOKEN_1_ROTATED] = application(owner_id="888")
        assert client.patch(f"/api/bots/{bot_id}", json={"name": "pwned"}, headers=headers).status_code == 404
        assert (
            client.put(f"/api/bots/{bot_id}/token", json={"token": TOKEN_1_ROTATED}, headers=headers).status_code == 404
        )
        assert client.delete(f"/api/bots/{bot_id}", headers=headers).status_code == 404
        listing = client.get("/bots")
        assert "Ping Bot" not in listing.text

        row = all_bots()[0]
        assert row.name == "Ping Bot" and deps.get_vault().decrypt(row.token_ciphertext) == TOKEN

    @pytest.mark.parametrize("bad_id", ["nope", "0" * 32, "../../etc/passwd", "A" * 32, "x" * 500])
    def test_malformed_or_unknown_ids_are_404(self, me, fluxer, bad_id):
        assert me.patch(f"/api/bots/{bad_id}", json={"name": "x"}, headers=me.h).status_code == 404
        assert me.delete(f"/api/bots/{bad_id}", headers=me.h).status_code == 404


# --------------------------------------------------------------------------- rename / token / remove
class TestManage:
    def test_rename(self, me, fluxer):
        bot_id = add(me).json()["bot"]["id"]
        r = me.patch(f"/api/bots/{bot_id}", json={"name": "  My   ping bot "}, headers=me.h)
        assert r.status_code == 200 and r.json()["bot"]["name"] == "My ping bot"
        assert all_bots()[0].name == "My ping bot"
        assert me.patch(f"/api/bots/{bot_id}", json={"name": ""}, headers=me.h).status_code == 422
        assert me.patch(f"/api/bots/{bot_id}", json={"name": "x" * 81}, headers=me.h).status_code == 422

    def test_replace_token_for_the_same_bot(self, me, fluxer):
        bot_id = add(me).json()["bot"]["id"]
        r = me.put(f"/api/bots/{bot_id}/token", json={"token": TOKEN_1_ROTATED}, headers=me.h)
        assert r.status_code == 200 and r.json()["bot"]["token_last4"] == "9999"
        assert TOKEN_1_ROTATED not in r.text
        assert deps.get_vault().decrypt(all_bots()[0].token_ciphertext) == TOKEN_1_ROTATED

    def test_a_token_for_a_different_bot_is_refused(self, me, fluxer):
        bot_id = add(me).json()["bot"]["id"]
        r = me.put(f"/api/bots/{bot_id}/token", json={"token": TOKEN_2}, headers=me.h)
        assert r.status_code == 409 and "different bot" in r.json()["detail"]
        assert deps.get_vault().decrypt(all_bots()[0].token_ciphertext) == TOKEN

    def test_an_invalid_new_token_keeps_the_old_one(self, me, fluxer):
        bot_id = add(me).json()["bot"]["id"]
        fluxer.apps.pop(TOKEN_1_ROTATED)
        assert me.put(f"/api/bots/{bot_id}/token", json={"token": TOKEN_1_ROTATED}, headers=me.h).status_code == 400
        assert deps.get_vault().decrypt(all_bots()[0].token_ciphertext) == TOKEN

    def test_remove_deletes_the_bot_and_its_files(self, me, fluxer):
        bot_id = add(me).json()["bot"]["id"]

        def attach(db):
            db.add(
                Asset(
                    bot_id=bot_id,
                    name="a.json",
                    mime="application/json",
                    kind="json",
                    size=2,
                    sha256="0" * 64,
                    data=b"{}",
                )
            )

        _call_with_session(attach, (), {})
        assert me.delete(f"/api/bots/{bot_id}", headers=me.h).json() == {"ok": True}
        assert all_bots() == []
        assert _call_with_session(lambda db: db.scalar(select(func.count()).select_from(Asset)), (), {}) == 0
        assert me.delete(f"/api/bots/{bot_id}", headers=me.h).status_code == 404  # already gone


# --------------------------------------------------------------------------- limits and overlapping requests
class TestLimitsHoldWhenRequestsOverlap:
    """Postgres locks the rows a limit is counted from. SQLite (only used on a developer's computer) has no row locks,
    so the database module does one piece of work at a time there. Without that, requests that arrive together could
    all count "9 bots" and all add a 10th."""

    def test_requests_that_arrive_together_cannot_get_past_the_bot_limit(self, me):
        from scratch_fluxer.routes import bots as bot_routes

        limit, attempts = 3, 12
        identities = [
            fx.BotIdentity(
                application_id=f"{i:018d}",
                application_name=f"Bot {i}",
                bot_user_id=f"{i:018d}",
                bot_username=f"bot{i}",
                bot_avatar_hash=None,
                owner_id=None,
                owner_name=None,
            )
            for i in range(1, attempts + 1)
        ]
        start_together = threading.Barrier(attempts)
        vault = deps.get_vault()

        def add_one(identity):
            start_together.wait(timeout=10)
            args = (me.user_id, identity, TOKEN, identity.application_name, limit, vault)
            try:
                _call_with_session(bot_routes._create_bot, args, {})
            except HTTPException as refused:
                return refused.status_code
            return "added"

        with ThreadPoolExecutor(attempts) as pool:
            results = list(pool.map(add_one, identities))
        assert results.count("added") == limit and results.count(409) == attempts - limit
        assert len(all_bots()) == limit

    def test_sqlite_work_never_overlaps(self):
        inside = worst = 0
        guard = threading.Lock()

        def work(_db):
            nonlocal inside, worst
            with guard:
                inside += 1
                worst = max(worst, inside)
            time.sleep(0.02)
            with guard:
                inside -= 1

        threads = [threading.Thread(target=_call_with_session, args=(work, (), {})) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert worst == 1

    def test_work_that_asks_for_more_work_does_not_lock_itself_up(self):
        """Inside a piece of database work, more work from the same thread must not wait for itself."""
        done = []
        _call_with_session(lambda _db: _call_with_session(lambda _db2: done.append(True), (), {}), (), {})
        assert done == [True]


# --------------------------------------------------------------------------- pages
class TestPages:
    def test_empty_state(self, me):
        r = me.get("/bots")
        assert r.status_code == 200
        assert "Add your first bot" in r.text and 'href="/bots/new"' in r.text

    def test_lists_the_persons_bots(self, me, fluxer):
        add(me)
        add(me, token=TOKEN_2, name="Quiz <b>time</b>")
        page = me.get("/bots").text
        assert "Ping Bot" in page and "@pingbot" in page
        assert "Token ends in" in page and 'data-role="last4">1234<' in page
        assert "Quiz &lt;b&gt;time&lt;/b&gt;" in page and "<b>time</b>" not in page
        assert page.count('class="bot-row"') == 2
        assert "/bots/" in page and "/edit" in page

    def test_the_add_page(self, me):
        r = me.get("/bots/new")
        assert r.status_code == 200
        assert 'id="token-input"' in r.text and "Check token" in r.text

    def test_the_add_page_when_the_limit_is_reached(self, me, fluxer, monkeypatch):
        from scratch_fluxer import config

        monkeypatch.setenv("MAX_BOTS_PER_USER", "1")
        config.reset_settings_cache()
        add(me)
        page = me.get("/bots/new").text
        assert "limit of 1 bots" in page and 'id="token-input"' not in page


# --------------------------------------------------------------------------- delete account
class TestDeleteAccount:
    def test_deletes_everything_and_logs_out(self, me, fluxer):
        add(me)
        r = me.request("DELETE", "/api/me", json={"confirm": "@AGAMY"}, headers=me.h)
        assert r.status_code == 200 and r.json() == {"ok": True, "redirect": "/"}
        assert all_bots() == []
        assert _call_with_session(lambda db: db.scalar(select(func.count()).select_from(User)), (), {}) == 0
        assert any(
            c.startswith(deps.SESSION_COOKIE + "=") and ("max-age=0" in c.lower() or "1970" in c)
            for c in r.headers.get_list("set-cookie")
        )
        me.cookies.clear()
        assert me.get("/bots", follow_redirects=False).status_code == 303

    @pytest.mark.parametrize("confirm", [None, "", "delete", "agamy2", 5, ["agamy"]])
    def test_needs_the_exact_username(self, me, fluxer, confirm):
        add(me)
        assert me.request("DELETE", "/api/me", json={"confirm": confirm}, headers=me.h).status_code == 422
        assert len(all_bots()) == 1

    def test_only_deletes_the_callers_data(self, me, make_user, fluxer):
        add(me)
        make_user(fluxer_id="31337", username="bystander")
        me.request("DELETE", "/api/me", json={"confirm": "agamy"}, headers=me.h)
        remaining = _call_with_session(lambda db: [u.username for u in db.execute(select(User)).scalars()], (), {})
        assert remaining == ["bystander"]
