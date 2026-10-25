"""The block catalogue: every block the editor offers, described once.

This module is only about *what a block looks like and where it may go*. The compiler (``compiler.py``) says what
Python each block writes, and a test checks that the two files cover exactly the same block types.

One spec per block feeds three places, so they cannot drift apart:

* the editor, which gets the Blockly definitions and the toolbox built from these specs (``browser_payload``),
* the compiler, which uses ``needs`` / ``returns`` / the part names to check a workspace before writing Python,
* the landing page, whose example blocks are looked up here (``example``).

A spec is a plain dict:

``type``      the Blockly block type (always starts with ``sff_``)
``category``  one of ``CATEGORY_ORDER`` (``None`` for the two locked blocks that start and end every bot)
``shape``     ``hat`` | ``statement`` | ``cap`` (no block may follow) | ``value`` | ``boolean`` | ``literal`` | ``config``
``lines``     the rows of the block; each row is a list of parts (see below)
``tooltip``   one sentence shown on hover
``needs``     where the block may be used: ``("message",)``, ``("command",)``, ``("member",)`` or a mix. ``None`` = anywhere
``returns``   for value blocks: ``text`` | ``number`` | ``boolean`` | ``list`` | ``any``

A *part* is a plain string (a label) or one of the dicts built by the helpers below.
"""

from __future__ import annotations

import copy
from typing import Any

from . import embeds
from .embeds import EMBED_COLOR_OPTIONS, EMBED_COLORS  # noqa: F401 - the colours live in embeds.py; kept here for callers

# --------------------------------------------------------------------------------------------- categories

CATEGORY_ORDER = [
    "events",
    "bot",
    "messages",
    "info",
    "community",
    "threads",
    "control",
    "operators",
    "variables",
    "lists",
    "data",
]

#: The colours match the ``--cat-*`` tokens in public/static/css/app.css (a test keeps them in step).
CATEGORIES: dict[str, dict[str, str]] = {
    "events": {
        "name": "Events",
        "icon": "lightning",
        "color": "#f5b335",
        "blurb": "Every script starts with an event: something happens in Fluxer and your blocks run.",
    },
    "bot": {
        "name": "Bot",
        "icon": "robot",
        "color": "#5b9bff",
        "blurb": "Control the bot itself: its status, its custom status and what it writes to the console.",
    },
    "messages": {
        "name": "Messages",
        "icon": "chat-circle-text",
        "color": "#a98bff",
        "blurb": "Talk back: reply to a message, send to a channel, or post an embed.",
    },
    "info": {
        "name": "Message info",
        "icon": "info",
        "color": "#4cc3e0",
        "blurb": "Read what was said, who said it and where.",
    },
    "community": {
        "name": "Channels and roles",
        "icon": "hash",
        "color": "#8fd14f",
        "blurb": "Build your community: make and delete channels (text, voice, announcement, forum, media, link) and roles.",
    },
    "threads": {
        "name": "Threads and forums",
        "icon": "chats",
        "color": "#d4a373",
        "blurb": "Start threads, write inside them, and publish posts (with a picture and tags) in forum and media channels.",
    },
    "control": {
        "name": "Control",
        "icon": "git-branch",
        "color": "#f59a4d",
        "blurb": "Decide and repeat: if / else, loops and waiting.",
    },
    "operators": {
        "name": "Operators",
        "icon": "plus-minus",
        "color": "#52c987",
        "blurb": "Join text, compare values and do maths.",
    },
    "variables": {
        "name": "Variables",
        "icon": "database",
        "color": "#f2735a",
        "blurb": "Give a value a name, then use it or change it later.",
    },
    "lists": {
        "name": "Lists",
        "icon": "list-bullets",
        "color": "#ee6a93",
        "blurb": "Keep many values together and loop over them.",
    },
    "data": {
        "name": "JSON and assets",
        "icon": "file-code",
        "color": "#2ec4b0",
        "blurb": "Use the files you uploaded: read a JSON file, or send an image.",
    },
}

#: Dark text on the bright block colours (the same token the CSS uses).
INK = "#15121c"

# --------------------------------------------------------------------------------------------- menus

STATUS_OPTIONS = [["online", "online"], ["idle", "idle"], ["do not disturb", "dnd"], ["invisible", "invisible"]]
LOG_LEVEL_OPTIONS = [["info", "info"], ["warning", "warning"], ["error", "error"]]
MATH_OPTIONS = [["+", "add"], ["−", "subtract"], ["×", "multiply"], ["÷", "divide"], ["mod", "mod"]]
COMPARE_OPTIONS = [["=", "eq"], ["≠", "ne"], ["<", "lt"], [">", "gt"], ["≤", "le"], ["≥", "ge"]]
LOGIC_OPTIONS = [["and", "and"], ["or", "or"]]
TEXT_CASE_OPTIONS = [["uppercase", "upper"], ["lowercase", "lower"], ["title case", "title"], ["trimmed", "strip"]]
ROUND_OPTIONS = [["round", "round"], ["round down", "floor"], ["round up", "ceil"], ["absolute value", "abs"]]

#: The kinds of channel a bot can make. The value is the number Fluxer uses for that kind of channel.
CHANNEL_KIND_OPTIONS = [
    ["text", "0"],
    ["voice", "2"],
    ["announcement", "5"],
    ["forum", "15"],
    ["media", "16"],
    ["link", "998"],
]
THREAD_KIND_OPTIONS = [["public", "public"], ["private", "private"]]
THREAD_CHANGE_OPTIONS = [["close", "archive"], ["reopen", "unarchive"], ["lock", "lock"], ["unlock", "unlock"]]
YES_NO_OPTIONS = [["no", "False"], ["yes", "True"]]
#: ``[label, rgb number]``: "no colour" first, then the same colours the card pop-up offers.
ROLE_COLOR_OPTIONS = [["no colour", "0"], *[[label, str(rgb)] for label, _name, rgb in EMBED_COLORS]]

#: File kinds an asset can have (see routes/assets.py). Blocks name the kinds they accept.
ASSET_KINDS = ("image", "json", "text")

# --------------------------------------------------------------------------------------------- part helpers


def text(name: str, default: str = "") -> dict[str, Any]:
    """A socket that takes any value (shown as text); an editable text box sits in it until a block replaces it."""
    return {"input": name, "type": "text", "default": default}


def number(name: str, default: int | float = 0) -> dict[str, Any]:
    return {"input": name, "type": "number", "default": default}


def boolean(name: str) -> dict[str, Any]:
    return {"input": name, "type": "boolean"}


def anything(name: str, default: str = "") -> dict[str, Any]:
    return {"input": name, "type": "any", "default": default}


def items(name: str) -> dict[str, Any]:
    """A socket for a list (a list variable, or a block that makes a list)."""
    return {"input": name, "type": "list"}


def field(name: str, default: str = "", *, max_length: int = 60) -> dict[str, Any]:
    """Text typed straight into the block (no socket): command names, emoji, the prefix."""
    return {"field": name, "default": default, "max": max_length}


