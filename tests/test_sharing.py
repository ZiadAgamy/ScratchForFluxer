"""Sharing a bot: invite links, who may do what, and that nothing leaks to a person who shouldn't have it."""

from __future__ import annotations

import copy
import re
from contextlib import contextmanager
from datetime import timedelta

import pytest
from blocks import command, reply, workspace
from fastapi.testclient import TestClient
from fluxer_mock import TOKEN
from sqlalchemy import select

from scratch_fluxer.db import _call_with_session
from scratch_fluxer.models import Bot, BotInvite, BotMember, User, utcnow

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
HELLO = workspace(command("hello", reply("Hello!")))


# --------------------------------------------------------------------------- helpers
@pytest.fixture
def person(app, make_user):
    """``person("name")`` is another logged-in person with their own client."""
    clients: list = []
    counter = iter(range(10, 99))

    def _make(name: str = "friend"):
        user_id, csrf, cookies = make_user(fluxer_id=f"90000000000{next(counter):04d}", username=name, global_name=name.title())
        other = TestClient(app, base_url="http://testserver")
        other.__enter__()
        clients.append(other)
        other.cookies.update(cookies)
        other.user_id, other.csrf, other.h = user_id, csrf, {"X-CSRF-Token": csrf}
        return other

    yield _make
    for other in clients:
        other.__exit__(None, None, None)


def invite(me, bot_id, perms=("edit",), expires="7d", max_uses=None):
    r = me.post(f"/api/bots/{bot_id}/invites", json={"perms": list(perms), "expires": expires, "max_uses": max_uses}, headers=me.h)
    assert r.status_code == 201, r.text
    body = r.json()
    code = body["url"].rsplit("/", 1)[1]
    return body["invite"], code


def join(client, code):
    return client.post("/api/invites/accept", json={"code": code}, headers=client.h)


def joined(me, person_, bot_id, perms=("edit",), **kw):
    """A new person who joined through a new link; returns their client."""
    friend = person_()
    _inv, code = invite(me, bot_id, perms, **kw)
    assert join(friend, code).status_code == 200
    return friend


def put_blocks(client, bot_id, version=0, ws=None):
    return client.put(
        f"/api/bots/{bot_id}/workspace", json={"version": version, "workspace": ws or copy.deepcopy(HELLO)}, headers=client.h
    )


def rows(model):
    def _read(db):
        return [{c.name: getattr(row, c.name) for c in model.__table__.columns} for row in db.execute(select(model)).scalars()]

    return _call_with_session(_read, (), {})


def change(model, where_id, **values):
    def _write(db):
        row = db.get(model, where_id)
        for key, value in values.items():
            setattr(row, key, value)

    _call_with_session(_write, (), {})


# --------------------------------------------------------------------------- making and using a link
def test_the_owner_makes_a_link_and_only_its_hash_is_stored(me, new_bot):
    bot_id = new_bot()
    view, code = invite(me, bot_id, ("edit", "files"))
    assert re.fullmatch(r"[A-Za-z0-9_-]{32}", code)
    assert view["perms"] == ["edit", "files"] and view["state"] == "active" and view["hint"] == code[-4:]
    stored = rows(BotInvite)[0]
    assert code not in " ".join(str(value) for value in stored.values())  # the link itself is nowhere in the database
    assert stored["code_hash"] != code and len(stored["code_hash"]) == 64


def test_a_person_joins_and_the_bot_shows_up_for_them(me, person, new_bot):
    bot_id = new_bot()
    friend = person("sam")
    assert friend.get(f"/api/bots/{bot_id}/workspace").status_code == 404  # a stranger can't tell it exists
    _view, code = invite(me, bot_id)
    r = join(friend, code)
    assert r.status_code == 200 and r.json() == {"bot_id": bot_id, "redirect": f"/bots/{bot_id}/edit", "already": False}
    assert friend.get(f"/api/bots/{bot_id}/workspace").status_code == 200
    page = friend.get("/bots").text
    assert "Shared with me" in page and "AGAMY" in page
    assert "Shared by" in friend.get(f"/bots/{bot_id}/edit").text


