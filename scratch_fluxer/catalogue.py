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

# --------------------------------------------------------------------------------------------- categories

CATEGORY_ORDER = ["events", "bot", "messages", "info", "control", "operators", "variables", "lists", "data"]

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
EMBED_COLORS = [
    ["blue", "blue", 0x176DFF],
    ["green", "green", 0x52C987],
    ["yellow", "yellow", 0xF5B335],
    ["orange", "orange", 0xF59A4D],
    ["red", "red", 0xF2735A],
    ["pink", "pink", 0xEE6A93],
    ["purple", "purple", 0xA98BFF],
    ["teal", "teal", 0x2EC4B0],
    ["grey", "grey", 0x8B8998],
]
EMBED_COLOR_OPTIONS = [[label, value] for label, value, _hex in EMBED_COLORS]
MATH_OPTIONS = [["+", "add"], ["−", "subtract"], ["×", "multiply"], ["÷", "divide"], ["mod", "mod"]]
COMPARE_OPTIONS = [["=", "eq"], ["≠", "ne"], ["<", "lt"], [">", "gt"], ["≤", "le"], ["≥", "ge"]]
LOGIC_OPTIONS = [["and", "and"], ["or", "or"]]
TEXT_CASE_OPTIONS = [["uppercase", "upper"], ["lowercase", "lower"], ["title case", "title"], ["trimmed", "strip"]]
ROUND_OPTIONS = [["round", "round"], ["round down", "floor"], ["round up", "ceil"], ["absolute value", "abs"]]

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
        tooltip="Some text.",
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
        ["set custom status to", text("TEXT", "Learning blocks")],
        tooltip="Shows a short text under your bot's name. Leave it empty to clear it.",
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
        ["color", menu("COLOR", EMBED_COLOR_OPTIONS)],
        tooltip="Sends a card with a title, some text and a coloured edge to this channel.",
        needs=("message",),
    ),
    block(
        "sff_reply_embed",
        "messages",
        "statement",
        ["reply with embed", text("TITLE", "Rules"), "with text", text("TEXT", "Be kind.")],
        ["color", menu("COLOR", EMBED_COLOR_OPTIONS)],
        tooltip="Replies with a card: a title, some text and a coloured edge.",
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
    # ---- Message info
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
        needs=("message", "member"),
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
]

BLOCKS: dict[str, dict[str, Any]] = {spec["type"]: spec for spec in _SPECS}
assert len(BLOCKS) == len(_SPECS), "two blocks share a type"

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


def browser_payload() -> dict[str, Any]:
    """Everything the editor needs to know about the blocks. Contains no secrets and is the same for everyone."""
    return {
        "ink": INK,
        "categories": [{"id": key, **CATEGORIES[key], "button": FLYOUT_BUTTONS.get(key)} for key in CATEGORY_ORDER],
        "blocks": [blockly_definition(spec) for spec in _SPECS],
        "toolbox": {key: [toolbox_entry(type_) for type_ in TOOLBOX[key]] for key in CATEGORY_ORDER},
        "hats": HATS,
        "locked": list(LOCKED_TYPES),
    }


# --------------------------------------------------------------------------------------------- the landing page


_PART_KEYS = ("input", "field", "number", "menu", "var", "asset", "statements")


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