def menu(name: str, options: list[list[str]], default: str | None = None) -> dict[str, Any]:
    return {"menu": name, "options": options, "default": default if default is not None else options[0][1]}


def variable(name: str, default: str, *, kinds: tuple[str, ...] = ("",)) -> dict[str, Any]:
    """A dropdown of the bot's variables. ``kinds`` is ``("",)`` for plain variables, ``("List",)`` for lists."""
    return {"var": name, "default": default, "kinds": list(kinds)}


def asset(name: str, kinds: tuple[str, ...], default: str = "") -> dict[str, Any]:
    """A dropdown of the files the person uploaded, limited to the kinds this block can use."""
    return {"asset": name, "kinds": list(kinds), "default": default}


def embed_options(name: str = "OPTIONS", *, files: bool = True) -> dict[str, Any]:
    """The button on an embed block that opens the pop-up: colour, link, pictures, author, footer and time.

    The value is a small piece of JSON (see ``embeds.py``). ``files`` says whether the block can send a file of its own
    (a webhook can't, so its pictures must be web addresses).
    """
    return {"embed": name, "default": "{}", "max": embeds.MAX_JSON, "files": files}


def do(name: str = "DO") -> dict[str, Any]:
    """A slot that holds a stack of blocks (the inside of an if or a loop)."""
    return {"statements": name}


def block(
    type_: str,
    category: str | None,
    shape: str,
    *lines: list[Any],
    tooltip: str,
    needs: tuple[str, ...] | None = None,
    returns: str | None = None,
) -> dict[str, Any]:
    return {
        "type": type_,
        "category": category,
        "shape": shape,
        "lines": [list(line) for line in lines],
        "tooltip": tooltip,
        "needs": needs,
        "returns": returns,
    }


# --------------------------------------------------------------------------------------------- the blocks