def test_joining_twice_does_not_use_another_place(me, person, new_bot):
    bot_id = new_bot()
    friend = person("sam")
    view, code = invite(me, bot_id, max_uses=5)
    assert join(friend, code).json()["already"] is False
    assert join(friend, code).json()["already"] is True
    assert rows(BotInvite)[0]["uses"] == 1 and len(rows(BotMember)) == 1


def test_the_owner_cannot_join_their_own_bot(me, new_bot):
    _view, code = invite(me, new_bot())
    assert join(me, code).status_code == 409


def test_a_link_with_one_place_works_for_one_person(me, person, new_bot):
    bot_id = new_bot()
    _view, code = invite(me, bot_id, max_uses=1)
    assert join(person("a"), code).status_code == 200
    second = join(person("b"), code)
    assert second.status_code == 410 and "ended" in second.json()["detail"]
    assert len(rows(BotMember)) == 1


def test_an_expired_or_cancelled_link_does_not_work(me, person, new_bot):
    bot_id = new_bot()
    view, code = invite(me, bot_id)
    change(BotInvite, view["id"], expires_at=utcnow() - timedelta(minutes=1))
    assert join(person("a"), code).status_code == 410
    view2, code2 = invite(me, bot_id)
    assert me.delete(f"/api/bots/{bot_id}/invites/{view2['id']}", headers=me.h).status_code == 200
    assert join(person("b"), code2).status_code == 410
    assert rows(BotMember) == []


def test_a_wrong_or_malformed_link_is_refused(me, person, new_bot):
    new_bot()
    friend = person("a")
    assert join(friend, "x" * 32).status_code == 404
    assert join(friend, "short").status_code == 404
    assert friend.post("/api/invites/accept", json={"code": 5}, headers=friend.h).status_code == 422
    assert friend.post("/api/invites/accept", json={}, headers=friend.h).status_code == 422


def test_a_bot_holds_only_a_few_people(me, person, new_bot):
    bot_id = new_bot()
    _view, code = invite(me, bot_id)
    for index in range(5):
        assert join(person(f"p{index}"), code).status_code == 200
    sixth = join(person("late"), code)
    assert sixth.status_code == 409 and "5 people" in sixth.json()["detail"]
    assert rows(BotInvite)[0]["uses"] == 5  # the refused person did not use up a place


def test_a_bot_has_a_limit_of_active_links(me, new_bot):
    bot_id = new_bot()
    for _ in range(10):
        invite(me, bot_id)
    r = me.post(f"/api/bots/{bot_id}/invites", json={"perms": [], "expires": "1d", "max_uses": None}, headers=me.h)
    assert r.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"perms": ["edit", "root"], "expires": "1d", "max_uses": 1},
        {"perms": "edit", "expires": "1d", "max_uses": 1},
        {"perms": [1], "expires": "1d", "max_uses": 1},
        {"perms": ["edit"], "expires": "forever", "max_uses": 1},
        {"perms": ["edit"], "expires": "1d", "max_uses": 3},
        {"perms": ["edit"], "expires": "1d", "max_uses": True},
        {"perms": ["edit"], "expires": 5, "max_uses": 1},
    ],
)
def test_a_bad_request_for_a_link_is_refused(me, new_bot, body):
    bot_id = new_bot()
    assert me.post(f"/api/bots/{bot_id}/invites", json=body, headers=me.h).status_code == 422
    assert rows(BotInvite) == []


# --------------------------------------------------------------------------- what a member may do
def test_a_person_who_may_only_look_cannot_change_anything(me, person, new_bot):
    bot_id = new_bot()
    friend = joined(me, person, bot_id, perms=())
    assert friend.get(f"/api/bots/{bot_id}/workspace").status_code == 200
    assert friend.get(f"/api/bots/{bot_id}/assets").status_code == 200
    assert put_blocks(friend, bot_id).status_code == 403
    assert friend.put(f"/api/bots/{bot_id}/assets/a.png", content=PNG, headers=friend.h).status_code == 403
    assert friend.post(f"/api/bots/{bot_id}/run", headers=friend.h).status_code == 403
    assert friend.post(f"/api/bots/{bot_id}/restart", headers=friend.h).status_code == 403
    assert friend.post(f"/api/bots/{bot_id}/stop", headers=friend.h).status_code == 403
    assert friend.get(f"/api/bots/{bot_id}/run").status_code == 403
    assert friend.get(f"/api/bots/{bot_id}/emojis").status_code == 403
    assert friend.get(f"/bots/{bot_id}/export/main.py").status_code == 403
    assert friend.get(f"/bots/{bot_id}/export/bot.zip").status_code == 403


