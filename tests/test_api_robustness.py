"""Odd input at every door.

Whatever arrives (text no file can hold, numbers that don't fit, the wrong kind of value, damaged JSON, strange
addresses, forged cookies), the site must answer with a refusal or a normal answer, never with a server error. What it
keeps must be clean, and what it shows must be escaped. This is the kind of input a person typing carefully never sends
and a person attacking the site always does.
"""

from __future__ import annotations

import ast
import json
import time
from urllib.parse import quote

import pytest
from fluxer_mock import TOKEN, TOKEN_2
from markupsafe import escape
from sqlalchemy import select
from test_runs_api import FakeRunner

from scratch_fluxer import codecheck, deps
from scratch_fluxer.db import _call_with_session
from scratch_fluxer.models import Asset, Bot
from scratch_fluxer.runner import service
from scratch_fluxer.security import limiter

TEXT = [
    "",
    " ",
    "\x00",
    "a\x00b",
    "\x1b[31mred",
    "\ud800",  # a lone surrogate: JSON can spell one, no file or database can hold one
    "\udfff\ud800",
    "ok\ud800ok",
    "\U0001f600",  # the same two halves in order are an emoji
    "\u202egnp.exe",  # right-to-left override
    "\u2028\u2029\x85",  # line separators
    "\ufeff",
    "\u200b\u200c\u200d",
    "\u0130\u0130\u0130",
    "\ufb03",
    "x" * 5_000,
    "../../etc/passwd",
    "..\\..\\windows",
    "%00",
    "%ZZ",
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "'; DROP TABLE bots;--",
    "{{7*7}} ${7*7} {% raw %}",
    "\r\nX-Injected: 1",
    "\U0001f600" * 60,
    "a" * 80,
    "\u0627" * 90,  # Arabic
]
DEEP = [[]]
for _ in range(200):
    DEEP = [DEEP]
VALUES = [
    *TEXT,
    None,
    True,
    False,
    0,
    -1,
    1,
    2**31,
    2**63,
    10**400,
    -(10**400),
    1.5,
    -0.0,
    1e308,
    [],
    {},
    [None],
    ["a", 1, None],
    {"a": {"b": [1, {"c": None}]}},
    DEEP,
]
RAW_BODIES = [
    b"",
    b"null",
    b"[]",
    b"123",
    b'"text"',
    b"{",
    b'{"a":',
    b"{'single': 'quotes'}",
    b'{"a": NaN}',
    b'{"a": Infinity}',
    b'{"a": -Infinity}',
    b'{"a": 1e999}',
    b'{"a": "\\ud800"}',
    b'{"a": "\\u0000"}',
    b'{"a": 1, "a": 2}',
    b"\xff\xfe\x00{",
    b"\xef\xbb\xbf{}",  # a byte order mark
    b'{"name": "\xff\xfe"}',  # not UTF-8
    b"{" * 5000,
    b"[" * 5000,
    b'{"a":' * 3000 + b"1" + b"}" * 3000,
    b"\x00" * 100,
]
BAD_IDS = [
    "x",
    "0" * 32,
    "f" * 32,
    "F" * 32,
    "g" * 32,
    "a" * 31,
    "a" * 33,
    "a" * 500,
    "../x",
    "..%2f..%2fx",
    "%00",
    "%ZZ",
    "a b",
    "a\nb",
    "\U0001f600",
    "\ud800",
    "<script>",
    "' OR 1=1 --",
]


def body_of(value) -> bytes:
    """JSON as text. ``ensure_ascii`` (the default) spells a lone surrogate as ``\\ud800``, which is what a browser sends."""
    return json.dumps(value).encode("ascii")


def path_piece(text: str) -> str:
    """A piece of a URL path. ``surrogatepass`` turns a lone surrogate into bytes that are not UTF-8, as an attacker would."""
    return quote(text, safe="", errors="surrogatepass")