_SPECS: list[dict[str, Any]] = [
    # ---- the two locked blocks: every bot has exactly one of each
    block(
        "sff_bot_setup",
        None,
        "config",
        ["create the bot with command prefix", field("PREFIX", "!", max_length=8)],
        tooltip="Creates your bot. People start a command with this prefix, like !ping.",
    ),
    block(
        "sff_bot_login",
        None,
        "config",
        ["log in with the saved bot token"],
        tooltip=(
            "Connects your bot to Fluxer with the token you saved. The token stays encrypted on the server and "
            "no block can read it."
        ),
    ),
    # ---- literals (they sit inside the sockets as editable boxes)
    block(
        "sff_text",
        None,
        "literal",
        [field("TEXT", "", max_length=2000)],
        tooltip=(
            "Some text. Write a codename like $authorName or $varCoins in it to put that value inside the text. "
            "Write $$ for a plain $."
        ),
        returns="text",
    ),
    block(
        "sff_number",
        None,
        "literal",
        [{"number": "NUM", "default": 0}],
        tooltip="A number.",
        returns="number",
    ),
    block(
        "sff_boolean",
        "operators",
        "boolean",
        [menu("BOOL", [["true", "true"], ["false", "false"]])],
        tooltip="True or false.",
        returns="boolean",
    ),
    # ---- Events
    block(
        "sff_event_ready",
        "events",
        "hat",
        ["when the bot is ready"],
        tooltip="Runs once, when your bot has connected to Fluxer.",
    ),
    block(
        "sff_event_message",
        "events",
        "hat",
        ["when a message is received"],
        tooltip=(
            "Runs for every message people send. Messages from bots, including yours, are ignored so your bot "
            "can't answer itself forever."
        ),
    ),
    block(
        "sff_event_command",
        "events",
        "hat",
        ["when the command", field("NAME", "ping", max_length=24), "is used"],
        tooltip="Runs when a message starts with your prefix and this name, for example !ping.",
    ),
    block(
        "sff_event_member_join",
        "events",
        "hat",
        ["when someone joins a community"],
        tooltip="Runs when a person joins a community your bot is in (called a guild in fluxer.py).",
    ),
    block(
        "sff_event_member_leave",
        "events",
        "hat",
        ["when someone leaves a community"],
        tooltip="Runs when a person leaves a community your bot is in.",
    ),
    block(
        "sff_event_bot_join",
        "events",
        "hat",
        ["when the bot joins a community"],
        tooltip=(
            "Runs when your bot is added to a community (for example, someone uses its invite link). Use "
            "“community name” and “community ID” inside it."
        ),
    ),
    block(
        "sff_event_reaction_add",
        "events",
        "hat",
        [
            "when someone adds the reaction",
            field("EMOJI", "✅", max_length=40),
            "to message",
            field("MESSAGE", "", max_length=30),
        ],
        tooltip=(
            "Runs when a person reacts. Paste the message's ID (leave it empty for any message) and type the emoji "
            "(leave it empty for any emoji). Reactions from your own bot are ignored."
        ),
    ),
    block(
        "sff_event_reaction_remove",
        "events",
        "hat",
        [
            "when someone removes the reaction",
            field("EMOJI", "✅", max_length=40),
            "from message",
            field("MESSAGE", "", max_length=30),
        ],
        tooltip=(
            "Runs when a person takes their reaction back. Use the same message ID and emoji as the block that gave "
            "the role, then take the role away here."
        ),
    ),
    # ---- Bot
    block(
        "sff_set_status",
        "bot",
        "statement",
        ["set status to", menu("STATUS", STATUS_OPTIONS)],
        tooltip="Changes the dot next to your bot's name: online, idle, do not disturb or invisible.",
    ),
    block(
        "sff_set_custom_status",
        "bot",
        "statement",
        ["set custom status to", text("TEXT", "Learning blocks"), "with emoji", field("EMOJI", "", max_length=80)],
        tooltip=(
            "Shows a short text, and an emoji if you want one, next to your bot's name. For the emoji, type or paste "
            "any normal emoji, or paste a community emoji written like <:name:123456789>. Leave both empty to clear it."
        ),
    ),
    block(
        "sff_console_print",
        "bot",
        "statement",
        ["write", text("TEXT", "Bot is online!"), "to the console"],
        tooltip="Writes a line to the console. Handy to see what your bot is doing.",
    ),
    block(
        "sff_console_log",
        "bot",
        "statement",
        ["write", text("TEXT", "Something happened"), "to the console as", menu("LEVEL", LOG_LEVEL_OPTIONS)],
        tooltip="Writes a line to the console as an info, a warning or an error, so you can filter for it.",
    ),
    block(
        "sff_bot_name",
        "bot",
        "value",
        ["the bot's name"],
        tooltip="Your bot's own username.",
        returns="text",
    ),
    block(
        "sff_bot_ping",
        "bot",
        "value",
        ["bot ping (ms)"],
        tooltip=("How many milliseconds Fluxer takes to answer your bot. It is measured each time the block runs."),
        returns="number",
    ),
    block(
        "sff_bot_uptime_seconds",
        "bot",
        "value",
        ["bot uptime (seconds)"],
        tooltip=("How many seconds your bot has been running since it started."),
        returns="number",
    ),
    block(
        "sff_bot_uptime_text",
        "bot",
        "value",
        ["bot uptime"],
        tooltip=("How long your bot has been running, written like 1d 2h 3m 4s."),
        returns="text",
    ),
    block(
        "sff_bot_community_count",
        "bot",
        "value",
        ["number of communities"],
        tooltip="How many communities your bot is in right now.",
        returns="number",
    ),
    # ---- Messages
    block(
        "sff_reply",
        "messages",
        "statement",
        ["reply with", text("TEXT", "Pong!")],
        tooltip="Replies to the message that started this script. Fluxer shows it as a reply.",
        needs=("message",),
    ),
    block(
        "sff_send_here",
        "messages",
        "statement",
        ["send", text("TEXT", "Hello!"), "to this channel"],
        tooltip="Sends a new message in the channel where the message was written.",
        needs=("message",),
    ),
    block(
        "sff_send_channel",
        "messages",
        "statement",
        ["send", text("TEXT", "Hello!"), "to channel", text("CHANNEL", "")],
        tooltip="Sends a message to any channel your bot can see. Paste the channel's ID.",
    ),
    block(
        "sff_send_embed",
        "messages",
        "statement",
        ["send embed", text("TITLE", "Rules"), "with text", text("TEXT", "Be kind.")],
        [embed_options()],
        tooltip=(
            "Sends a card (an embed) to this channel. Press “Card options” to change its colour, add a link, a picture, "
            "a small picture (thumbnail), an author, a footer or the time."
        ),
        needs=("message",),
    ),
    block(
        "sff_reply_embed",
        "messages",
        "statement",
        ["reply with embed", text("TITLE", "Rules"), "with text", text("TEXT", "Be kind.")],
        [embed_options()],
        tooltip=(
            "Replies with a card (an embed). Press “Card options” to change its colour, add a link, a picture, "
            "a small picture (thumbnail), an author, a footer or the time."
        ),
        needs=("message",),
    ),
    block(
        "sff_react",
        "messages",
        "statement",
        ["react to this message with", field("EMOJI", "👍", max_length=16)],
        tooltip="Adds an emoji reaction to the message. Type or paste any emoji.",
        needs=("message",),
    ),
    block(
        "sff_delete_message",
        "messages",
        "statement",
        ["delete this message"],
        tooltip="Deletes the message. Your bot needs permission to manage messages.",
        needs=("message",),
    ),
    block(
        "sff_dm_author",
        "messages",
        "statement",
        ["send", text("TEXT", "Hi!"), "to the author in a private message"],
        tooltip="Opens a private chat with the person who wrote the message and sends this text.",
        needs=("message",),
    ),
    block(
        "sff_dm_member",
        "messages",
        "statement",
        ["send", text("TEXT", "Welcome!"), "to the member in a private message"],
        tooltip="Sends a private message to the person who just joined or left.",
        needs=("member",),
    ),
    block(
        "sff_give_role",
        "messages",
        "statement",
        ["give the member the role", text("ROLE", "")],
        tooltip=(
            "Gives the person who just joined a role. Paste the role's ID. Your bot needs the Manage Roles "
            "permission, and its own role must be above the role it gives."
        ),
        needs=("member",),
    ),
    block(
        "sff_give_role_to",
        "messages",
        "statement",
        ["give the role", text("ROLE", ""), "to the person", text("USER", "")],
        tooltip=(
            "Gives a role to a person. Paste the role's ID and the person's ID (or a mention). Your bot needs the Manage Roles permission, and its own role must be above the role it gives."
        ),
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_take_role_from",
        "messages",
        "statement",
        ["take the role", text("ROLE", ""), "from the person", text("USER", "")],
        tooltip=(
            "Removes a role from a person. Paste the role's ID and the person's ID (or a mention). Your bot needs the Manage Roles permission, and its own role must be above that role."
        ),
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_reaction_give_role",
        "messages",
        "statement",
        ["give the person who reacted the role", text("ROLE", "")],
        tooltip=(
            "Gives the role to whoever added the reaction. Paste the role's ID. Your bot needs the Manage Roles permission, and its own role must be above the role it gives."
        ),
        needs=("reaction",),
    ),
    block(
        "sff_reaction_take_role",
        "messages",
        "statement",
        ["take the role", text("ROLE", ""), "from the person who reacted"],
        tooltip=(
            "Removes the role from whoever took their reaction back. Paste the same role ID that the adding script gives."
        ),
        needs=("reaction",),
    ),
    block(
        "sff_timeout_member",
        "messages",
        "statement",
        [
            "mute (time out) the person",
            text("USER", ""),
            "for",
            number("MINUTES", 10),
            "minutes, reason",
            text("REASON", ""),
        ],
        tooltip=(
            "Stops a person from talking for some minutes (1 to 40320, which is 28 days). Paste their ID or mention them. Needs the Moderate Members permission and a role above theirs."
        ),
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_untimeout_member",
        "messages",
        "statement",
        ["unmute the person", text("USER", ""), "reason", text("REASON", "")],
        tooltip=("Ends a mute early. Needs the Moderate Members permission."),
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_set_nickname",
        "messages",
        "statement",
        ["change the nickname of the person", text("USER", ""), "to", text("NICK", ""), "reason", text("REASON", "")],
        tooltip=(
            "Sets a person's nickname in this community (at most 32 letters). Leave the nickname empty to remove it. Paste their ID or mention them. Needs the Manage Nicknames permission and a role above theirs."
        ),
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_kick_member",
        "messages",
        "statement",
        ["kick the person", text("USER", ""), "reason", text("REASON", "")],
        tooltip=(
            "Removes a person from the community. They can join again with an invite. Needs the Kick Members permission and a role above theirs."
        ),
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_ban_member",
        "messages",
        "statement",
        ["ban the person", text("USER", ""), "reason", text("REASON", "")],
        tooltip=(
            "Removes a person and stops them from joining again until you unban them. Needs the Ban Members permission and a role above theirs."
        ),
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_unban_member",
        "messages",
        "statement",
        ["unban the person", text("USER", ""), "reason", text("REASON", "")],
        tooltip=("Lets a banned person join again. Paste their ID. Needs the Ban Members permission."),
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_send_here_save_id",
        "messages",
        "statement",
        ["send", text("TEXT", "Loading..."), "here and save the message ID in", variable("VAR", "message id")],
        tooltip=(
            "Sends a message to this channel and keeps its ID in a variable, so you can edit or delete that message later."
        ),
        needs=("message",),
    ),
    block(
        "sff_send_channel_save_id",
        "messages",
        "statement",
        ["send", text("TEXT", "Loading..."), "to channel", text("CHANNEL", "")],
        ["and save the message ID in", variable("VAR", "message id")],
        tooltip=(
            "Sends a message to a channel and keeps its ID in a variable, so you can edit or delete that message later."
        ),
    ),
    block(
        "sff_edit_message",
        "messages",
        "statement",
        ["edit message", text("ID", ""), "in channel", text("CHANNEL", "")],
        ["to say", text("TEXT", "Done!")],
        tooltip=(
            "Changes the text of one of your bot's own messages (a bot can't edit other people's messages). Paste the message ID and the channel ID, or use the variable you saved the ID in."
        ),
    ),
    block(
        "sff_delete_message_by_id",
        "messages",
        "statement",
        ["delete message", text("ID", ""), "in channel", text("CHANNEL", "")],
        tooltip=("Deletes a message by its ID. Deleting other people's messages needs the Manage Messages permission."),
    ),
    block(
        "sff_typing_here",
        "messages",
        "statement",
        ["show that the bot is typing here"],
        tooltip=(
            "Shows \u201cthe bot is typing\u2026\u201d in this channel for a few seconds. Use it before a wait to make replies feel natural."
        ),
        needs=("message",),
    ),
    block(
        "sff_typing_channel",
        "messages",
        "statement",
        ["show that the bot is typing in channel", text("CHANNEL", "")],
        tooltip=("Shows \u201cthe bot is typing\u2026\u201d in a channel for a few seconds. Paste the channel's ID."),
    ),
    block(
        "sff_webhook_send",
        "messages",
        "statement",
        ["send", text("TEXT", "Hello!"), "using the webhook", text("URL", "")],
        ["as the name", text("NAME", "")],
        tooltip=(
            "Posts a message through a webhook. Paste the webhook URL from the channel settings. Leave the name empty to use the webhook's own. Keep the URL private: anyone who has it can post."
        ),
    ),
    block(
        "sff_webhook_embed",
        "messages",
        "statement",
        ["send embed", text("TITLE", "News"), "with text", text("TEXT", "Something happened.")],
        [embed_options(files=False)],
        ["using the webhook", text("URL", ""), "as the name", text("NAME", "")],
        tooltip=(
            "Posts a card (an embed) through a webhook. Press “Card options” for the colour, a link, pictures (web "
            "addresses only), an author, a footer or the time. Paste the webhook URL from the channel settings. Leave "
            "the name empty to use the webhook's own. Keep the URL private."
        ),
    ),
    # ---- Message info
    block(
        "sff_reactor_id",
        "info",
        "value",
        ["ID of the person who reacted"],
        tooltip="The ID of the person who added or removed the reaction.",
        needs=("reaction",),
        returns="text",
    ),
    block(
        "sff_first_mention_id",
        "info",
        "value",
        ["ID of the first person mentioned"],
        tooltip="The ID of the first person mentioned in the message (like !kick @someone), or empty if nobody was.",
        needs=("message",),
        returns="text",
    ),
    block(
        "sff_author_has_role",
        "info",
        "boolean",
        ["author has the role", text("ROLE", "")],
        tooltip="True if the person who wrote the message has this role. Paste the role's ID. Use it so only staff can run moderation commands.",
        needs=("message",),
        returns="boolean",
    ),
    block(
        "sff_msg_text",
        "info",
        "value",
        ["message text"],
        tooltip="What the message says, exactly as it was written (a command message includes the prefix).",
        needs=("message",),
        returns="text",
    ),
    block(
        "sff_command_text",
        "info",
        "value",
        ["text after the command"],
        tooltip="Everything written after the command. For !say hello it is: hello",
        needs=("command",),
        returns="text",
    ),
    block(
        "sff_msg_author_name",
        "info",
        "value",
        ["author name"],
        tooltip="The display name of the person who wrote the message.",
        needs=("message",),
        returns="text",
    ),
    block(
        "sff_msg_author_mention",
        "info",
        "value",
        ["author mention"],
        tooltip="Text that mentions the author, so they get a notification.",
        needs=("message",),
        returns="text",
    ),
    block(
        "sff_msg_author_id",
        "info",
        "value",
        ["author ID"],
        tooltip="The author's ID. IDs are long numbers, so they are kept as text.",
        needs=("message",),
        returns="text",
    ),
    block(
        "sff_msg_channel_id",
        "info",
        "value",
        ["channel ID"],
        tooltip="The ID of the channel the message was written in.",
        needs=("message",),
        returns="text",
    ),
    block(
        "sff_msg_id",
        "info",
        "value",
        ["message ID"],
        tooltip="The ID of the message.",
        needs=("message",),
        returns="text",
    ),
    block(
        "sff_community_id",
        "info",
        "value",
        ["community ID"],
        tooltip="The ID of the community. It is empty in private messages.",
        needs=("message", "member", "community"),
        returns="text",
    ),
    block(
        "sff_community_name",
        "info",
        "value",
        ["community name"],
        tooltip="The name of the community the bot just joined. Works inside “when the bot joins a community”.",
        needs=("community",),
        returns="text",
    ),
    block(
        "sff_msg_author_is_bot",
        "info",
        "boolean",
        ["author is a bot"],
        tooltip="True if the message was written by a bot.",
        needs=("message",),
        returns="boolean",
    ),
    block(
        "sff_member_name",
        "info",
        "value",
        ["member name"],
        tooltip="The display name of the person who joined or left.",
        needs=("member",),
        returns="text",
    ),
    block(
        "sff_member_mention",
        "info",
        "value",
        ["member mention"],
        tooltip="Text that mentions the member, so they get a notification.",
        needs=("member",),
        returns="text",
    ),
    block(
        "sff_member_id",
        "info",
        "value",
        ["member ID"],
        tooltip="The member's ID. IDs are long numbers, so they are kept as text.",
        needs=("member",),
        returns="text",
    ),
    # ---- Control
    block(
        "sff_if",
        "control",
        "statement",
        ["if", boolean("COND"), "then"],
        [do("DO")],
        tooltip="Runs the blocks inside only when the condition is true.",
    ),
    block(
        "sff_if_else",
        "control",
        "statement",
        ["if", boolean("COND"), "then"],
        [do("DO")],
        ["else"],
        [do("ELSE")],
        tooltip="Runs the first stack when the condition is true, otherwise the second.",
    ),
    block(
        "sff_repeat",
        "control",
        "statement",
        ["repeat", number("TIMES", 10), "times"],
        [do("DO")],
        tooltip="Runs the blocks inside again and again, this many times.",
    ),
    block(
        "sff_repeat_until",
        "control",
        "statement",
        ["repeat until", boolean("COND")],
        [do("DO")],
        tooltip="Keeps running the blocks inside until the condition becomes true.",
    ),
    block(
        "sff_forever",
        "control",
        "cap",
        ["forever"],
        [do("DO")],
        tooltip="Runs the blocks inside again and again. Put a wait inside so it doesn't spin too fast.",
    ),
    block(
        "sff_wait",
        "control",
        "statement",
        ["wait", number("SECONDS", 1), "seconds"],
        tooltip="Pauses this script. Your bot keeps answering other things while it waits.",
    ),
    block(
        "sff_stop_script",
        "control",
        "cap",
        ["stop this script"],
        tooltip="Ends this script here. Other scripts keep running.",
    ),
    # ---- Operators
    block(
        "sff_join",
        "operators",
        "value",
        ["join", text("A", "Hello "), text("B", "world")],
        tooltip="Sticks two pieces of text together. Numbers and other values are turned into text.",
        returns="text",
    ),
    block(
        "sff_math",
        "operators",
        "value",
        [number("A", 0), menu("OP", MATH_OPTIONS), number("B", 0)],
        tooltip=(
            "Adds, subtracts, multiplies or divides two numbers. ÷ always gives a decimal (10 ÷ 2 is 5.0). "
            "mod gives the remainder."
        ),
        returns="number",
    ),
    block(
        "sff_compare",
        "operators",
        "boolean",
        [anything("A", ""), menu("OP", COMPARE_OPTIONS), anything("B", "")],
        tooltip="Compares two values. Text is compared exactly: Hello and hello are different.",
        returns="boolean",
    ),
    block(
        "sff_logic",
        "operators",
        "boolean",
        [boolean("A"), menu("OP", LOGIC_OPTIONS), boolean("B")],
        tooltip="True when both (and) or either (or) of the two conditions are true.",
        returns="boolean",
    ),
    block(
        "sff_not",
        "operators",
        "boolean",
        ["not", boolean("A")],
        tooltip="Flips true to false and false to true.",
        returns="boolean",
    ),
    block(
        "sff_random",
        "operators",
        "value",
        ["pick random", number("A", 1), "to", number("B", 10)],
        tooltip="A whole number between the two numbers, both included.",
        returns="number",
    ),
    block(
        "sff_contains",
        "operators",
        "boolean",
        [text("A", "hello world"), "contains", text("B", "world")],
        tooltip="True if the first text has the second inside it. Capital letters don't matter.",
        returns="boolean",
    ),
    block(
        "sff_text_length",
        "operators",
        "value",
        ["length of", text("TEXT", "hello")],
        tooltip="How many characters the text has.",
        returns="number",
    ),
    block(
        "sff_text_case",
        "operators",
        "value",
        [menu("OP", TEXT_CASE_OPTIONS), "of", text("TEXT", "hello")],
        tooltip="Changes capital letters, or trims spaces from both ends.",
        returns="text",
    ),
    block(
        "sff_word",
        "operators",
        "value",
        ["word", number("N", 1), "of", text("TEXT", "hello world")],
        tooltip="The Nth word of some text. It is empty if there is no such word.",
        returns="text",
    ),
    block(
        "sff_replace",
        "operators",
        "value",
        ["replace", text("A", "a"), "with", text("B", "o"), "in", text("TEXT", "banana")],
        tooltip="Swaps every copy of the first text for the second.",
        returns="text",
    ),
    block(
        "sff_to_number",
        "operators",
        "value",
        ["number from", text("TEXT", "5")],
        tooltip="Turns text like 12 into a number. If it isn't a number you get 0.",
        returns="number",
    ),
    block(
        "sff_round",
        "operators",
        "value",
        [menu("OP", ROUND_OPTIONS), number("N", 0)],
        tooltip=(
            "Rounds a number to a whole number (a half goes to the nearest even number, so 2.5 becomes 2 and 3.5 "
            "becomes 4), rounds it down or up, or removes its minus sign."
        ),
        returns="number",
    ),
    # ---- Variables
    block(
        "sff_set_variable",
        "variables",
        "statement",
        ["set", variable("VAR", "score"), "to", anything("VALUE", "0")],
        tooltip="Gives a variable a new value.",
    ),
    block(
        "sff_change_variable",
        "variables",
        "statement",
        ["change", variable("VAR", "score"), "by", number("BY", 1)],
        tooltip="Adds a number to a variable. Use a negative number to subtract.",
    ),
    block(
        "sff_variable",
        "variables",
        "value",
        [variable("VAR", "score")],
        tooltip="The current value of a variable.",
        returns="any",
    ),
    # ---- Lists
    block(
        "sff_list_add",
        "lists",
        "statement",
        ["add", anything("ITEM", "apple"), "to", variable("LIST", "fruits", kinds=("List",))],
        tooltip="Puts a new item at the end of the list.",
    ),
    block(
        "sff_list_delete",
        "lists",
        "statement",
        ["delete item", number("N", 1), "of", variable("LIST", "fruits", kinds=("List",))],
        tooltip="Removes one item from the list. Counting starts at 1.",
    ),
    block(
        "sff_list_clear",
        "lists",
        "statement",
        ["delete all of", variable("LIST", "fruits", kinds=("List",))],
        tooltip="Empties the list.",
    ),
    block(
        "sff_list_set",
        "lists",
        "statement",
        ["set", variable("LIST", "fruits", kinds=("List",)), "to", items("VALUE")],
        tooltip="Replaces the whole list with another list, for example the words of some text.",
    ),
    block(
        "sff_list_item",
        "lists",
        "value",
        ["item", number("N", 1), "of", variable("LIST", "fruits", kinds=("List",))],
        tooltip="One item of the list. Counting starts at 1. It is empty if there is no such item.",
        returns="any",
    ),
    block(
        "sff_list_length",
        "lists",
        "value",
        ["length of", variable("LIST", "fruits", kinds=("List",))],
        tooltip="How many items the list has.",
        returns="number",
    ),
    block(
        "sff_list_contains",
        "lists",
        "boolean",
        [variable("LIST", "fruits", kinds=("List",)), "contains", anything("ITEM", "apple")],
        tooltip="True if the list has this item.",
        returns="boolean",
    ),
    block(
        "sff_list_random",
        "lists",
        "value",
        ["random item of", variable("LIST", "fruits", kinds=("List",))],
        tooltip="A random item from the list. It is empty if the list is empty.",
        returns="any",
    ),
    block(
        "sff_list_join",
        "lists",
        "value",
        ["join items of", variable("LIST", "fruits", kinds=("List",)), "with", text("SEP", ", ")],
        tooltip="Turns the list into one piece of text, with this between the items.",
        returns="text",
    ),
    block(
        "sff_list_split",
        "lists",
        "value",
        ["split", text("TEXT", "a,b,c"), "by", text("SEP", ",")],
        tooltip="Cuts text into a list at every separator. An empty separator cuts at spaces.",
        returns="list",
    ),
    block(
        "sff_list_variable",
        "lists",
        "value",
        [variable("LIST", "fruits", kinds=("List",))],
        tooltip="The list itself, to use in a loop or to copy.",
        returns="list",
    ),
    block(
        "sff_for_each",
        "lists",
        "statement",
        ["for each", variable("VAR", "item"), "in", items("LIST")],
        [do("DO")],
        tooltip="Runs the blocks inside once for every item. The variable holds the current item.",
    ),
    # ---- JSON and assets
    block(
        "sff_json_load",
        "data",
        "statement",
        [
            "load JSON file",
            asset("FILE", ("json",), "quotes.json"),
            "into",
            variable("VAR", "quotes", kinds=("", "List")),
        ],
        tooltip="Reads a JSON file you uploaded and keeps what is inside in a variable.",
    ),
    block(
        "sff_json_get",
        "data",
        "value",
        ["get", text("KEY", "title"), "from", variable("VAR", "quotes", kinds=("", "List"))],
        tooltip="Digs into JSON data. Use dots to go deeper, like users.0.name. Missing keys give empty text.",
        returns="any",
    ),
    block(
        "sff_file_text",
        "data",
        "value",
        ["text of file", asset("FILE", ("text", "json"), "notes.txt")],
        tooltip="All the text inside a text or JSON file you uploaded.",
        returns="text",
    ),
    block(
        "sff_reply_image",
        "data",
        "statement",
        ["reply with image", asset("FILE", ("image",), "cat.png")],
        tooltip="Replies with a picture you uploaded.",
        needs=("message",),
    ),
    block(
        "sff_send_image",
        "data",
        "statement",
        ["send image", asset("FILE", ("image",), "cat.png"), "to this channel"],
        tooltip="Sends a picture you uploaded to the channel where the message was written.",
        needs=("message",),
    ),
    block(
        "sff_reply_file",
        "data",
        "statement",
        ["reply with file", asset("FILE", ("image", "json", "text"), "notes.txt")],
        tooltip="Replies with a file you uploaded, as an attachment.",
        needs=("message",),
    ),
    # ---- channels and roles
    block(
        "sff_create_channel",
        "community",
        "statement",
        ["create a", menu("KIND", CHANNEL_KIND_OPTIONS), "channel named", text("NAME", "new-channel")],
        ["with topic (or the web address, for a link channel)", text("EXTRA", "")],
        ["inside the category with ID", text("PARENT", "")],
        tooltip=(
            "Makes a new channel. Fill in the topic for a text, voice, announcement, forum or media channel, or the web "
            "address (https://…) for a link channel. Leave the category empty to put it at the top. Needs the Manage "
            "Channels permission."
        ),
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_delete_channel",
        "community",
        "statement",
        ["delete the channel (or thread) with ID", text("ID", "")],
        tooltip=(
            "Deletes a channel for good, with everything in it. There is no undo. Paste the ID, or use the ID of the "
            "channel you just made. Needs the Manage Channels permission."
        ),
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_create_role",
        "community",
        "statement",
        ["create a role named", text("NAME", "Member"), "with colour", menu("COLOR", ROLE_COLOR_OPTIONS)],
        [
            "shown apart from others:",
            menu("HOIST", YES_NO_OPTIONS),
            "anyone can mention it:",
            menu("MENTION", YES_NO_OPTIONS),
        ],
        tooltip="Makes a new role (it starts with no special permissions). Needs the Manage Roles permission.",
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_delete_role",
        "community",
        "statement",
        ["delete the role with ID", text("ID", "")],
        tooltip="Deletes a role, so everyone loses it. Needs the Manage Roles permission, and the role must be below the bot's own role.",
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_new_channel_id",
        "community",
        "value",
        ["ID of the channel I just made"],
        tooltip="The ID of the last channel this bot made. Use it to delete it later or to send something in it.",
        returns="text",
    ),
    block(
        "sff_new_role_id",
        "community",
        "value",
        ["ID of the role I just made"],
        tooltip="The ID of the last role this bot made. Use it with “give the role … to the person …”.",
        returns="text",
    ),
    # ---- threads
    block(
        "sff_thread_start",
        "threads",
        "statement",
        ["start a", menu("KIND", THREAD_KIND_OPTIONS), "thread named", text("NAME", "Chat")],
        ["in the channel with ID (empty = this channel)", text("CHANNEL", "")],
        tooltip=(
            "Starts a thread in a text or announcement channel. A public thread can be seen by everyone who sees the "
            "channel; a private one only by the people in it. Needs permission to create threads."
        ),
        needs=("message", "reaction"),
    ),
    block(
        "sff_thread_from_message",
        "threads",
        "statement",
        ["start a thread named", text("NAME", "Discussion"), "from this message"],
        tooltip="Starts a public thread attached to the message that triggered the script.",
        needs=("message",),
    ),
    block(
        "sff_thread_send",
        "threads",
        "statement",
        ["send", text("TEXT", "Hello!"), "in the thread with ID", text("THREAD", "")],
        tooltip=(
            "Writes a message inside a thread. Use the ID of the thread you just made. To bring a person into a private "
            "thread, mention them in the message."
        ),
    ),
    block(
        "sff_thread_change",
        "threads",
        "statement",
        [menu("ACTION", THREAD_CHANGE_OPTIONS), "the thread with ID", text("THREAD", "")],
        tooltip="Closes (archives) or reopens a thread, or locks it so only moderators can write in it.",
    ),
    block(
        "sff_new_thread_id",
        "threads",
        "value",
        ["ID of the thread or post I just made"],
        tooltip="The ID of the last thread or forum post this bot made. A thread's ID works like a channel's ID.",
        returns="text",
    ),
    # ---- forum and media channels
    block(
        "sff_forum_post",
        "threads",
        "statement",
        ["post in the forum or media channel", text("CHANNEL", ""), "with title", text("TITLE", "Hello")],
        ["and text", text("TEXT", "First post!")],
        ["and tags (names, separated by commas)", text("TAGS", "")],
        tooltip=(
            "Publishes a post in a forum or media channel. Tags must already exist in that channel; write their names "
            "like Help, Bug. A wrong name is skipped and shown in the console."
        ),
    ),
    block(
        "sff_forum_post_picture",
        "threads",
        "statement",
        ["post in the forum or media channel", text("CHANNEL", ""), "with title", text("TITLE", "Hello")],
        ["and text", text("TEXT", "First post!"), "and the picture", asset("FILE", ("image",), "")],
        ["and tags (names, separated by commas)", text("TAGS", "")],
        tooltip=(
            "Publishes a post with a picture you uploaded on the Files tab. The post opens with your text, and the "
            "picture is added to it right after. Tags must already exist in the channel."
        ),
    ),
    block(
        "sff_forum_post_link",
        "threads",
        "statement",
        ["post in the forum or media channel", text("CHANNEL", ""), "with title", text("TITLE", "Hello")],
        ["and text", text("TEXT", "First post!"), "and the picture at", text("PICTURE", "https://")],
        ["and tags (names, separated by commas)", text("TAGS", "")],
        tooltip=(
            "Publishes a post with a picture from a web address (https://…). The picture is shown inside the first "
            "message. Tags must already exist in the channel."
        ),
    ),
    block(
        "sff_forum_tag_add",
        "threads",
        "statement",
        ["add the tag", text("NAME", "Help"), "to the forum or media channel", text("CHANNEL", "")],
        tooltip="Adds a new tag people can pick when they post (up to 20 per channel). Needs the Manage Channels permission.",
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_forum_tag_remove",
        "threads",
        "statement",
        ["remove the tag", text("NAME", "Help"), "from the forum or media channel", text("CHANNEL", "")],
        tooltip="Removes a tag from the channel by its name. Needs the Manage Channels permission.",
        needs=("message", "member", "reaction"),
    ),
    block(
        "sff_forum_tags",
        "threads",
        "value",
        ["tags of the forum or media channel", text("CHANNEL", "")],
        tooltip="A list with the names of the tags the channel has. Use it to check that a tag exists, or to show them.",
        returns="list",
    ),
]