def test_each_permission_opens_exactly_what_it_says(me, person, new_bot):
    bot_id = new_bot()
    editor = joined(me, person, bot_id, perms=("edit",))
    assert put_blocks(editor, bot_id).status_code == 200
    assert editor.put(f"/api/bots/{bot_id}/assets/a.png", content=PNG, headers=editor.h).status_code == 403

    files = joined(me, person, bot_id, perms=("files",))
    assert files.put(f"/api/bots/{bot_id}/assets/a.png", content=PNG, headers=files.h).status_code in (200, 201)
    assert put_blocks(files, bot_id, version=1).status_code == 403
    assert files.delete(f"/api/bots/{bot_id}/assets/a.png", headers=files.h).status_code == 200

    exporter = joined(me, person, bot_id, perms=("export",))
    assert exporter.get(f"/bots/{bot_id}/export/main.py").status_code == 200

    console = joined(me, person, bot_id, perms=("console",))
    assert console.get(f"/api/bots/{bot_id}/run").status_code != 403  # reading is allowed (the runner is off in tests)
    assert console.post(f"/api/bots/{bot_id}/run", headers=console.h).status_code == 403

    runner = joined(me, person, bot_id, perms=("run",))
    assert runner.post(f"/api/bots/{bot_id}/run", headers=runner.h).status_code == 503  # allowed; the runner is just off


def test_a_member_never_sees_the_token_or_its_ending(me, person, new_bot):
    bot_id = new_bot()
    friend = joined(me, person, bot_id, perms=("edit", "files", "run", "console", "export"))
    row = rows(Bot)[0]
    for text in (
        friend.get(f"/api/bots/{bot_id}/workspace").text,
        friend.get(f"/bots/{bot_id}/edit").text,
        friend.get("/bots").text,
        friend.get(f"/bots/{bot_id}/export/main.py").text,
    ):
        assert TOKEN not in text and row["token_ciphertext"] not in text
        assert f'"token_last4":"{row["token_last4"]}"' not in text.replace(" ", "")
    assert friend.get(f"/api/bots/{bot_id}/workspace").json()["bot"]["token_last4"] == ""
    assert me.get(f"/api/bots/{bot_id}/workspace").json()["bot"]["token_last4"] == row["token_last4"]


def test_a_member_cannot_do_what_only_the_owner_can(me, person, new_bot):
    bot_id = new_bot()
    friend = joined(me, person, bot_id, perms=("edit", "files", "run", "console", "export"))
    assert friend.patch(f"/api/bots/{bot_id}", json={"name": "mine now"}, headers=friend.h).status_code == 404
    assert friend.put(f"/api/bots/{bot_id}/token", json={"token": TOKEN}, headers=friend.h).status_code in (403, 404)
    assert friend.delete(f"/api/bots/{bot_id}", headers=friend.h).status_code == 404
    assert friend.get(f"/api/bots/{bot_id}/sharing").status_code == 404
    r = friend.post(f"/api/bots/{bot_id}/invites", json={"perms": ["edit"], "expires": "1d", "max_uses": None}, headers=friend.h)
    assert r.status_code == 404
    member_id = rows(BotMember)[0]["id"]
    assert friend.patch(f"/api/bots/{bot_id}/members/{member_id}", json={"perms": []}, headers=friend.h).status_code == 404
    assert friend.delete(f"/api/bots/{bot_id}/members/{member_id}", headers=friend.h).status_code == 404
    assert rows(Bot)[0]["name"] != "mine now"


def test_a_stranger_cannot_manage_sharing_for_a_bot_they_do_not_own(me, person, new_bot):
    bot_id = new_bot()
    other = person("nosy")
    for call in (
        lambda: other.get(f"/api/bots/{bot_id}/sharing"),
        lambda: other.post(f"/api/bots/{bot_id}/invites", json={"perms": [], "expires": "1d", "max_uses": None}, headers=other.h),
        lambda: other.delete(f"/api/bots/{bot_id}/membership", headers=other.h),
    ):
        assert call().status_code in (404, 409)