class Doors:
    """Calls the site as the logged-in person and checks that it never fails."""

    def __init__(self, me) -> None:
        self.me = me
        self.calls = 0

    def call(self, method: str, path: str, *, body: bytes | None = None, headers: dict | None = None, **options):
        limiter.reset()  # the rate limits are not what is being tested: every call must reach its handler
        sent = {**self.me.h, **(headers or {})}
        if body is not None and "Content-Type" not in sent:
            sent["Content-Type"] = "application/json"
        response = self.me.request(method, path, content=body, headers=sent, **options)
        self.calls += 1
        assert response.status_code < 500, (method, path, body[:200] if body else body, response.text[:300])
        assert response.status_code != 0
        return response


@pytest.fixture
def doors(me, fluxer):
    service.set_runner(FakeRunner())
    return Doors(me)


@pytest.fixture
def bot(doors):
    response = doors.call("POST", "/api/bots", body=body_of({"token": TOKEN}))
    assert response.status_code == 201
    return response.json()["bot"]["id"]


def stored_bots():
    return _call_with_session(lambda db: list(db.execute(select(Bot)).scalars()), (), {})


def clean_text(value: str) -> None:
    """Text that was kept: it can be written to any file or database, and has nothing hidden in it."""
    value.encode("utf-8")  # raises on a lone surrogate
    assert "\x00" not in value and not any(ord(ch) < 32 for ch in value)
    assert not any(ch in value for ch in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


# ================================================================================================= names and tokens
class TestBotsAndTokens:
    @pytest.mark.parametrize("value", VALUES)
    def test_names(self, doors, bot, value):
        doors.call("PATCH", f"/api/bots/{bot}", body=body_of({"name": value}))
        r = doors.call("POST", "/api/bots", body=body_of({"token": TOKEN_2, "name": value}))
        if r.status_code == 201:
            doors.call("DELETE", f"/api/bots/{r.json()['bot']['id']}")
        for row in stored_bots():
            clean_text(row.name)
            assert 1 <= len(row.name) <= 80

    @pytest.mark.parametrize("value", VALUES)
    def test_tokens(self, doors, bot, value):
        for method, path in (
            ("POST", "/api/bots/verify"),
            ("POST", "/api/bots"),
            ("PUT", f"/api/bots/{bot}/token"),
        ):
            doors.call(method, path, body=body_of({"token": value}))
        row = stored_bots()[0]
        assert deps.get_vault().decrypt(row.token_ciphertext) == TOKEN  # nothing odd replaced the real token

    @pytest.mark.parametrize("raw", RAW_BODIES)
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/api/bots/verify"),
            ("POST", "/api/bots"),
            ("PATCH", "/api/bots/{bot}"),
            ("PUT", "/api/bots/{bot}/token"),
            ("PUT", "/api/bots/{bot}/workspace"),
            ("DELETE", "/api/me"),
        ],
    )
    def test_damaged_bodies(self, doors, bot, method, path, raw):
        r = doors.call(method, path.format(bot=bot), body=raw)
        assert r.status_code in (400, 401, 413, 415, 422, 404, 409, 200)  # a refusal, or (rarely) a normal answer
        assert len(stored_bots()) == 1  # and the account is still there

    @pytest.mark.parametrize(
        "content_type",
        [
            "",
            "text/plain",
            "application/x-www-form-urlencoded",
            "multipart/form-data",
            "application/json; charset=utf-16",
            "application/json;;",
            "APPLICATION/JSON",
            "application/jsonx",
            "application/json\r\nX-Injected: 1",
        ],
    )
    def test_content_types(self, doors, bot, content_type):
        headers = {"Content-Type": content_type}
        if "\r" in content_type:  # a header can't carry a line break; the client refuses before it is sent
            return
        for method, path in (("POST", "/api/bots/verify"), ("PATCH", f"/api/bots/{bot}"), ("DELETE", "/api/me")):
            doors.call(method, path, body=body_of({"token": TOKEN, "name": "x", "confirm": "nobody"}), headers=headers)
        assert len(stored_bots()) == 1


# ================================================================================================= blocks
def nasty_blocks(value) -> list:
    """Blocks of every kind with ``value`` wherever a value can go."""
    return [
        {
            "type": "sff_event_ready",
            "id": "a",
            "inputs": {
                "DO": {
                    "block": {
                        "type": "sff_console_print",
                        "id": "b",
                        "inputs": {"TEXT": {"block": {"type": "sff_text", "id": "c", "fields": {"TEXT": value}}}},
                    }
                }
            },
        },
        {"type": value, "id": value, "x": value, "y": value, "fields": value, "inputs": value, "next": value},
        {"type": "sff_event_command", "id": "d", "fields": {"NAME": value}},
        {"type": "sff_set_variable", "id": "e", "fields": {"VAR": {"id": value}}, "inputs": {"VALUE": value}},
    ]


