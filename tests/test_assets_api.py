"""A bot's files: upload, list, download and delete (the pictures, JSON and text its blocks can use)."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from blocks import block, command, reply, workspace
from sqlalchemy import select

from scratch_fluxer import config
from scratch_fluxer.db import _call_with_session
from scratch_fluxer.models import Asset

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
GIF = b"GIF89a" + b"\x00" * 20
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8
QUOTES = b'["Be kind.", "Keep going."]'


# --------------------------------------------------------------------------- helpers
def url(bot_id: str, name: str = "") -> str:
    return f"/api/bots/{bot_id}/assets" + (f"/{quote(name, safe='')}" if name else "")


def put(client, bot_id, name, data=PNG, **kw):
    return client.put(url(bot_id, name), content=data, headers=client.h, **kw)


def rows(bot_id: str) -> list[SimpleNamespace]:
    def _read(db):
        found = db.execute(select(Asset).where(Asset.bot_id == bot_id).order_by(Asset.name)).scalars().all()
        return [SimpleNamespace(**{c.name: getattr(a, c.name) for c in Asset.__table__.columns}) for a in found]

    return _call_with_session(_read, (), {})


def limits(monkeypatch, **values):
    """Change the limits for the rest of the test (``max_asset_bytes=1000``)."""
    for key, value in values.items():
        monkeypatch.setenv(key.upper(), str(value))
    config.reset_settings_cache()


def uses(*files):
    """Blocks that each name one of ``files`` (so the file counts as used)."""
    return workspace(*[command(f"c{n}", block("sff_reply_file", fields={"FILE": f})) for n, f in enumerate(files)])


# --------------------------------------------------------------------------- upload
class TestUpload:
    def test_a_picture(self, me, new_bot):
        bot_id = new_bot()
        r = put(me, bot_id, "cat.png")
        assert r.status_code == 201
        body = r.json()
        assert body["replaced"] is False
        file = body["file"]
        assert (file["name"], file["kind"], file["mime"], file["size"], file["used_by"]) == (
            "cat.png",
            "image",
            "image/png",
            len(PNG),
            0,
        )
        assert file["created_at"].endswith("+00:00")  # a time with its zone, so every browser reads it the same way
        assert body["files"] == [file] and body["compile"]["ok"] is True
        (row,) = rows(bot_id)
        assert row.data == PNG and row.sha256 == hashlib.sha256(PNG).hexdigest() and row.size == len(PNG)

    @pytest.mark.parametrize(
        ("name", "data", "kind", "mime"),
        [
            ("a.png", PNG, "image", "image/png"),
            ("a.jpg", JPEG, "image", "image/jpeg"),
            ("a.JPEG", JPEG, "image", "image/jpeg"),
            ("a.gif", GIF, "image", "image/gif"),
            ("a.webp", WEBP, "image", "image/webp"),
            ("a.json", QUOTES, "json", "application/json"),
            ("a.txt", b"hello", "text", "text/plain; charset=utf-8"),
            ("a.md", b"# hi", "text", "text/plain; charset=utf-8"),
            ("a.csv", b"a,b\n1,2\n", "text", "text/plain; charset=utf-8"),
            ("réponse ünïcode.txt", "ça va".encode(), "text", "text/plain; charset=utf-8"),
            ("my cat (2).png", PNG, "image", "image/png"),
            ("名前.json", b"{}", "json", "application/json"),
        ],
    )
    def test_each_kind_of_file(self, me, new_bot, name, data, kind, mime):
        bot_id = new_bot()
        r = put(me, bot_id, name, data)
        assert r.status_code == 201, r.text
        assert (r.json()["file"]["kind"], r.json()["file"]["mime"]) == (kind, mime)
        assert rows(bot_id)[0].name == name

    def test_a_byte_order_mark_is_dropped_from_text(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "notes.txt", b"\xef\xbb\xbfhello")
        put(me, bot_id, "data.json", b"\xef\xbb\xbf" + QUOTES)
        by_name = {row.name: row for row in rows(bot_id)}
        assert by_name["notes.txt"].data == b"hello" and by_name["data.json"].data == QUOTES
        assert by_name["notes.txt"].size == 5

    def test_the_same_name_replaces_the_file(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "notes.txt", b"first")
        r = put(me, bot_id, "notes.txt", b"second one")
        assert r.status_code == 200 and r.json()["replaced"] is True
        assert r.json()["file"]["size"] == len(b"second one") and len(r.json()["files"]) == 1
        (row,) = rows(bot_id)
        assert row.data == b"second one" and row.sha256 == hashlib.sha256(b"second one").hexdigest()

    def test_a_replacement_can_change_the_kind_of_content_within_the_type(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "a.json", b"[1]")
        assert put(me, bot_id, "a.json", b'{"x": 2}').status_code == 200
        assert put(me, bot_id, "a.json", b"not json").status_code == 422
        assert rows(bot_id)[0].data == b'{"x": 2}'  # the refused upload left the old file alone

    def test_two_bots_can_each_have_a_file_of_the_same_name(self, me, new_bot):
        from fluxer_mock import TOKEN_2

        first, second = new_bot(), new_bot(token=TOKEN_2)
        put(me, first, "cat.png", PNG)
        assert put(me, second, "cat.png", PNG + b"x").status_code == 201
        assert len(rows(first)) == len(rows(second)) == 1

    def test_the_list_is_in_order_and_says_which_files_are_used(self, me, new_bot):
        bot_id = new_bot()
        for name, data in (("zebra.png", PNG), ("Apple.txt", b"x"), ("mango.json", b"{}"), ("banana.md", b"y")):
            put(me, bot_id, name, data)
        me.put(
            f"/api/bots/{bot_id}/workspace",
            json={"version": 0, "workspace": uses("banana.md", "banana.md", "zebra.png")},
            headers=me.h,
        )
        body = me.get(url(bot_id)).json()
        assert [(f["name"], f["used_by"]) for f in body["files"]] == [
            ("Apple.txt", 0),
            ("banana.md", 2),
            ("mango.json", 0),
            ("zebra.png", 1),
        ]
        assert body["limits"]["max_files"] == 40 and body["limits"]["max_file_bytes"] == 2 * 1024 * 1024
        assert ".png" in body["limits"]["extensions"] and "PNG" in body["limits"]["accepted"]

    def test_the_response_never_holds_the_file(self, me, new_bot):
        bot_id = new_bot()
        text = put(me, bot_id, "notes.txt", b"my private notes").text
        assert "my private notes" not in text and "my private notes" not in me.get(url(bot_id)).text

    # ---- names
    @pytest.mark.parametrize(
        "name",
        [
            "noextension",
            ".png",
            "cat.exe",
            "cat.svg",
            "page.html",
            "run.py",
            "cat.png.exe",
            "-leading.png",
            "_leading.png",
            " .png",
            "two..dots.png",
            "con.txt",
            "NUL.json",
            "com1.png",
            "semi;colon.png",
            'quote".png',
            "star*.png",
            "back\\slash.png",
            "emoji😀.png",
            "it's.png",
            "rtl‮txt.png",
            "tab\t.png",
            "a" * 77 + ".png",
        ],
    )
    def test_names_that_cant_be_used(self, me, new_bot, name):
        bot_id = new_bot()
        r = put(me, bot_id, name)
        assert r.status_code == 422 and "file name" in r.json()["detail"]
        assert rows(bot_id) == []

    @pytest.mark.parametrize("name", ["..%2F..%2Fetc%2Fpasswd.txt", "a%2Fb.png", "%2e%2e%2fx.png", "a/b.png"])
    def test_a_name_with_a_slash_never_reaches_the_store(self, me, new_bot, name):
        bot_id = new_bot()
        r = me.put(f"/api/bots/{bot_id}/assets/{name}", content=PNG, headers=me.h)
        assert r.status_code in (404, 405, 422)
        assert rows(bot_id) == []

    def test_the_longest_name_is_80_characters(self, me, new_bot):
        bot_id = new_bot()
        assert put(me, bot_id, "a" * 76 + ".png").status_code == 201
        assert put(me, bot_id, "b" * 77 + ".png").status_code == 422

    def test_a_name_is_stored_in_its_plain_form(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "café.txt", b"x")  # "e" and a combining accent
        assert rows(bot_id)[0].name == "café.txt"

    def test_names_are_not_case_sensitive_when_it_comes_to_clashes(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "Cat.png")
        r = put(me, bot_id, "cat.png")
        assert r.status_code == 409 and "Cat.png" in r.json()["detail"] and "not case-sensitive" in r.json()["detail"]
        assert [row.name for row in rows(bot_id)] == ["Cat.png"]
        assert put(me, bot_id, "Cat.png", PNG + b"1").status_code == 200  # the exact name still replaces

    # ---- contents
    @pytest.mark.parametrize(
        ("name", "data", "message"),
        [
            ("a.png", b"not a png", "real PNG"),
            ("a.png", JPEG, "real PNG"),
            ("a.jpg", PNG, "real JPG"),
            ("a.gif", PNG, "real GIF"),
            ("a.webp", b"RIFF\x00\x00\x00\x00WAVEfmt ", "real WEBP"),
            ("a.png", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "real PNG"),
            ("a.json", b"{oops", "mistake in it"),
            ("a.json", b"", "empty"),
            ("a.txt", b"", "empty"),
            ("a.txt", b"\xff\xfe\x00bad", "UTF-8"),
            ("a.txt", b"nul\x00inside", "binary"),
            ("a.csv", b"\x89PNG\r\n\x1a\n\x00", "UTF-8"),
            ("a.json", b"[" * 100_000, "mistake in it"),
        ],
    )
    def test_a_file_that_isnt_what_it_says_is_refused(self, me, new_bot, name, data, message):
        bot_id = new_bot()
        r = put(me, bot_id, name, data)
        assert r.status_code == 422 and message in r.json()["detail"]
        assert rows(bot_id) == []

    def test_json_with_odd_but_legal_values_is_fine(self, me, new_bot):
        bot_id = new_bot()
        for n, data in enumerate(
            (b"0", b"null", b'"text"', b"[]", b'{"a": {"b": [1, 2.5, true, null]}}', b"1e400", b"NaN")
        ):
            assert put(me, bot_id, f"v{n}.json", data).status_code == 201, data

    # ---- limits
    def test_too_big_a_file(self, me, new_bot, monkeypatch):
        limits(monkeypatch, max_asset_bytes=1000)
        bot_id = new_bot()
        assert put(me, bot_id, "ok.txt", b"x" * 1000).status_code == 201
        r = put(me, bot_id, "big.txt", b"x" * 1001)
        assert r.status_code == 413 and "bigger than" in r.json()["detail"]
        assert [row.name for row in rows(bot_id)] == ["ok.txt"]

    def test_the_real_limit_is_two_megabytes(self, me, new_bot):
        bot_id = new_bot()
        assert put(me, bot_id, "limit.txt", b"x" * (2 * 1024 * 1024)).status_code == 201
        r = put(me, bot_id, "over.txt", b"x" * (2 * 1024 * 1024 + 1))
        assert r.status_code == 413 and "2 MB" in r.json()["detail"]

    def test_a_declared_length_that_is_too_big_is_refused_without_reading(self, me, new_bot, monkeypatch):
        limits(monkeypatch, max_asset_bytes=1000)
        bot_id = new_bot()
        r = me.put(url(bot_id, "x.txt"), content=b"x" * 5000, headers={**me.h, "Content-Length": "5000"})
        assert r.status_code == 413

    def test_only_so_many_files(self, me, new_bot, monkeypatch):
        limits(monkeypatch, max_assets_per_bot=3)
        bot_id = new_bot()
        for n in range(3):
            assert put(me, bot_id, f"f{n}.txt", b"x").status_code == 201
        r = put(me, bot_id, "f3.txt", b"x")
        assert r.status_code == 409 and "already has 3 files" in r.json()["detail"]
        assert put(me, bot_id, "f1.txt", b"replaced").status_code == 200  # a replacement isn't one more file
        assert len(rows(bot_id)) == 3

    def test_only_so_many_bytes_in_all(self, me, new_bot, monkeypatch):
        limits(monkeypatch, max_assets_total_bytes=2500)
        bot_id = new_bot()
        assert put(me, bot_id, "a.txt", b"x" * 1000).status_code == 201
        assert put(me, bot_id, "b.txt", b"x" * 1000).status_code == 201
        r = put(me, bot_id, "c.txt", b"x" * 501)
        assert r.status_code == 409 and "Delete a file" in r.json()["detail"]
        assert put(me, bot_id, "c.txt", b"x" * 500).status_code == 201
        # replacing a file counts only the difference
        assert put(me, bot_id, "a.txt", b"y" * 1000).status_code == 200
        assert put(me, bot_id, "a.txt", b"y" * 1001).status_code == 409
        assert put(me, bot_id, "c.txt", b"y" * 499).status_code == 200
        assert put(me, bot_id, "a.txt", b"y" * 1001).status_code == 200

    def test_deleting_makes_room(self, me, new_bot, monkeypatch):
        limits(monkeypatch, max_assets_per_bot=1)
        bot_id = new_bot()
        put(me, bot_id, "a.txt", b"x")
        assert put(me, bot_id, "b.txt", b"x").status_code == 409
        assert me.delete(url(bot_id, "a.txt"), headers=me.h).status_code == 200
        assert put(me, bot_id, "b.txt", b"x").status_code == 201

    def test_limits_are_per_bot(self, me, new_bot, monkeypatch):
        from fluxer_mock import TOKEN_2

        limits(monkeypatch, max_assets_per_bot=1)
        first, second = new_bot(), new_bot(token=TOKEN_2)
        assert put(me, first, "a.txt", b"x").status_code == 201
        assert put(me, second, "a.txt", b"x").status_code == 201

    def test_is_rate_limited_per_person(self, me, new_bot):
        bot_id = new_bot()
        codes = [put(me, bot_id, "a.txt", b"x").status_code for _ in range(42)]
        assert codes[0] == 201 and codes[1:40] == [200] * 39 and codes[40:] == [429, 429]

    # ---- who may upload
    def test_needs_a_login(self, client, me, new_bot):
        bot_id = new_bot()
        client.cookies.clear()
        assert client.put(url(bot_id, "a.txt"), content=b"x").status_code == 401
        assert client.get(url(bot_id)).status_code == 401

    def test_needs_the_csrf_header(self, me, new_bot):
        bot_id = new_bot()
        assert me.put(url(bot_id, "a.txt"), content=b"x").status_code == 403
        assert me.put(url(bot_id, "a.txt"), content=b"x", headers={"X-CSRF-Token": "wrong"}).status_code == 403
        assert (
            me.put(url(bot_id, "a.txt"), content=b"x", headers={**me.h, "Origin": "https://evil.example"}).status_code
            == 403
        )
        assert rows(bot_id) == []

    def test_somebody_elses_bot_cannot_be_written_to(self, me, stranger, new_bot):
        bot_id = new_bot()
        r = put(stranger, bot_id, "a.txt", b"x")
        assert r.status_code == 404 and rows(bot_id) == []
        assert stranger.get(url(bot_id)).status_code == 404

    @pytest.mark.parametrize("bad_id", ["abc", "../x", "0" * 32])
    def test_a_made_up_bot_is_a_404(self, me, bad_id):
        assert put(me, bad_id, "a.txt", b"x").status_code == 404
        assert me.get(url(bad_id)).status_code == 404


# --------------------------------------------------------------------------- download
class TestDownload:
    def test_a_picture_is_shown_in_the_page(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "cat.png")
        r = me.get(url(bot_id, "cat.png"))
        assert r.status_code == 200 and r.content == PNG
        assert r.headers["content-type"] == "image/png"
        assert r.headers["content-disposition"].startswith("inline;")

    @pytest.mark.parametrize(("name", "data"), [("notes.txt", b"hello"), ("quotes.json", QUOTES), ("t.csv", b"a,b")])
    def test_other_files_are_downloaded_not_shown(self, me, new_bot, name, data):
        bot_id = new_bot()
        put(me, bot_id, name, data)
        r = me.get(url(bot_id, name))
        assert r.content == data and r.headers["content-disposition"].startswith("attachment;")

    def test_a_file_cannot_do_anything_when_opened(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "cat.png")
        put(me, bot_id, "page.txt", b"<script>alert(1)</script>")
        for name in ("cat.png", "page.txt"):
            headers = me.get(url(bot_id, name)).headers
            assert headers["x-content-type-options"] == "nosniff"
            assert headers["content-security-policy"] == "default-src 'none'; sandbox"
            assert headers["x-frame-options"] == "DENY"
        assert me.get(url(bot_id, "page.txt")).headers["content-type"] == "text/plain; charset=utf-8"

    def test_is_private_and_revalidated(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "cat.png")
        first = me.get(url(bot_id, "cat.png"))
        assert first.headers["cache-control"] == "private, no-cache"
        assert first.headers["etag"] == f'"{hashlib.sha256(PNG).hexdigest()}"'
        again = me.get(url(bot_id, "cat.png"), headers={"If-None-Match": first.headers["etag"]})
        assert again.status_code == 304 and again.content == b""
        put(me, bot_id, "cat.png", PNG + b"more")
        changed = me.get(url(bot_id, "cat.png"), headers={"If-None-Match": first.headers["etag"]})
        assert changed.status_code == 200 and changed.content == PNG + b"more"

    @pytest.mark.parametrize("name", ["my cat (1).png", "réponse.txt", "名前.json", "a (b)_c-d.txt"])
    def test_awkward_names_make_valid_headers(self, me, new_bot, name):
        bot_id = new_bot()
        data = {"png": PNG, "txt": b"x", "json": b"{}"}[name.rsplit(".", 1)[1]]
        put(me, bot_id, name, data)
        disposition = me.get(url(bot_id, name)).headers["content-disposition"]
        assert disposition.isascii() and "\n" not in disposition and "\r" not in disposition
        assert f"filename*=UTF-8''{quote(name)}" in disposition

    def test_the_name_has_to_be_exact(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "Cat.png")
        assert me.get(url(bot_id, "cat.png")).status_code == 404
        assert me.get(url(bot_id, "Cat.png")).status_code == 200

    def test_a_missing_file(self, me, new_bot):
        bot_id = new_bot()
        r = me.get(url(bot_id, "nothing.png"))
        assert r.status_code == 404 and r.json() == {"detail": "That file doesn't exist."}

    def test_needs_a_login(self, client, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "cat.png")
        client.cookies.clear()
        assert client.get(url(bot_id, "cat.png")).status_code == 401

    def test_somebody_elses_file_is_a_404(self, me, stranger, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "cat.png")
        r = stranger.get(url(bot_id, "cat.png"))
        assert r.status_code == 404 and PNG not in r.content

    def test_the_same_name_in_another_bot_is_not_reachable_through_this_one(self, me, new_bot):
        from fluxer_mock import TOKEN_2

        first, second = new_bot(), new_bot(token=TOKEN_2)
        put(me, first, "secret.txt", b"first bot only")
        assert me.get(url(second, "secret.txt")).status_code == 404


# --------------------------------------------------------------------------- delete
class TestDelete:
    def test_removes_the_file(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "a.txt", b"x")
        put(me, bot_id, "b.txt", b"y")
        r = me.delete(url(bot_id, "a.txt"), headers=me.h)
        assert r.status_code == 200 and r.json()["ok"] is True
        assert [f["name"] for f in r.json()["files"]] == ["b.txt"]
        assert [row.name for row in rows(bot_id)] == ["b.txt"]
        assert me.get(url(bot_id, "a.txt")).status_code == 404

    def test_a_missing_file(self, me, new_bot):
        bot_id = new_bot()
        r = me.delete(url(bot_id, "nothing.txt"), headers=me.h)
        assert r.status_code == 404 and r.json() == {"detail": "That file doesn't exist."}

    @pytest.mark.parametrize("name", ["a%2Fb.txt", "x", "no-extension", "..%5Cx.txt"])
    def test_names_that_cant_exist_are_a_404(self, me, new_bot, name):
        bot_id = new_bot()
        put(me, bot_id, "keep.txt", b"x")
        r = me.delete(f"/api/bots/{bot_id}/assets/{name}", headers=me.h)
        assert r.status_code in (404, 405)
        assert [row.name for row in rows(bot_id)] == ["keep.txt"]

    def test_the_blocks_that_used_it_now_have_a_problem(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "notes.txt", b"x")
        saved = me.put(
            f"/api/bots/{bot_id}/workspace",
            json={"version": 0, "workspace": uses("notes.txt")},
            headers=me.h,
        ).json()
        assert saved["compile"]["ok"] is True and saved["files"][0]["used_by"] == 1
        after = me.delete(url(bot_id, "notes.txt"), headers=me.h).json()
        assert after["files"] == [] and after["compile"]["ok"] is False
        assert "notes.txt" in after["compile"]["diagnostics"][0]["message"]
        # ...and the blocks themselves still say which file they wanted
        body = me.get(f"/api/bots/{bot_id}/workspace").json()
        assert "notes.txt" in json.dumps(body["workspace"])

    def test_uploading_it_again_fixes_the_problem(self, me, new_bot):
        bot_id = new_bot()
        me.put(f"/api/bots/{bot_id}/workspace", json={"version": 0, "workspace": uses("notes.txt")}, headers=me.h)
        assert me.get(f"/api/bots/{bot_id}/workspace").json()["compile"]["ok"] is False
        fixed = put(me, bot_id, "notes.txt", b"x").json()
        assert fixed["compile"]["ok"] is True and fixed["file"]["used_by"] == 1

    def test_a_file_of_the_wrong_kind_is_a_problem_not_a_crash(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "notes.txt", b"x")
        ws = workspace(command("pic", block("sff_reply_image", fields={"FILE": "notes.txt"})))
        body = me.put(f"/api/bots/{bot_id}/workspace", json={"version": 0, "workspace": ws}, headers=me.h).json()
        assert body["compile"]["ok"] is False and "notes.txt" in body["compile"]["diagnostics"][0]["message"]

    def test_deleting_a_bot_deletes_its_files(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "a.txt", b"x")
        assert me.delete(f"/api/bots/{bot_id}", headers=me.h).status_code == 200
        assert rows(bot_id) == []

    def test_needs_a_login(self, client, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "a.txt", b"x")
        client.cookies.clear()
        assert client.delete(url(bot_id, "a.txt")).status_code == 401
        assert len(rows(bot_id)) == 1

    def test_needs_the_csrf_header(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "a.txt", b"x")
        assert me.delete(url(bot_id, "a.txt")).status_code == 403
        assert me.delete(url(bot_id, "a.txt"), headers={**me.h, "Origin": "https://evil.example"}).status_code == 403
        assert len(rows(bot_id)) == 1

    def test_somebody_elses_file_cannot_be_deleted(self, me, stranger, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "a.txt", b"x")
        assert stranger.delete(url(bot_id, "a.txt"), headers=stranger.h).status_code == 404
        assert len(rows(bot_id)) == 1


# --------------------------------------------------------------------------- a file name in blocks
class TestFilesInBlocks:
    def test_the_python_reads_the_file_from_the_assets_folder(self, me, new_bot):
        bot_id = new_bot()
        put(me, bot_id, "cat.png")
        ws = workspace(command("cat", block("sff_reply_image", fields={"FILE": "cat.png"})))
        code = me.put(f"/api/bots/{bot_id}/workspace", json={"version": 0, "workspace": ws}, headers=me.h).json()[
            "compile"
        ]["code"]
        assert 'ASSETS / "cat.png"' in code
        compile(code, "main.py", "exec")

    def test_a_file_name_cannot_smuggle_code_into_the_bot(self, me, new_bot):
        bot_id = new_bot()
        evil = 'x.txt"); import os; os.system("id'
        ws = workspace(command("go", block("sff_reply_file", fields={"FILE": evil})))
        body = me.put(f"/api/bots/{bot_id}/workspace", json={"version": 0, "workspace": ws}, headers=me.h).json()
        assert body["compile"]["ok"] is False and body["compile"]["diagnostics"]
        assert "os.system" not in body["compile"]["code"]
        assert me.get(f"/bots/{bot_id}/export/main.py").status_code == 409  # code like that can't be taken out either

    def test_text_blocks_are_unaffected(self, me, new_bot):
        bot_id = new_bot()
        body = me.put(
            f"/api/bots/{bot_id}/workspace",
            json={"version": 0, "workspace": workspace(command("hi", reply("Hello")))},
            headers=me.h,
        ).json()
        assert body["compile"]["ok"] is True and body["files"] == []
