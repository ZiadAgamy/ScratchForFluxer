"""What changed on the site, newest first. This is the one place to edit: the /changelog page is drawn from this list.

To announce an update, add an entry at the top. Each change is ``(kind, text)`` where kind is one of ``KINDS``. Text is
plain words (no HTML); put names of blocks in quotes.
"""

from __future__ import annotations

import datetime
from typing import Any

#: kind -> the label shown on the page
KINDS = {"new": "New", "improved": "Improved", "fixed": "Fixed", "docs": "Docs"}

ENTRIES: list[dict[str, Any]] = [
    {
        "date": "2026-10-16",
        "title": "Channels, roles, threads and forums",
        "summary": "Your bot can now build and tidy up a community, not only talk in it. Two new block families bring all of it.",
        "changes": [
            (
                "new",
                "“Channels and roles” family: make a text, voice, announcement, forum, media or link channel, and delete a channel.",
            ),
            (
                "new",
                "Make a role (with a colour, and choices for showing it apart and letting people mention it) and delete a role.",
            ),
            (
                "new",
                "“Threads and forums” family: start a public or private thread, start one from a message, write inside it, and close, reopen or lock it.",
            ),
            (
                "new",
                "Publish a post in a forum or media channel, with a title, text, tags and a picture (an uploaded file or a web address).",
            ),
            ("new", "Add and remove the tags of a forum or media channel, and read the list of tags it has."),
            (
                "new",
                "“ID of the channel / role / thread I just made” blocks, and the codenames $newChannelID, $newRoleID and $newThreadID.",
            ),
            (
                "improved",
                "Blocks that change the community warn you when anyone could use them, so you can put them behind a staff-role check.",
            ),
            ("docs", "The guide has two new block families, and the safety notes for them."),
        ],
    },
    {
        "date": "2026-10-16",
        "title": "A better colour picker and a livelier home page",
        "summary": "Picking a colour is now quick and looks the same in every browser.",
        "changes": [
            (
                "new",
                "A new colour picker in Card options: a shade square, a rainbow slider, red, green and blue boxes, a code box, and the colours you used lately.",
            ),
            (
                "new",
                "“Pick from screen” copies a colour from anywhere on your screen (in browsers that have an eyedropper, such as Chrome and Edge).",
            ),
            (
                "improved",
                "The home page moves: soft blocks drift behind the title, sections rise into view as you scroll, and the blocks of a family pop in when you pick it. Nothing moves if your system asks for less motion.",
            ),
            ("new", "This changelog page."),
        ],
    },
    {
        "date": "2026-10-16",
        "title": "Codenames",
        "summary": "Put a value inside your text without a join block: write Coins Balance: $varCoins and the bot fills it in.",
        "changes": [
            (
                "new",
                "Codenames for your variables and lists: $varName is the value, $listName is every item, $countName is how many items.",
            ),
            (
                "new",
                "Codenames for the blocks that have no sockets: $authorName, $authorID, $messageID, $channelID, $communityID, $memberID, $botPing and more.",
            ),
            ("new", "“Insert a codename…” menu under the text boxes in Card options."),
            (
                "improved",
                "Codenames work in every text box, and in the card's author and footer. Write $$ for a plain dollar sign.",
            ),
            ("docs", "A beginner-friendly “Codenames” section in the guide, with a table of every codename."),
        ],
    },
    {
        "date": "2026-10-15",
        "title": "Card options for embeds",
        "summary": "Everything about a card is now in one window, with a preview.",
        "changes": [
            (
                "new",
                "“Card options” button on the embed blocks: colour, link, big picture, small picture, author, footer and the time.",
            ),
            ("new", "Pictures can be a web address or one of the pictures you uploaded on the Files tab."),
            ("improved", "A live preview of the card changes as you type."),
            ("improved", "Cards made before this update keep their colour."),
            ("docs", "A new “Cards (embeds)” section in the guide, with a picture of the window."),
        ],
    },
    {
        "date": "2026-10-15",
        "title": "Bots keep running",
        "summary": "A bot no longer stops by itself after a few hours.",
        "changes": [
            ("improved", "Bots run with no time limit: they stay online until you press Stop."),
            ("fixed", "Pictures in the guide were missing on the live site."),
            ("docs", "The guide no longer mentions a time limit."),
        ],
    },
    {
        "date": "2026-10-15",
        "title": "Open source",
        "summary": "Anyone can run their own copy of the site, even on a self-hosted Fluxer.",
        "changes": [
            ("new", "The project is open source under the MIT license."),
            (
                "docs",
                "A new README with the whole setup: your computer, a server, the runner, and a self-hosted Fluxer, with every link.",
            ),
        ],
    },
]


def _date(value: str) -> datetime.date:
    return datetime.date.fromisoformat(value)


def entries() -> list[dict[str, Any]]:
    """The entries newest first, with the date written out (``16 October 2026``) and a stable id for a link."""
    shown = []
    for index, entry in enumerate(sorted(ENTRIES, key=lambda e: e["date"], reverse=True)):
        day = _date(entry["date"])
        shown.append(
            {
                **entry,
                "day": day.isoformat(),
                "pretty": f"{day.day} {day.strftime('%B %Y')}",
                "latest": index == 0,
                "changes": [{"kind": kind, "label": KINDS[kind], "text": text} for kind, text in entry["changes"]],
            }
        )
    return shown


def by_date() -> list[dict[str, Any]]:
    """The same entries grouped under their date, newest first: ``[{"day", "pretty", "entries": [...]}]``."""
    groups: list[dict[str, Any]] = []
    for entry in entries():
        if groups and groups[-1]["day"] == entry["day"]:
            groups[-1]["entries"].append(entry)
        else:
            groups.append({"day": entry["day"], "pretty": entry["pretty"], "entries": [entry]})
    return groups