class TestWorkspaces:
    @pytest.mark.parametrize("value", VALUES)
    def test_any_value_in_any_place_is_cleaned_not_crashed_on(self, doors, bot, value):
        version = 0
        for state in (
            {
                "blocks": {"languageVersion": 0, "blocks": nasty_blocks(value)},
                "variables": [{"name": value, "id": value}],
            },
            {"blocks": value, "variables": value},
            value,
        ):
            r = doors.call("PUT", f"/api/bots/{bot}/workspace", body=body_of({"version": version, "workspace": state}))
            if r.status_code == 200:
                version = r.json()["version"]
            loaded = doors.call("GET", f"/api/bots/{bot}/workspace")
            assert loaded.status_code == 200
            doors.call("GET", f"/bots/{bot}/edit")
            doors.call("GET", f"/bots/{bot}/export/main.py")
            doors.call("GET", f"/bots/{bot}/export/bot.zip")

    @pytest.mark.parametrize("version", VALUES)
    def test_any_version(self, doors, bot, version):
        r = doors.call("PUT", f"/api/bots/{bot}/workspace", body=body_of({"version": version, "workspace": {}}))
        assert r.status_code in (200, 409, 422)

    def test_a_workspace_that_is_far_too_big_is_refused_quickly(self, doors, bot):
        big = {
            "blocks": {
                "blocks": [{"type": "sff_text", "id": str(n), "fields": {"TEXT": "x" * 100}} for n in range(30_000)]
            }
        }
        started = time.time()
        r = doors.call("PUT", f"/api/bots/{bot}/workspace", body=body_of({"version": 0, "workspace": big}))
        assert r.status_code in (413, 422) and time.time() - started < 5

    @pytest.mark.parametrize(
        "text",
        [
            '"); import os; os.system("id"); ("',
            "\n    os.system('id')\n",
            "\\",
            '"""',
            "{x}",
            "\u2028",
            "\ud800",
        ],
    )
    def test_the_code_that_comes_out_is_still_checked_code(self, doors, bot, text):
        """Whatever text was typed into a block, the exported file is code that passes the safety check: the text stayed
        text and never became code."""
        state = {"blocks": {"blocks": nasty_blocks(text)[:1] + nasty_blocks(text)[2:3]}}
        r = doors.call("PUT", f"/api/bots/{bot}/workspace", body=body_of({"version": 0, "workspace": state}))
        assert r.status_code == 200, r.text
        exported = doors.call("GET", f"/bots/{bot}/export/main.py")
        if exported.status_code == 200:
            codecheck.check(exported.text)  # raises if anything unsafe got in
            calls = [
                node.func.attr
                for node in ast.walk(ast.parse(exported.text))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            ]
            assert "system" not in calls


