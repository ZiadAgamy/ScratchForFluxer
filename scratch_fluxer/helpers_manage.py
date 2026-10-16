"""Helper functions for the blocks that make and delete channels and roles, start threads and write forum posts.

They are written into a bot's file only when a block needs them. Each one talks to Fluxer through ``bot._http`` (the
connection fluxer.py already holds) and never raises: when something goes wrong (a missing permission, a wrong ID) it
prints one clear line to the console.

``codecheck.py`` allows ``bot._http`` for exactly the calls written here: a few named methods, and ``request`` with one
of the routes in ``codecheck.HTTP_ROUTES``. Change the two together.
"""

from __future__ import annotations

from typing import Any

HELPERS: dict[str, dict[str, Any]] = {
    "made": {
        "imports": (),
        "needs": (),
        "source": """made = {"channel": "", "role": "", "thread": ""}""",
    },
    "make_channel": {
        "imports": (),
        "needs": ("as_id", "made"),
        "source": '''async def make_channel(guild_id, kind, name, extra="", parent=""):
    """Make a channel. kind is Fluxer's number for it (0 text, 2 voice, 5 announcement, 15 forum, 16 media, 998 link)."""
    name = str(name).strip()
    words = str(extra).strip()
    parent_id = str(as_id(parent)) if as_id(parent) else None
    if not guild_id or not name:
        print("Channel not made: it needs a community and a name.")
        return
    if kind == 998 and not words.lower().startswith(("http://", "https://")):
        print("Channel not made: a link channel needs a web address that starts with https://")
        return
    try:
        if kind == 998:
            data = await bot._http.create_guild_channel(str(guild_id), name=name, type=998, url=words, parent_id=parent_id)
        else:
            data = await bot._http.create_guild_channel(
                str(guild_id), name=name, type=kind, topic=words or None, parent_id=parent_id
            )
        made["channel"] = str(data["id"])
        print("Made the channel " + name)
    except Exception:
        print("Channel not made. Check that the bot has the Manage Channels permission and the details are right.")
''',
    },
    "remove_channel": {
        "imports": (),
        "needs": ("as_id",),
        "source": '''async def remove_channel(channel_id):
    """Delete a channel or a thread. A problem is printed, never a crash."""
    if not as_id(channel_id):
        print("Nothing deleted: give the ID of the channel.")
        return
    try:
        await bot._http.delete_channel(str(as_id(channel_id)))
        print("Deleted the channel " + str(as_id(channel_id)))
    except Exception:
        print("Channel not deleted. Check the ID and that the bot has the Manage Channels permission.")
''',
    },
    "make_role": {
        "imports": (),
        "needs": ("made",),
        "source": '''async def make_role(guild_id, name, color=0, hoist=False, mentionable=False):
    """Make a role with no special permissions. A problem is printed, never a crash."""
    name = str(name).strip()
    if not guild_id or not name:
        print("Role not made: it needs a community and a name.")
        return
    try:
        data = await bot._http.create_guild_role(
            str(guild_id), name=name, color=color, hoist=hoist, mentionable=mentionable
        )
        made["role"] = str(data["id"])
        print("Made the role " + name)
    except Exception:
        print("Role not made. Check that the bot has the Manage Roles permission.")
''',
    },
    "remove_role": {
        "imports": (),
        "needs": ("as_id",),
        "source": '''async def remove_role(guild_id, role_id):
    """Delete a role. A problem is printed, never a crash."""
    if not guild_id or not as_id(role_id):
        print("Nothing deleted: give the ID of the role.")
        return
    try:
        await bot._http.delete_guild_role(str(guild_id), str(as_id(role_id)))
        print("Deleted the role " + str(as_id(role_id)))
    except Exception:
        print("Role not deleted. The bot needs Manage Roles, and its own role must be above that role.")
''',
    },
    "make_thread": {
        "imports": (),
        "needs": ("as_id", "made"),
        "source": '''async def make_thread(channel_id, name, kind="public"):
    """Start a thread in a text or announcement channel. A problem is printed, never a crash."""
    name = str(name).strip()
    if not as_id(channel_id) or not name:
        print("Thread not started: it needs a channel and a name.")
        return
    try:
        data = await bot._http.request(
            bot._http._route("POST", "/channels/{channel_id}/threads", channel_id=str(as_id(channel_id))),
            json={"name": name, "type": 12 if kind == "private" else 11},
        )
        made["thread"] = str(data["id"])
        print("Started the thread " + name)
    except Exception:
        print("Thread not started. Use a text or announcement channel, and check the bot may create threads.")
''',
    },
    "thread_from_message": {
        "imports": (),
        "needs": ("as_id", "made"),
        "source": '''async def thread_from_message(channel_id, message_id, name):
    """Start a thread from a message. A problem is printed, never a crash."""
    name = str(name).strip()
    if not as_id(channel_id) or not as_id(message_id) or not name:
        print("Thread not started: it needs a message and a name.")
        return
    try:
        data = await bot._http.request(
            bot._http._route(
                "POST",
                "/channels/{channel_id}/messages/{message_id}/threads",
                channel_id=str(as_id(channel_id)),
                message_id=str(as_id(message_id)),
            ),
            json={"name": name},
        )
        made["thread"] = str(data["id"])
        print("Started the thread " + name)
    except Exception:
        print("Thread not started. A message can only start one thread, and the bot must be allowed to create threads.")
''',
    },
    "send_in_thread": {
        "imports": (),
        "needs": ("as_id",),
        "source": '''async def send_in_thread(thread_id, text):
    """Write a message inside a thread. A problem is printed, never a crash."""
    if not as_id(thread_id):
        print("Nothing sent: give the ID of the thread.")
        return
    try:
        await bot._http.send_message(str(as_id(thread_id)), content=str(text))
    except Exception:
        print("Could not write in the thread. Check the ID and that the thread is not locked.")
''',
    },
    "change_thread": {
        "imports": (),
        "needs": ("as_id",),
        "source": '''async def change_thread(thread_id, action):
    """Close, reopen, lock or unlock a thread. A problem is printed, never a crash."""
    changes = {
        "archive": {"archived": True},
        "unarchive": {"archived": False},
        "lock": {"locked": True},
        "unlock": {"locked": False},
    }
    if not as_id(thread_id) or action not in changes:
        print("Thread not changed: give the ID of the thread.")
        return
    try:
        await bot._http.request(
            bot._http._route("PATCH", "/channels/{channel_id}", channel_id=str(as_id(thread_id))),
            json=changes[action],
        )
    except Exception:
        print("Thread not changed. Check the ID and that the bot may manage threads.")
''',
    },
    "forum_tags": {
        "imports": (),
        "needs": ("as_id",),
        "source": '''async def forum_tags(channel_id):
    """The names of the tags a forum or media channel has, as a list. Empty when anything goes wrong."""
    if not as_id(channel_id):
        return []
    try:
        channel = await bot._http.request(
            bot._http._route("GET", "/channels/{channel_id}", channel_id=str(as_id(channel_id)))
        )
        names = []
        for tag in channel["available_tags"]:
            names.append(str(tag["name"]))
        return names
    except Exception:
        print("Could not read the tags. Check the channel ID.")
        return []
''',
    },
    "forum_post": {
        "imports": (),
        "needs": ("as_id", "made"),
        "source": '''async def forum_post(channel_id, title, text, tags="", picture=None, picture_url=""):
    """Publish a post in a forum or media channel. A problem is printed, never a crash."""
    title = str(title).strip()
    body = str(text).strip() or title
    if not as_id(channel_id) or not title:
        print("Post not published: it needs a forum channel and a title.")
        return
    try:
        wanted = []
        for part in str(tags).split(","):
            if part.strip():
                wanted.append(part.strip().lower())
        applied = []
        if wanted:
            channel = await bot._http.request(
                bot._http._route("GET", "/channels/{channel_id}", channel_id=str(as_id(channel_id)))
            )
            matched = []
            for tag in channel["available_tags"]:
                if str(tag["name"]).lower() in wanted and len(applied) < 5:
                    applied.append(str(tag["id"]))
                    matched.append(str(tag["name"]).lower())
            for name in wanted:
                if name not in matched:
                    print("The tag " + name + " does not exist in this channel, so it was skipped.")
        message = {"content": body}
        link = str(picture_url).strip()
        if link.lower().startswith(("http://", "https://")):
            message["embeds"] = [{"image": {"url": link}}]
        data = await bot._http.request(
            bot._http._route("POST", "/channels/{channel_id}/threads", channel_id=str(as_id(channel_id))),
            json={"name": title, "message": message, "applied_tags": applied},
        )
        made["thread"] = str(data["id"])
        print("Published the post " + title)
        if picture is not None:
            await bot._http.send_message(made["thread"], files=[picture.to_dict()])
    except Exception:
        print("Post not published. Use a forum or media channel, and check the bot may post there and the tags are right.")
''',
    },
    "forum_tag_add": {
        "imports": (),
        "needs": ("as_id",),
        "source": '''async def forum_tag_add(channel_id, name):
    """Add a tag to a forum or media channel. A problem is printed, never a crash."""
    name = str(name).strip()
    if not as_id(channel_id) or not name:
        print("Tag not added: it needs a forum channel and a name.")
        return
    try:
        await bot._http.request(
            bot._http._route("POST", "/channels/{channel_id}/tags", channel_id=str(as_id(channel_id))),
            json={"name": name},
        )
    except Exception:
        print("Tag not added. The name must be new, the channel a forum or media channel, and the bot needs Manage Channels.")
''',
    },
    "forum_tag_remove": {
        "imports": (),
        "needs": ("as_id",),
        "source": '''async def forum_tag_remove(channel_id, name):
    """Remove a tag from a forum or media channel by its name. A problem is printed, never a crash."""
    if not as_id(channel_id) or not str(name).strip():
        print("Tag not removed: it needs a forum channel and a name.")
        return
    try:
        channel = await bot._http.request(
            bot._http._route("GET", "/channels/{channel_id}", channel_id=str(as_id(channel_id)))
        )
        for tag in channel["available_tags"]:
            if str(tag["name"]).lower() == str(name).strip().lower():
                await bot._http.request(
                    bot._http._route(
                        "DELETE",
                        "/channels/{channel_id}/tags/{tag_id}",
                        channel_id=str(as_id(channel_id)),
                        tag_id=str(as_id(tag["id"])),
                    )
                )
                return
        print("There is no tag called " + str(name) + " in this channel.")
    except Exception:
        print("Tag not removed. Check the channel ID and that the bot has Manage Channels.")
''',
    },
}

#: Written after the other helpers, in this order (a helper's own ``needs`` are written first anyway).
ORDER = [
    "made",
    "make_channel",
    "remove_channel",
    "make_role",
    "remove_role",
    "make_thread",
    "thread_from_message",
    "send_in_thread",
    "change_thread",
    "forum_tags",
    "forum_post",
    "forum_tag_add",
    "forum_tag_remove",
]