BLOCKS: dict[str, dict[str, Any]] = {spec["type"]: spec for spec in _SPECS}
assert len(BLOCKS) == len(_SPECS), "two blocks share a type"

#: Codenames: a short word written as ``$word`` inside a text box that stands for a block with no sockets of its own
#: (``$authorID`` is the block "author ID"). ``[codename, block type, group, what it is]``; the order is the order the
#: guide and the pop-up list them in. A codename works only where its block would (``authorID`` needs a message).
CODENAME_LIST: list[tuple[str, str, str, str]] = [
    ("authorName", "sff_msg_author_name", "The message", "Name of the person who wrote the message"),
    ("authorMention", "sff_msg_author_mention", "The message", "A mention of that person (they get pinged)"),
    ("authorID", "sff_msg_author_id", "The message", "ID of that person"),
    ("messageText", "sff_msg_text", "The message", "What the message says"),
    ("messageID", "sff_msg_id", "The message", "ID of the message"),
    ("channelID", "sff_msg_channel_id", "The message", "ID of the channel it was written in"),
    ("mentionedID", "sff_first_mention_id", "The message", "ID of the first person mentioned in it"),
    ("commandText", "sff_command_text", "The command", "Everything written after the command"),
    ("communityID", "sff_community_id", "Community", "ID of the community (server)"),
    ("communityName", "sff_community_name", "Community", "Name of the community the bot just joined"),
    ("memberName", "sff_member_name", "Members and reactions", "Name of the member who joined"),
    ("memberMention", "sff_member_mention", "Members and reactions", "A mention of that member"),
    ("memberID", "sff_member_id", "Members and reactions", "ID of that member"),
    ("reactorID", "sff_reactor_id", "Members and reactions", "ID of the person who reacted"),
    ("newChannelID", "sff_new_channel_id", "Things the bot made", "ID of the channel the bot just made"),
    ("newRoleID", "sff_new_role_id", "Things the bot made", "ID of the role the bot just made"),
    ("newThreadID", "sff_new_thread_id", "Things the bot made", "ID of the thread or post the bot just made"),
    ("botName", "sff_bot_name", "The bot", "The bot's name"),
    ("botPing", "sff_bot_ping", "The bot", "Bot ping in milliseconds"),
    ("botUptime", "sff_bot_uptime_text", "The bot", "How long the bot has been running, as words"),
    ("botUptimeSeconds", "sff_bot_uptime_seconds", "The bot", "How long the bot has been running, in seconds"),
    ("communityCount", "sff_bot_community_count", "The bot", "How many communities the bot is in"),
]

