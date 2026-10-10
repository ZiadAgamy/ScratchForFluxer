"""The editor's server side: the block list, loading and saving a bot's blocks, and taking the code out.

Fluxer is mocked (a bot is added with a mock token), so these run the real routes against the real database.
"""

from __future__ import annotations

import copy
import io
import json
import time
import zipfile
from types import SimpleNamespace

import pytest
from blocks import command, on_ready, reply, workspace
from fluxer_mock import TOKEN
from sqlalchemy import select

from scratch_fluxer import catalogue
from scratch_fluxer.compiler import default_workspace
from scratch_fluxer.db import _call_with_session
from scratch_fluxer.models import Bot

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


# --------------------------------------------------------------------------- helpers
def stored(bot_id: str) -> SimpleNamespace:
    """The bot's row as plain values (name, token_ciphertext, token_last4, workspace_json, workspace_version, ...)."""

    def _read(db):
        row = db.execute(select(Bot).where(Bot.id == bot_id)).scalar_one()
        return SimpleNamespace(**{column.name: getattr(row, column.name) for column in Bot.__table__.columns})

    return _call_with_session(_read, (), {})


def store_text(bot_id: str, text: str) -> None:
    """Put exactly ``text`` in the database as the bot's blocks (to see how a bad value is handled)."""

    def _write(db):
        db.execute(select(Bot).where(Bot.id == bot_id)).scalar_one().workspace_json = text

    _call_with_session(_write, (), {})


def load(me, bot_id):
    return me.get(f"/api/bots/{bot_id}/workspace")


def save(me, bot_id, ws, version=0, **kw):
    return me.put(f"/api/bots/{bot_id}/workspace", json={"version": version, "workspace": ws}, headers=me.h, **kw)


HELLO = workspace(command("hello", reply("Hello!")))


def hello():
    """The same blocks every time (a fresh copy, so a test can change it)."""
    return copy.deepcopy(HELLO)


def upload(me, bot_id, name, data=PNG):
    r = me.put(f"/api/bots/{bot_id}/assets/{name}", content=data, headers=me.h)
    assert r.status_code in (200, 201), r.text
    return r


def zip_of(response) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(response.content))


# --------------------------------------------------------------------------- the block list
class TestBlockList:
    def test_lists_every_block(self, client):
        r = client.get("/api/blocks")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
        body = r.json()
        assert body == json.loads(json.dumps(catalogue.browser_payload()))
        assert len(body["blocks"]) == len(catalogue.BLOCKS)

    def test_is_the_same_for_everyone_and_needs_no_login(self, client, me):
        logged_in = me.get("/api/blocks")
        client.cookies.clear()
        assert client.get("/api/blocks").content == logged_in.content

    def test_can_be_cached_and_revalidated(self, client):
        first = client.get("/api/blocks")
        assert "max-age" in first.headers["cache-control"] and "private" not in first.headers["cache-control"]
        again = client.get("/api/blocks", headers={"If-None-Match": first.headers["etag"]})
        assert again.status_code == 304 and again.content == b""
        assert again.headers["etag"] == first.headers["etag"]

    def test_a_stale_etag_gets_the_full_list(self, client):
        r = client.get("/api/blocks", headers={"If-None-Match": '"something-else"'})
        assert r.status_code == 200 and r.content

    def test_holds_nothing_private(self, client):
        text = client.get("/api/blocks").text.lower()
        for word in ("secret", "token_ciphertext", "password", "client_secret"):
            assert word not in text