# ================================================================================================= files
class TestFiles:
    @pytest.mark.parametrize(
        "name", TEXT + ["a.json", "A.JSON", "a.png", "con.txt", "..", ".", ".hidden", "a/b.txt", "a\\b.txt"]
    )
    def test_file_names(self, doors, bot, name):
        for body in (b"{}", b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, b"hello", b""):
            doors.call(
                "PUT",
                f"/api/bots/{bot}/assets/{path_piece(name)}",
                body=body,
                headers={"Content-Type": "application/octet-stream"},
            )
        doors.call("GET", f"/api/bots/{bot}/assets/{path_piece(name)}")
        doors.call("DELETE", f"/api/bots/{bot}/assets/{path_piece(name)}")
        names = _call_with_session(lambda db: list(db.execute(select(Asset.name)).scalars()), (), {})
        for stored in names:
            clean_text(stored)
            assert "/" not in stored and "\\" not in stored and not stored.startswith(".")

    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"{",
            b"[" * 100_000,
            b'{"a":' * 5000 + b"1" + b"}" * 5000,
            b"\xff\xfe\xfd",
            b"\x89PNG\r\n\x1a\n",
            b"\x89PNG\r\n\x1a\n" + b"\xff" * 5000,
            b"GIF89a",
            b"<html><script>alert(1)</script></html>",
            b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>",
            b"\x00" * 50_000,
        ],
    )
    def test_file_contents(self, doors, bot, data):
        for name in ("a.json", "a.png", "a.txt", "a.gif", "a.svg", "a.html"):
            doors.call(
                "PUT", f"/api/bots/{bot}/assets/{name}", body=data, headers={"Content-Type": "application/octet-stream"}
            )
            r = doors.call("GET", f"/api/bots/{bot}/assets/{name}")
            if r.status_code == 200:
                assert r.headers["x-content-type-options"] == "nosniff"
                assert (
                    "sandbox" in r.headers["content-security-policy"]
                    or "default-src 'none'" in r.headers["content-security-policy"]
                )

    def test_a_file_that_is_too_big_is_refused_not_buffered(self, doors, bot):
        r = doors.call(
            "PUT",
            f"/api/bots/{bot}/assets/big.txt",
            body=b"x" * (3 * 1024 * 1024),
            headers={"Content-Type": "text/plain"},
        )
        assert r.status_code == 413


# ================================================================================================= addresses
class TestAddresses:
    @pytest.mark.parametrize("bad", BAD_IDS)
    def test_a_bot_id_that_is_not_one(self, doors, bad):
        piece = path_piece(bad)
        for method, path in (
            ("GET", f"/bots/{piece}/edit"),
            ("GET", f"/bots/{piece}/export/main.py"),
            ("GET", f"/bots/{piece}/export/bot.zip"),
            ("GET", f"/api/bots/{piece}/workspace"),
            ("PUT", f"/api/bots/{piece}/workspace"),
            ("GET", f"/api/bots/{piece}/assets"),
            ("PUT", f"/api/bots/{piece}/assets/a.txt"),
            ("PATCH", f"/api/bots/{piece}"),
            ("PUT", f"/api/bots/{piece}/token"),
            ("DELETE", f"/api/bots/{piece}"),
            ("POST", f"/api/bots/{piece}/run"),
            ("POST", f"/api/bots/{piece}/restart"),
            ("POST", f"/api/bots/{piece}/stop"),
            ("GET", f"/api/bots/{piece}/run"),
        ):
            r = doors.call(method, path, body=b"{}" if method in ("PUT", "PATCH") else None)
            assert r.status_code in (400, 404, 405, 415, 422), (method, path, r.status_code)

    @pytest.mark.parametrize("value", TEXT[:24])
    def test_query_strings(self, doors, bot, value):
        q = path_piece(value)
        doors.call("GET", f"/api/bots/{bot}/run?after={q}&run={q}")
        doors.call("GET", f"/login?next={q}&error={q}")
        doors.call("GET", f"/auth/login?next={q}", follow_redirects=False)
        doors.call("GET", f"/auth/callback?code={q}&state={q}&error={q}", follow_redirects=False)
        doors.call("GET", f"/bots?x={q}")
        doors.call("GET", f"/privacy?x={q}")
        doors.call("GET", f"/static/{q}")
        doors.call("GET", f"/{q}")

    def test_repeated_and_missing_parameters(self, doors, bot):
        doors.call("GET", f"/api/bots/{bot}/run?after=1&after=2&after=3&run=&run=")
        doors.call("GET", f"/api/bots/{bot}/run?after[]=1&after.x=2")
        doors.call("GET", "/login?next=/a&next=/b&next=")
        doors.call("GET", "/auth/callback", follow_redirects=False)

    def test_a_very_long_address(self, doors, bot):
        r = doors.call("GET", "/" + "a" * 20_000)
        assert r.status_code in (404, 414, 400)
        doors.call("GET", f"/api/bots/{bot}/run?after={'9' * 5000}")