def test_cancelling_a_link_of_another_bot_does_nothing(me, person, new_bot, fluxer):
    from fluxer_mock import OWNER_FLUXER_ID  # noqa: F401

    mine = new_bot()
    view, code = invite(me, mine)
    other = person("owner2")
    assert other.delete(f"/api/bots/{mine}/invites/{view['id']}", headers=other.h).status_code == 404
    assert join(person("a"), code).status_code == 200  # the link still works


def test_two_editors_still_get_the_conflict_message_not_a_silent_overwrite(me, person, new_bot):
    bot_id = new_bot()
    friend = joined(me, person, bot_id, perms=("edit",))
    assert put_blocks(me, bot_id, version=0).status_code == 200
    stale = put_blocks(friend, bot_id, version=0)
    assert stale.status_code == 409 and stale.json()["version"] == 1


def test_a_member_saves_with_a_valid_csrf_token_only(me, person, new_bot):
    bot_id = new_bot()
    friend = joined(me, person, bot_id)
    r = friend.put(f"/api/bots/{bot_id}/workspace", json={"version": 0, "workspace": HELLO})
    assert r.status_code == 403


# --------------------------------------------------------------------------- managing people
def test_the_owner_sees_links_and_people_and_changes_what_a_person_may_do(me, person, new_bot):
    bot_id = new_bot()
    friend = joined(me, person, bot_id, perms=())
    info = me.get(f"/api/bots/{bot_id}/sharing").json()
    assert len(info["members"]) == 1 and info["members"][0]["name"] == "Friend" and info["members"][0]["perms"] == []
    assert [item["key"] for item in info["permissions"]] == ["edit", "files", "run", "console", "export"]
    assert put_blocks(friend, bot_id).status_code == 403
    member_id = info["members"][0]["id"]
    r = me.patch(f"/api/bots/{bot_id}/members/{member_id}", json={"perms": ["edit"]}, headers=me.h)
    assert r.status_code == 200 and r.json()["member"]["perms"] == ["edit"]
    assert put_blocks(friend, bot_id).status_code == 200  # the change applies at once
    assert me.patch(f"/api/bots/{bot_id}/members/{member_id}", json={"perms": ["fly"]}, headers=me.h).status_code == 422
    assert me.patch(f"/api/bots/{bot_id}/members/nope", json={"perms": []}, headers=me.h).status_code == 404


def test_removing_a_person_ends_their_access_straight_away(me, person, new_bot):
    bot_id = new_bot()
    friend = joined(me, person, bot_id)
    member_id = rows(BotMember)[0]["id"]
    assert me.delete(f"/api/bots/{bot_id}/members/{member_id}", headers=me.h).status_code == 200
    assert friend.get(f"/api/bots/{bot_id}/workspace").status_code == 404
    assert put_blocks(friend, bot_id).status_code == 404
    assert "Shared with me" not in friend.get("/bots").text


def test_removing_a_person_frees_their_place_but_not_the_old_link(me, person, new_bot):
    bot_id = new_bot()
    _view, code = invite(me, bot_id, max_uses=1)
    friend = person("a")
    assert join(friend, code).status_code == 200
    me.delete(f"/api/bots/{bot_id}/members/{rows(BotMember)[0]['id']}", headers=me.h)
    assert join(friend, code).status_code == 410  # the link had one place and it was used


def test_a_person_can_leave_and_an_owner_cannot(me, person, new_bot):
    bot_id = new_bot()
    friend = joined(me, person, bot_id)
    assert friend.delete(f"/api/bots/{bot_id}/membership", headers=friend.h).json()["redirect"] == "/bots"
    assert friend.get(f"/api/bots/{bot_id}/workspace").status_code == 404
    assert me.delete(f"/api/bots/{bot_id}/membership", headers=me.h).status_code == 409


def test_removing_the_bot_removes_its_links_and_people(me, person, new_bot):
    bot_id = new_bot()
    joined(me, person, bot_id)
    assert rows(BotInvite) and rows(BotMember)
    assert me.delete(f"/api/bots/{bot_id}", headers=me.h).status_code == 200
    assert rows(BotInvite) == [] and rows(BotMember) == []