# --------------------------------------------------------------------------- loading
class TestLoad:
    def test_a_new_bot_starts_with_the_starting_blocks(self, me, new_bot):
        bot_id = new_bot()
        r = load(me, bot_id)
        assert r.status_code == 200
        body = r.json()
        assert body["workspace"] == default_workspace()
        assert body["version"] == 0 and body["dropped"] == 0 and body["damaged"] is False
        assert body["files"] == []
        assert body["compile"]["ok"] is True and "import fluxer" in body["compile"]["code"]
        assert set(body["compile"]) == {"code", "ok", "diagnostics", "lines", "assets"}
        assert body["bot"]["id"] == bot_id and body["bot"]["name"] == "Ping Bot"
        assert body["limits"]["max_files"] == 40 and "PNG" in body["limits"]["accepted"]

    def test_never_includes_the_token(self, me, new_bot):
        bot_id = new_bot()
        text = load(me, bot_id).text
        row = stored(bot_id)
        assert TOKEN not in text and row.token_ciphertext not in text
        assert "ciphertext" not in text and "FLUXER_TOKEN" in text  # the code reads it from the environment

    def test_gives_back_what_was_saved(self, me, new_bot):
        bot_id = new_bot()
        assert save(me, bot_id, hello()).status_code == 200
        body = load(me, bot_id).json()
        assert body["workspace"] == hello() and body["version"] == 1
        assert "async def hello(ctx)" in body["compile"]["code"]

    def test_text_that_is_not_json_is_replaced_and_reported(self, me, new_bot):
        bot_id = new_bot()
        store_text(bot_id, "{this is not json")
        body = load(me, bot_id).json()
        assert body["workspace"] == default_workspace() and body["damaged"] is True and body["compile"]["ok"]

    def test_damaged_blocks_are_rebuilt_not_trusted(self, me, new_bot):
        bot_id = new_bot()
        ws = hello()
        ws["blocks"]["blocks"].insert(1, {"type": "sff_not_a_block", "id": "evil", "x": 1, "y": 1})
        ws["blocks"]["blocks"][1]["fields"] = {"__proto__": "x"}
        store_text(bot_id, json.dumps(ws))
        body = load(me, bot_id).json()
        assert body["dropped"] == 1 and body["damaged"] is False
        assert "sff_not_a_block" not in json.dumps(body["workspace"])

    def test_the_compiler_sees_the_uploaded_files(self, me, new_bot):
        bot_id = new_bot()
        ws = workspace(command("pic", {"type": "sff_send_image", "id": "send", "fields": {"FILE": "cat.png"}}))
        assert save(me, bot_id, ws).json()["compile"]["ok"] is False  # no such file yet
        upload(me, bot_id, "cat.png")
        body = load(me, bot_id).json()
        assert body["compile"]["ok"] is True and body["compile"]["assets"] == ["cat.png"]
        assert body["files"][0]["name"] == "cat.png" and body["files"][0]["used_by"] == 1

    def test_needs_a_login(self, client, new_bot, me):
        bot_id = new_bot()
        client.cookies.clear()
        assert client.get(f"/api/bots/{bot_id}/workspace").status_code == 401

    def test_somebody_elses_bot_is_a_404(self, me, stranger, new_bot):
        bot_id = new_bot()
        r = stranger.get(f"/api/bots/{bot_id}/workspace")
        assert r.status_code == 404 and r.json() == {"detail": "That bot doesn't exist."}
        assert me.get(f"/api/bots/{'0' * 32}/workspace").json() == r.json()  # same answer as a bot that isn't there

    @pytest.mark.parametrize("bad_id", ["abc", "../x", "A" * 32, "0" * 31, "0" * 33])
    def test_a_made_up_id_is_a_404(self, me, bad_id):
        assert me.get(f"/api/bots/{bad_id}/workspace").status_code == 404