#: Codenames for the person's own variables and lists: ``$varCoins``, ``$listShop``, ``$countShop``.
#: ``[prefix, what it does]``
VARIABLE_CODENAMES: list[tuple[str, str]] = [
    ("var", "The value of a variable"),
    ("list", "All the items of a list, separated by commas"),
    ("count", "How many items a list has"),
]

CODENAMES: dict[str, str] = {name.lower(): type_ for name, type_, _group, _what in CODENAME_LIST}
assert len(CODENAMES) == len(CODENAME_LIST), "two codenames are the same"
for _name, _type, _group, _what in CODENAME_LIST:
    assert _type in BLOCKS and not [p for line in BLOCKS[_type]["lines"] for p in line if not isinstance(p, str)], _name


def codenames_for_browser() -> dict[str, Any]:
    return {
        "blocks": [
            {"name": name, "group": group, "what": what, "needs": list(BLOCKS[type_]["needs"] or [])}
            for name, type_, group, what in CODENAME_LIST
        ],
        "variables": [{"prefix": prefix, "what": what} for prefix, what in VARIABLE_CODENAMES],
    }


#: Blocks that are in the toolbox, per category, in the order they are listed above.
TOOLBOX: dict[str, list[str]] = {
    category: [spec["type"] for spec in _SPECS if spec["category"] == category and spec["shape"] != "literal"]
    for category in CATEGORY_ORDER
}

