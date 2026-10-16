"""Copy that is shared between pages: the block palette shown on the landing page.

The block examples are the real blocks: each one is drawn from the block catalogue (``catalogue.example``), so the words,
the sockets and the starting values a visitor sees on the landing page are exactly what the editor gives them. Only the
choice of blocks, and the values typed into a few of them to make a better picture, are written here.
"""

from __future__ import annotations

from typing import Any

from . import catalogue

#: The starting values of the live demo on the landing page. The Python it shows is identical to what the real compiler
#: writes for the same blocks (tests/test_landing.py and tests/test_landing_browser.py compare the two).
DEMO = {"prefix": "!", "ready": "Bot is online!", "command": "ping", "reply": "Pong!"}

#: Three blocks to show for each family: ``(block type, values typed into it)``. Values are keyed by the names of the
#: block's fields and sockets in the catalogue; anything left out shows the block's own starting value.
EXAMPLES: dict[str, list[tuple[str, dict[str, str]]]] = {
    "events": [("sff_event_ready", {}), ("sff_event_message", {}), ("sff_event_command", {"NAME": "ping"})],
    "bot": [
        ("sff_set_custom_status", {"TEXT": "Learning blocks"}),
        ("sff_set_status", {}),
        ("sff_console_print", {"TEXT": "Bot is online!"}),
    ],
    "messages": [
        ("sff_reply", {"TEXT": "Pong!"}),
        ("sff_send_here", {"TEXT": "Hello!"}),
        ("sff_send_embed", {"TITLE": "Rules", "TEXT": "Be kind."}),
    ],
    "info": [("sff_msg_text", {}), ("sff_msg_author_name", {}), ("sff_msg_channel_id", {})],
    "community": [
        ("sff_create_channel", {"NAME": "news"}),
        ("sff_create_role", {"NAME": "Member"}),
        ("sff_delete_channel", {}),
    ],
    "threads": [
        ("sff_thread_start", {"NAME": "Chat"}),
        ("sff_thread_send", {"TEXT": "Hello!"}),
        ("sff_forum_post", {"TITLE": "Hello"}),
    ],
    "control": [("sff_if", {}), ("sff_repeat", {}), ("sff_wait", {"SECONDS": "2"})],
    "operators": [("sff_join", {}), ("sff_compare", {"A": "a", "B": "b"}), ("sff_random", {})],
    "variables": [("sff_set_variable", {}), ("sff_change_variable", {}), ("sff_variable", {})],
    "lists": [("sff_list_add", {}), ("sff_list_item", {}), ("sff_list_length", {})],
    "data": [("sff_json_load", {}), ("sff_json_get", {}), ("sff_reply_image", {})],
}


def _categories() -> list[dict[str, Any]]:
    """The families, in the editor's order, with the example blocks for each."""
    found = []
    for key in catalogue.CATEGORY_ORDER:
        family = catalogue.CATEGORIES[key]
        blocks = []
        for type_, values in EXAMPLES[key]:
            blocks.append({"type": type_, **catalogue.example(type_, **values)})
        found.append(
            {"id": key, "name": family["name"], "icon": family["icon"], "blurb": family["blurb"], "blocks": blocks}
        )
    return found


CATEGORIES: list[dict[str, Any]] = _categories()

HOW_IT_WORKS = [
    ("sign-in", "Log in with Fluxer", "Use your Fluxer account. We only ask for your name and avatar."),
    ("robot", "Add your bot", "Paste its token. We check it with Fluxer and keep it encrypted."),
    (
        "puzzle-piece",
        "Snap blocks together",
        "Events, messages, variables, lists and more. Each block writes real fluxer.py code.",
    ),
    ("play", "Run it", "Press Run and watch the console. Logs, warnings and errors appear as they happen."),
]

SAFETY = [
    (
        "lock-key",
        "Your token is encrypted",
        "It is encrypted before it is stored and never shown again. No block can read it, so a bot can't post it into chat.",
    ),
    ("user-circle", "Only your own bots", "You can add a bot only if your Fluxer account owns it."),
    (
        "puzzle-piece",
        "Blocks, not arbitrary code",
        "Every block turns into a fixed, checked piece of Python. There is no block that runs code you typed in.",
    ),
    (
        "shield-check",
        "Bots run apart from the site",
        "Each run is a separate process with limits on memory, processor time and running time, on a server that is not the website.",
    ),
    (
        "identification-card",
        "A minimal login",
        "We ask Fluxer for your name, avatar and ID, then give Fluxer's access token straight back.",
    ),
    ("trash", "Delete it all, any time", "One button on My bots removes your bots, tokens, blocks and files."),
]