# --------------------------------------------------------------------------- saving
class TestSave:
    def test_saves_and_returns_the_new_version_and_the_python(self, me, new_bot):
        bot_id = new_bot()
        r = save(me, bot_id, hello())
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == 1 and body["dropped"] == 0
        assert body["compile"]["ok"] is True and "async def hello(ctx)" in body["compile"]["code"]
        assert body["files"] == []
        assert "workspace" not in body  # the stored blocks are what was sent, so the editor needn't reload them
        row = stored(bot_id)
        assert row.workspace_version == 1 and json.loads(row.workspace_json) == hello()

    def test_each_save_moves_the_version_on(self, me, new_bot):
        bot_id = new_bot()
        versions = []
        for version in range(4):
            versions.append(save(me, bot_id, hello(), version=version).json()["version"])
        assert versions == [1, 2, 3, 4]
        assert stored(bot_id).workspace_version == 4

    def test_the_token_is_left_alone(self, me, new_bot):
        bot_id = new_bot()
        before = stored(bot_id)
        save(me, bot_id, hello())
        after = stored(bot_id)
        assert (after.token_ciphertext, after.token_last4, after.name) == (
            before.token_ciphertext,
            before.token_last4,
            before.name,
        )

    def test_a_stale_version_is_refused_and_nothing_is_overwritten(self, me, new_bot):
        bot_id = new_bot()
        first = hello()
        assert save(me, bot_id, first, version=0).status_code == 200
        other = workspace(command("other", reply("Other tab")))
        r = save(me, bot_id, other, version=0)  # the second tab still thinks it is at version 0
        assert r.status_code == 409
        assert r.json()["version"] == 1 and "somewhere else" in r.json()["detail"]
        row = stored(bot_id)
        assert row.workspace_version == 1 and json.loads(row.workspace_json) == first

    def test_after_a_conflict_the_newer_version_can_be_saved_over(self, me, new_bot):
        bot_id = new_bot()
        save(me, bot_id, hello(), version=0)
        stale = save(me, bot_id, workspace(command("other", reply("x"))), version=0)
        assert stale.status_code == 409
        mine = workspace(command("mine", reply("y")))
        assert save(me, bot_id, mine, version=stale.json()["version"]).status_code == 200
        assert json.loads(stored(bot_id).workspace_json) == mine

    def test_a_version_from_the_future_is_a_conflict_too(self, me, new_bot):
        bot_id = new_bot()
        assert save(me, bot_id, hello(), version=7).status_code == 409

    def test_stores_the_cleaned_blocks_and_tells_the_editor_what_changed(self, me, new_bot):
        bot_id = new_bot()
        ws = hello()
        ws["blocks"]["blocks"].append({"type": "sff_made_up", "id": "x", "x": 5, "y": 5})
        ws["blocks"]["blocks"][1].setdefault("inputs", {})["EXTRA"] = {"block": {"type": "sff_text", "id": "t"}}
        body = save(me, bot_id, ws).json()
        assert body["dropped"] >= 1
        assert body["workspace"] == hello()  # the editor is told to show what is stored
        assert json.loads(stored(bot_id).workspace_json) == hello()

    def test_a_workspace_with_problems_still_saves(self, me, new_bot):
        bot_id = new_bot()
        body = save(me, bot_id, workspace(on_ready(reply("hi")))).json()
        assert body["version"] == 1 and body["compile"]["ok"] is False
        assert "needs a message" in body["compile"]["diagnostics"][0]["message"]

    def test_the_cleaner_adds_back_the_locked_blocks(self, me, new_bot):
        bot_id = new_bot()
        body = save(me, bot_id, workspace(command("hello", reply("Hi")), setup=False, login=False)).json()
        kept = json.loads(stored(bot_id).workspace_json)
        types = [b["type"] for b in kept["blocks"]["blocks"]]
        assert types.count("sff_bot_setup") == 1 and types.count("sff_bot_login") == 1
        assert body["workspace"] == kept and body["compile"]["ok"] is True

    def test_variables_and_files_survive_a_round_trip(self, me, new_bot):
        bot_id = new_bot()
        ws = hello()
        ws["variables"] = [{"name": "score", "id": "v1"}, {"name": "friends", "id": "v2", "type": "List"}]
        assert "workspace" not in save(me, bot_id, ws).json()
        assert load(me, bot_id).json()["workspace"]["variables"] == ws["variables"]

    def test_no_variables_written_as_a_missing_key_is_not_a_change(self, me, new_bot):
        """Blockly leaves ``variables`` out when there are none. If that counted as a change the editor would reload
        its own blocks after every save, which throws away the undo history and moves what the person is holding."""
        bot_id = new_bot()
        for gone in ("missing", None):
            ws = hello()
            if gone == "missing":
                del ws["variables"]
            else:
                ws["variables"] = None
            body = save(me, bot_id, ws, version=stored(bot_id).workspace_version).json()
            assert "workspace" not in body and body["dropped"] == 0, gone
        assert load(me, bot_id).json()["workspace"]["variables"] == []

    def test_variables_in_a_different_order_are_not_a_change(self, me, new_bot):
        """Blockly lists variables one kind after another; the server does too, however they were sent."""
        bot_id = new_bot()
        ws = hello()
        ws["variables"] = [
            {"name": "a", "id": "v1"},
            {"name": "L", "id": "v2", "type": "List"},
            {"name": "b", "id": "v3"},
        ]
        body = save(me, bot_id, ws).json()
        assert "workspace" not in body
        assert [v["name"] for v in json.loads(stored(bot_id).workspace_json)["variables"]] == ["a", "b", "L"]
        assert [v["name"] for v in load(me, bot_id).json()["workspace"]["variables"]] == ["a", "b", "L"]

    def test_a_variable_that_had_to_be_changed_is_still_reported(self, me, new_bot):
        bot_id = new_bot()
        ws = hello()
        ws["variables"] = [{"name": "score", "id": "v1"}, {"name": "SCORE", "id": "v2"}]
        body = save(me, bot_id, ws).json()
        assert [v["name"] for v in body["workspace"]["variables"]] == ["score", "SCORE 2"]
        ws["variables"] = [{"name": "score", "id": "v1"}, {"name": "other", "id": "v1"}]  # the same id twice
        body = save(me, bot_id, ws, version=body["version"]).json()
        assert body["workspace"]["variables"] == [{"name": "score", "id": "v1"}]

    def test_variables_that_are_not_a_list_are_replaced_and_reported(self, me, new_bot):
        bot_id = new_bot()
        ws = hello()
        ws["variables"] = {"name": "score", "id": "v1"}
        body = save(me, bot_id, ws).json()
        assert body["workspace"]["variables"] == []

    # ---- bad requests
    def test_not_json(self, me, new_bot):
        bot_id = new_bot()
        r = me.put(
            f"/api/bots/{bot_id}/workspace",
            content=b"{nope",
            headers={**me.h, "Content-Type": "application/json"},
        )
        assert r.status_code == 400 and r.json()["detail"]

    @pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
    def test_not_a_number_is_not_json(self, me, new_bot, constant):
        bot_id = new_bot()
        body = '{"version":0,"workspace":{"blocks":{"blocks":[{"type":"sff_bot_setup","x":' + constant + "}]}}}"
        r = me.put(f"/api/bots/{bot_id}/workspace", content=body, headers={**me.h, "Content-Type": "application/json"})
        assert r.status_code == 400
        assert stored(bot_id).workspace_version == 0

    def test_must_say_it_is_json(self, me, new_bot):
        bot_id = new_bot()
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            r = me.put(
                f"/api/bots/{bot_id}/workspace",
                content=json.dumps({"version": 0, "workspace": hello()}),
                headers={**me.h, "Content-Type": content_type},
            )
            assert r.status_code == 415, content_type
        assert stored(bot_id).workspace_version == 0

    @pytest.mark.parametrize("body", ["[]", '"text"', "5", "null", '[{"version": 0}]', "true"])
    def test_the_body_must_be_an_object(self, me, new_bot, body):
        bot_id = new_bot()
        r = me.put(f"/api/bots/{bot_id}/workspace", content=body, headers={**me.h, "Content-Type": "application/json"})
        assert r.status_code == 400

    @pytest.mark.parametrize("version", [None, "0", -1, 1.5, True, False, [0], {"v": 0}, 2**31, 2**80])
    def test_the_version_must_be_a_whole_number(self, me, new_bot, version):
        bot_id = new_bot()
        r = me.put(f"/api/bots/{bot_id}/workspace", json={"version": version, "workspace": hello()}, headers=me.h)
        assert r.status_code == 422 and "version" in r.json()["detail"]

    def test_the_version_can_be_left_out_of_nothing(self, me, new_bot):
        bot_id = new_bot()
        r = me.put(f"/api/bots/{bot_id}/workspace", json={"workspace": hello()}, headers=me.h)
        assert r.status_code == 422

    @pytest.mark.parametrize("ws", [None, [], "blocks", 5, True])
    def test_the_workspace_must_be_an_object(self, me, new_bot, ws):
        bot_id = new_bot()
        r = save(me, bot_id, ws)
        assert r.status_code == 422 and r.json()["detail"]
        assert stored(bot_id).workspace_version == 0

    def test_blocks_nested_beyond_reason_are_refused(self, me, new_bot):
        bot_id = new_bot()
        # Written as text: Python's own json module can't write something this deep on every version of Python.
        chain = '{"type":"sff_console_print","next":{"block":' * 900 + '{"type":"sff_console_print"}' + "}}" * 900
        body = '{"version":0,"workspace":{"blocks":{"blocks":[' + chain + "]}}}"
        r = me.put(f"/api/bots/{bot_id}/workspace", content=body, headers={**me.h, "Content-Type": "application/json"})
        assert r.status_code in (400, 422) and r.json()["detail"]
        assert stored(bot_id).workspace_version == 0

    def test_thousands_of_blocks_that_name_missing_variables_are_refused_quickly(self, me, new_bot):
        """Once ~50 seconds of work for the server (and every other visitor waiting), from a request under the size limit."""
        bot_id = new_bot()
        pointing = [
            {"type": "sff_variable", "fields": {"VAR": {"id": f"ghost{i}"}}, "x": 0, "y": i} for i in range(12_000)
        ]
        started = time.perf_counter()
        r = save(me, bot_id, {"blocks": {"languageVersion": 0, "blocks": pointing}, "variables": []})
        assert r.status_code == 422 and "variables" in r.json()["detail"]
        assert time.perf_counter() - started < 3
        assert stored(bot_id).workspace_version == 0

    def test_an_enormous_save_is_refused_before_it_is_read(self, me, new_bot):
        bot_id = new_bot()
        too_big = " " * (get_limit() + 5000)
        r = me.put(
            f"/api/bots/{bot_id}/workspace",
            content='{"version":0,"workspace":{}}' + too_big,
            headers={**me.h, "Content-Type": "application/json"},
        )
        assert r.status_code == 413 and "too big" in r.json()["detail"]

    def test_blocks_that_come_to_more_than_the_limit_are_refused(self, me, new_bot, monkeypatch):
        bot_id = new_bot()
        monkeypatch.setenv("MAX_WORKSPACE_BYTES", "1500")
        from scratch_fluxer import config

        config.reset_settings_cache()
        big = workspace(*[command(f"c{n}", reply("x" * 50)) for n in range(30)])
        r = save(me, bot_id, big)
        assert r.status_code in (413, 422) and "too big" in r.json()["detail"]
        assert stored(bot_id).workspace_version == 0

    # ---- who may save
    def test_needs_a_login(self, client, me, new_bot):
        bot_id = new_bot()
        client.cookies.clear()
        r = client.put(f"/api/bots/{bot_id}/workspace", json={"version": 0, "workspace": hello()})
        assert r.status_code == 401

    def test_needs_the_csrf_header(self, me, new_bot):
        bot_id = new_bot()
        body = {"version": 0, "workspace": hello()}
        assert me.put(f"/api/bots/{bot_id}/workspace", json=body).status_code == 403
        assert me.put(f"/api/bots/{bot_id}/workspace", json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
        assert stored(bot_id).workspace_version == 0

    def test_refuses_another_site(self, me, new_bot):
        bot_id = new_bot()
        r = me.put(
            f"/api/bots/{bot_id}/workspace",
            json={"version": 0, "workspace": hello()},
            headers={**me.h, "Origin": "https://evil.example"},
        )
        assert r.status_code == 403 and stored(bot_id).workspace_version == 0

    def test_somebody_elses_bot_cannot_be_saved_to(self, me, stranger, new_bot):
        bot_id = new_bot()
        r = save(stranger, bot_id, hello())
        assert r.status_code == 404
        assert stored(bot_id).workspace_version == 0 and not stored(bot_id).workspace_json

    def test_is_rate_limited_per_person(self, me, new_bot):
        bot_id = new_bot()
        codes = [save(me, bot_id, hello(), version=n).status_code for n in range(92)]
        assert codes[:90] == [200] * 90 and codes[90:] == [429, 429]


def get_limit() -> int:
    from scratch_fluxer.config import get_settings

    return get_settings().max_workspace_bytes


# --------------------------------------------------------------------------- the code, taken out
class TestExport:
    def test_the_python_file(self, me, new_bot):
        bot_id = new_bot()
        save(me, bot_id, hello())
        r = me.get(f"/bots/{bot_id}/export/main.py")
        assert r.status_code == 200
        assert r.headers["content-disposition"] == 'attachment; filename="main.py"'
        assert r.headers["content-type"].startswith("text/x-python") and r.headers["cache-control"] == "no-store"
        assert r.text == load(me, bot_id).json()["compile"]["code"]
        compile(r.text, "main.py", "exec")

    def test_the_file_never_holds_the_token(self, me, new_bot):
        bot_id = new_bot()
        r = me.get(f"/bots/{bot_id}/export/main.py")
        assert TOKEN not in r.text and "AAAAAAAA" not in r.text
        assert r.text.rstrip().endswith('bot.run(os.environ["FLUXER_TOKEN"])')

    def test_a_folder_with_everything_needed(self, me, new_bot):
        bot_id = new_bot(name="My Quiz Bot!")
        upload(me, bot_id, "cat.png")
        upload(me, bot_id, "quotes.json", b'["a", "b"]')
        r = me.get(f"/bots/{bot_id}/export/bot.zip")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        assert r.headers["content-disposition"] == 'attachment; filename="My-Quiz-Bot.zip"'
        assert r.headers["cache-control"] == "no-store"
        archive = zip_of(r)
        assert archive.testzip() is None
        assert sorted(archive.namelist()) == [
            "README.txt",
            "assets/cat.png",
            "assets/quotes.json",
            "main.py",
            "requirements.txt",
        ]
        assert archive.read("assets/cat.png") == PNG and archive.read("assets/quotes.json") == b'["a", "b"]'
        assert archive.read("main.py").decode() == me.get(f"/bots/{bot_id}/export/main.py").text
        assert archive.read("requirements.txt").decode().startswith("fluxer.py")
        assert "FLUXER_TOKEN" in archive.read("README.txt").decode()

    def test_no_file_in_the_folder_holds_the_token(self, me, new_bot):
        bot_id = new_bot()
        archive = zip_of(me.get(f"/bots/{bot_id}/export/bot.zip"))
        for name in archive.namelist():
            assert TOKEN.encode() not in archive.read(name), name

    @pytest.mark.parametrize("name", ["....", "///", "ü ñ", "a" * 80])
    def test_odd_bot_names_make_a_safe_file_name(self, me, new_bot, name):
        bot_id = new_bot(name=name)
        disposition = me.get(f"/bots/{bot_id}/export/bot.zip").headers["content-disposition"]
        filename = disposition.split('filename="', 1)[1].rstrip('"')
        assert filename.endswith(".zip") and "/" not in filename and "\\" not in filename and filename.isascii()
        assert len(filename) <= 44

    def test_code_with_problems_cannot_be_downloaded(self, me, new_bot):
        bot_id = new_bot()
        save(me, bot_id, workspace(on_ready(reply("hi"))))
        for path in ("main.py", "bot.zip"):
            r = me.get(f"/bots/{bot_id}/export/{path}")
            assert r.status_code == 409 and "Fix the problems" in r.text and "attachment" not in str(r.headers)

    def test_needs_a_login(self, client, me, new_bot):
        bot_id = new_bot()
        client.cookies.clear()
        r = client.get(f"/bots/{bot_id}/export/main.py", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")

    def test_somebody_elses_bot_is_a_404(self, me, stranger, new_bot):
        bot_id = new_bot()
        upload(me, bot_id, "cat.png")
        for path in ("main.py", "bot.zip"):
            r = stranger.get(f"/bots/{bot_id}/export/{path}")
            assert r.status_code == 404 and b"PNG" not in r.content

    def test_is_rate_limited_per_person(self, me, new_bot):
        bot_id = new_bot()
        codes = [me.get(f"/bots/{bot_id}/export/main.py").status_code for _ in range(22)]
        assert codes[:20] == [200] * 20 and codes[20:] == [429, 429]


# --------------------------------------------------------------------------- the page
class TestEditorPage:
    def test_needs_a_login(self, client, me, new_bot):
        bot_id = new_bot()
        client.cookies.clear()
        r = client.get(f"/bots/{bot_id}/edit", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == f"/login?next=/bots/{bot_id}/edit"

    def test_opens_for_the_owner(self, me, new_bot):
        bot_id = new_bot()
        r = me.get(f"/bots/{bot_id}/edit")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
        assert "Ping Bot" in r.text and bot_id in r.text

    def test_never_holds_the_token(self, me, new_bot):
        bot_id = new_bot()
        text = me.get(f"/bots/{bot_id}/edit").text
        assert TOKEN not in text and stored(bot_id).token_ciphertext not in text

    def test_somebody_elses_bot_is_a_404_page(self, me, stranger, new_bot):
        bot_id = new_bot()
        r = stranger.get(f"/bots/{bot_id}/edit")
        assert r.status_code == 404 and "Ping Bot" not in r.text

    @pytest.mark.parametrize("bad_id", ["abc", "0" * 32, "../x"])
    def test_a_bot_that_isnt_there_is_a_404_page(self, me, bad_id):
        r = me.get(f"/bots/{bad_id}/edit")
        assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