# ================================================================================================= cookies and origin
class TestSessionsAndOrigin:
    COOKIES = [
        "",
        "x",
        "a.b.c",
        "." * 50,
        "A" * 3000,
        "A" * 100_000,
        "e30.AAAA.AAAA",
        "eyJ1IjoiYSJ9.AAAAAA.AAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "%00",
        "\\",
        '"quoted"',
        "a;b=c",
        "=",
    ]

    @pytest.mark.parametrize("cookie", COOKIES)
    def test_forged_session_cookies_are_just_no_session(self, client, cookie):
        client.cookies.clear()
        for path in ("/", "/bots", "/api/bots/" + "a" * 32 + "/workspace", "/bots/new", "/privacy"):
            r = client.get(
                path, headers={"Cookie": f"sff_session={cookie}; sff_oauth={cookie}"}, follow_redirects=False
            )
            assert r.status_code < 500, (path, cookie[:40])
            if path.startswith("/api/") or path.startswith("/bots"):
                assert r.status_code in (303, 401, 404)  # never let in

    @pytest.mark.parametrize(
        "origin",
        [
            "null",
            "",
            "http://",
            "http://[::1",
            "https://evil.example",
            "http://testserver.evil.example",
            "ftp://testserver",
            "http://testserver:99999",
            "\u202ehttp://testserver",
            "http://testserver@evil.example",
        ],
    )
    def test_strange_origins_are_refused_not_crashed_on(self, doors, bot, origin):
        for key in ("Origin", "Referer"):
            try:
                r = doors.call("PATCH", f"/api/bots/{bot}", body=body_of({"name": "Renamed"}), headers={key: origin})
            except UnicodeEncodeError:  # the client itself can't send this one
                continue
            assert r.status_code in (200, 403), (key, origin, r.status_code)

    def test_the_csrf_token_in_odd_shapes(self, doors, bot):
        for token in (
            "",
            " ",
            "x" * 5000,
            "csrf-token-for-tests ",
            " csrf-token-for-tests",
            "CSRF-TOKEN-FOR-TESTS",
            "csrf-token-for-tests\x00",
        ):
            try:
                r = doors.call(
                    "PATCH", f"/api/bots/{bot}", body=body_of({"name": "n"}), headers={"X-CSRF-Token": token}
                )
            except (UnicodeEncodeError, ValueError):
                continue
            assert r.status_code in (200, 403)

    def test_logout_with_odd_bodies(self, doors):
        for raw in (
            b"",
            b"csrf_token=",
            b"csrf_token=" + b"x" * 10_000,
            b"\xff\xfe\x00",
            b"csrf_token=%ZZ",
            b"=&=&=",
            b"csrf_token[]=1",
        ):
            r = doors.call(
                "POST",
                "/auth/logout",
                body=raw,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                follow_redirects=False,
            )
            assert r.status_code in (303, 403, 413)


# ================================================================================================= nothing odd is shown unescaped
class TestWhatIsShown:
    @pytest.mark.parametrize(
        ("name", "must_not_appear"),
        [
            ("<script>alert(1)</script>", "<script>alert(1)</script>"),
            ('"><img src=x onerror=alert(1)>', "<img src=x onerror"),
            ("</title></script><script>alert(1)</script>", "<script>alert(1)"),
            ("{{1234*4321}}", "5332114"),
            ("${1234*4321}", "5332114"),
            ("{% for x in range(3) %}Q9Z{% endfor %}", "Q9ZQ9ZQ9Z"),
            ("ok\ud800ok", "\ud800"),
            ("'; DROP TABLE bots;--", "\x00"),
        ],
    )
    def test_bot_names_are_escaped_everywhere(self, doors, bot, name, must_not_appear):
        doors.call("PATCH", f"/api/bots/{bot}", body=body_of({"name": name}))
        row = stored_bots()[0]  # the bot is still there, whatever its name said
        clean_text(row.name)
        shown = str(escape(row.name))
        for path in ("/bots", f"/bots/{bot}/edit"):
            page = doors.call("GET", path).text
            assert must_not_appear not in page, path
            assert shown in page, path  # the name as it was kept, every special character spelled out safely

    def test_every_error_is_json_for_the_api_and_a_page_for_everything_else(self, doors, bot):
        api = doors.call("GET", "/api/nothing-here")
        assert api.headers["content-type"].startswith("application/json")
        page = doors.call("GET", "/nothing-here")
        assert page.headers["content-type"].startswith("text/html") and "<h1" in page.text