def test_deleting_a_members_data_leaves_the_bot_and_its_other_people(me, person, new_bot):
    bot_id = new_bot()
    keep = joined(me, person, bot_id)
    gone = joined(me, person, bot_id)
    assert gone.request("DELETE", "/api/me", json={"confirm": "friend"}, headers=gone.h).status_code == 200
    assert [row["user_id"] for row in rows(BotMember)] == [keep.user_id]
    assert me.get(f"/api/bots/{bot_id}/workspace").status_code == 200


def test_deleting_the_owners_data_removes_the_shared_bot_for_everyone(me, person, new_bot):
    bot_id = new_bot()
    friend = joined(me, person, bot_id)
    assert me.request("DELETE", "/api/me", json={"confirm": "agamy"}, headers=me.h).status_code == 200
    assert friend.get(f"/api/bots/{bot_id}/workspace").status_code == 404
    assert rows(BotMember) == [] and rows(BotInvite) == [] and rows(Bot) == []
    assert any(row["username"] == "friend" for row in rows(User))  # the friend's own account stays


# --------------------------------------------------------------------------- the invite page
def test_the_invite_page_names_the_bot_and_what_joining_allows(me, person, new_bot):
    bot_id = new_bot(name="Moka")
    _view, code = invite(me, bot_id, ("edit", "run"))
    friend = person("sam")
    page = friend.get(f"/invite/{code}")
    assert page.status_code == 200 and "Moka" in page.text and "AGAMY" in page.text
    assert "Change the blocks" in page.text and "Run the bot" in page.text and "Download the code" in page.text
    assert f'data-code="{code}"' in page.text
    assert page.headers["cache-control"] == "no-store"
    assert TOKEN not in page.text


def test_the_invite_page_says_when_a_link_is_not_valid_or_has_ended(me, person, new_bot):
    bot_id = new_bot()
    friend = person("sam")
    assert friend.get("/invite/" + "z" * 32).status_code == 404
    view, code = invite(me, bot_id)
    me.delete(f"/api/bots/{bot_id}/invites/{view['id']}", headers=me.h)
    page = friend.get(f"/invite/{code}")
    assert "cancelled" in page.text and 'id="invite-join"' not in page.text


def test_the_invite_page_says_when_you_are_already_on_the_bot(me, person, new_bot):
    bot_id = new_bot()
    friend = joined(me, person, bot_id)
    _view, code = invite(me, bot_id)
    assert "already on this bot" in friend.get(f"/invite/{code}").text
    assert "your own bot" in me.get(f"/invite/{code}").text


def test_a_logged_out_visitor_is_sent_to_log_in_and_back(client, me, new_bot):
    _view, code = invite(me, new_bot())
    visitor = TestClient(client.app, base_url="http://testserver")
    r = visitor.get(f"/invite/{code}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=/invite/")


def test_joining_needs_a_login_and_a_csrf_token(client, me, person, new_bot):
    _view, code = invite(me, new_bot())
    visitor = TestClient(client.app, base_url="http://testserver")
    assert visitor.post("/api/invites/accept", json={"code": code}).status_code == 401
    friend = person("sam")
    assert friend.post("/api/invites/accept", json={"code": code}).status_code == 403
    assert rows(BotMember) == []


def test_the_editor_page_tells_each_person_what_they_may_do(me, person, new_bot):
    bot_id = new_bot()
    owner_page = me.get(f"/bots/{bot_id}/edit").text
    assert 'data-role="owner"' in owner_page and 'id="share-btn"' in owner_page and 'id="leave-btn"' not in owner_page
    friend = joined(me, person, bot_id, perms=("edit", "console"))
    page = friend.get(f"/bots/{bot_id}/edit").text
    assert 'data-role="member"' in page and 'data-can-edit="yes"' in page
    assert 'data-can-files="no"' in page and 'data-can-run="no"' in page and 'data-can-console="yes"' in page
    assert 'id="share-btn"' not in page and 'id="leave-btn"' in page
    assert friend.get("/bots/" + "0" * 32 + "/edit").status_code == 404


def test_the_shared_list_shows_what_the_person_can_do(me, person, new_bot):
    bot_id = new_bot()
    friend = joined(me, person, bot_id, perms=("edit", "files"))
    page = friend.get("/bots").text
    assert "You can: look, change the blocks, manage files" in page
    assert "Token ends in" not in page