HATS = [spec["type"] for spec in _SPECS if spec["shape"] == "hat"]
LOCKED_TYPES = ("sff_bot_setup", "sff_bot_login")

# --------------------------------------------------------------------------------------------- Blockly


def _is_input(part: Any) -> bool:
    return isinstance(part, dict) and "input" in part


def _check_for_input(kind: str) -> list[str] | None:
    return {"number": ["Number"], "boolean": ["Boolean"], "list": ["List"]}.get(kind)


def _output_check(returns: str | None) -> list[str] | None:
    return {"text": ["String"], "number": ["Number"], "boolean": ["Boolean"], "list": ["List"]}.get(returns or "")


def _escape_message(label: str) -> str:
    return label.replace("%", "%%")


def blockly_definition(spec: dict[str, Any]) -> dict[str, Any]:
    """The JSON the browser hands to ``Blockly.defineBlocksWithJsonArray`` for this spec."""
    definition: dict[str, Any] = {
        "type": spec["type"],
        "style": spec["category"] or ("literal" if spec["shape"] == "literal" else "config"),
        "tooltip": spec["tooltip"],
        "inputsInline": True,
    }
    shape = spec["shape"]
    if shape == "hat":
        definition["nextStatement"] = None
        definition["hat"] = "cap"
    elif shape == "statement":
        definition["previousStatement"] = None
        definition["nextStatement"] = None
    elif shape == "cap":
        definition["previousStatement"] = None
    elif shape in ("value", "boolean", "literal"):
        # "any" has no check, so it plugs into every socket (variables, list items, JSON values).
        definition["output"] = _output_check("boolean" if shape == "boolean" else spec["returns"])
    # "config" blocks have no connections at all.

    for index, line in enumerate(spec["lines"]):
        message: list[str] = []
        args: list[dict[str, Any]] = []
        for part in line:
            if isinstance(part, str):
                message.append(_escape_message(part))
                continue
            args.append(_blockly_arg(part))
            message.append(f"%{len(args)}")
        definition[f"message{index}"] = " ".join(message)
        if args:
            definition[f"args{index}"] = args
    return definition


def _blockly_arg(part: dict[str, Any]) -> dict[str, Any]:
    if "input" in part:
        arg: dict[str, Any] = {"type": "input_value", "name": part["input"]}
        check = _check_for_input(part["type"])
        if check:
            arg["check"] = check
        return arg
    if "statements" in part:
        return {"type": "input_statement", "name": part["statements"]}
    if "field" in part:
        return {
            "type": "field_input",
            "name": part["field"],
            "text": part["default"],
            "spellcheck": False,
            "maxLength": part["max"],
        }
    if "number" in part:
        return {"type": "field_number", "name": part["number"], "value": part["default"]}
    if "menu" in part:
        return {"type": "field_dropdown", "name": part["menu"], "options": part["options"]}
    if "var" in part:
        return {
            "type": "field_variable",
            "name": part["var"],
            "variable": part["default"],
            "variableTypes": part["kinds"],
            "defaultType": part["kinds"][0],
        }
    if "asset" in part:
        return {"type": "field_sff_asset", "name": part["asset"], "kinds": part["kinds"]}
    if "embed" in part:
        return {"type": "field_sff_embed", "name": part["embed"], "files": part["files"], "value": part["default"]}
    raise ValueError(f"unknown part: {part!r}")


def _shadow_for(part: dict[str, Any]) -> dict[str, Any] | None:
    """The editable box that sits in a socket until a block is dropped on it (like Scratch's white fields)."""
    if part["type"] == "number":
        return {"type": "sff_number", "fields": {"NUM": part["default"]}}
    if part["type"] in ("text", "any"):
        return {"type": "sff_text", "fields": {"TEXT": part["default"]}}
    return None


def _variable_part(spec: dict[str, Any]) -> dict[str, Any] | None:
    for line in spec["lines"]:
        for part in line:
            if isinstance(part, dict) and "var" in part:
                return part
    return None


def _is_getter(spec: dict[str, Any]) -> bool:
    """A reporter that is nothing but a variable's name: the flyout shows one per variable."""
    only_part = len(spec["lines"]) == 1 and len(spec["lines"][0]) == 1
    return spec["shape"] == "value" and only_part and _variable_part(spec) is not None


def toolbox_entry(type_: str) -> dict[str, Any]:
    """One block as it appears in a flyout: the block plus its default shadow boxes.

    Blocks with a variable dropdown carry a ``variable`` note. The editor fills it in with one of the person's own
    variables (or leaves the block out until they have one), so opening a flyout never invents variables.
    """
    spec = BLOCKS[type_]
    entry: dict[str, Any] = {"kind": "block", "type": type_}
    inputs: dict[str, Any] = {}
    for line in spec["lines"]:
        for part in line:
            if _is_input(part):
                shadow = _shadow_for(part)
                if shadow:
                    inputs[part["input"]] = {"shadow": copy.deepcopy(shadow)}
    if inputs:
        entry["inputs"] = inputs
    var = _variable_part(spec)
    if var:
        entry["variable"] = {"field": var["var"], "kinds": var["kinds"], "each": _is_getter(spec)}
    return entry


#: The button at the top of a flyout that makes the things its blocks use.
FLYOUT_BUTTONS = {
    "variables": {"label": "Make a variable", "kind": ""},
    "lists": {"label": "Make a list", "kind": "List"},
}


def search_index() -> list[dict[str, str]]:
    """What the editor's block search looks through: every block in a flyout, with its words, family and tooltip."""
    found = []
    for key in CATEGORY_ORDER:
        for type_ in TOOLBOX[key]:
            spec = BLOCKS[type_]
            words = [part if isinstance(part, str) else "…" for line in spec["lines"] for part in line]
            found.append({"type": type_, "family": key, "label": " ".join(words), "tip": spec["tooltip"]})
    return found


def browser_payload() -> dict[str, Any]:
    """Everything the editor needs to know about the blocks. Contains no secrets and is the same for everyone."""
    return {
        "ink": INK,
        "categories": [{"id": key, **CATEGORIES[key], "button": FLYOUT_BUTTONS.get(key)} for key in CATEGORY_ORDER],
        "blocks": [blockly_definition(spec) for spec in _SPECS],
        "toolbox": {key: [toolbox_entry(type_) for type_ in TOOLBOX[key]] for key in CATEGORY_ORDER},
        "hats": HATS,
        "locked": list(LOCKED_TYPES),
        "embed": embeds.browser_config(),
        "codenames": codenames_for_browser(),
        "search": search_index(),
    }


# --------------------------------------------------------------------------------------------- the landing page


_PART_KEYS = ("input", "field", "number", "menu", "var", "asset", "embed", "statements")


def _part_names(part: dict[str, Any]) -> list[str]:
    """The name a block's field, socket, menu, variable or file dropdown is called in the saved blocks."""
    return [part[key] for key in _PART_KEYS if key in part and isinstance(part[key], str)]


def example(type_: str, **values: str) -> dict[str, Any]:
    """The block as the landing page draws it: ``{"hat"|"round": True, "parts": [...]}``.

    Text and number sockets show their default (or the value passed here), dropdowns show the chosen option.
    """
    spec = BLOCKS[type_]
    known = {key for line in spec["lines"] for part in line if isinstance(part, dict) for key in _part_names(part)}
    if unknown := set(values) - known:
        raise ValueError(f"{type_} has no {', '.join(sorted(unknown))} (it has {', '.join(sorted(known))})")
    parts: list[Any] = []
    for line in spec["lines"]:
        for part in line:
            if isinstance(part, str):
                parts.append(part)
            elif "input" in part:
                if part["type"] in ("boolean", "list"):  # these sockets start empty: a condition or a list goes in them
                    parts.append(("field", values.get(part["input"], "…")))
                else:
                    parts.append(("field", str(values.get(part["input"], part["default"]))))
            elif "field" in part:
                parts.append(("field", values.get(part["field"], part["default"])))
            elif "number" in part:
                parts.append(("field", str(values.get(part["number"], part["default"]))))
            elif "menu" in part:
                chosen = values.get(part["menu"], part["default"])
                label = next((opt[0] for opt in part["options"] if opt[1] == chosen), chosen)
                parts.append(("menu", label))
            elif "var" in part or "asset" in part:
                key = part.get("var") or part["asset"]
                parts.append(("menu", values.get(key, part["default"])))
            elif "embed" in part:
                parts.append(("menu", "Card options"))
            # statement slots are drawn by the landing page as the block's body, not as parts
    out: dict[str, Any] = {"parts": parts}
    if spec["shape"] == "hat":
        out["hat"] = True
    if spec["shape"] in ("value", "boolean"):
        out["round"] = True
    return out


def flat_label(type_: str) -> str:
    """The block's words with sockets shown as ``_`` (used by tests and the code panel's tooltips)."""
    spec = BLOCKS[type_]
    words: list[str] = []
    for line in spec["lines"]:
        for part in line:
            words.append(part if isinstance(part, str) else "_")
    return " ".join(words)
