"""What the /docs page says about the blocks.

The list of blocks is not written here: every block on the page is drawn from the block catalogue
(``catalogue.example`` and its tooltip), so the guide always matches the editor, including blocks added later. Only the
plain-language introduction to each family, and the order they are explained in, live in this file.
"""

from __future__ import annotations

from typing import Any

from . import catalogue

#: What a family is for, in words a beginner can use, plus one small example to try.
FAMILY_GUIDE: dict[str, dict[str, str]] = {
    "events": {
        "intro": (
            "An event is something that happens in Fluxer: your bot comes online, someone sends a message, someone joins "
            "your community. Every script of blocks starts with a yellow event block, and the blocks snapped under it "
            "run each time that event happens. Blocks that aren't under an event never run."
        ),
        "try": "Use <strong>when the command ping is used</strong> and snap <strong>reply with Pong!</strong> under it. Now typing <code>!ping</code> in Fluxer makes your bot answer.",
    },
    "bot": {
        "intro": (
            "These blocks are about the bot itself rather than a message: the little status dot next to its name, the "
            "text under its name, and what it writes to the console (the panel under the Python code in the editor)."
        ),
        "try": "Put <strong>write Bot is online! to the console</strong> under <strong>when the bot is ready</strong>. When you press Run, that line appears in the console.",
    },
    "messages": {
        "intro": (
            "These blocks make your bot do something: reply, send to a channel, post an embed (a message with a title and "
            "a coloured edge), react with an emoji, send a private message, delete a message, or manage roles and "
            "members. Most of them only make sense inside an event that has a message or a person, and the editor tells "
            "you if one is in the wrong place."
        ),
        "try": "Snap <strong>reply with embed</strong> under a command to answer with a coloured card instead of plain text, then press its <strong>Card options</strong> button to change the colour, add a picture, a link and more.",
    },
    "info": {
        "intro": (
            "Round blocks that hold information about the message that just arrived: what it says, who wrote it, which "
            "channel it is in. They don't do anything on their own. Drop them into the white sockets of other blocks."
        ),
        "try": "Drop <strong>author name</strong> into the second socket of a <strong>join</strong> block to say <em>Hello, Sam</em>.",
    },
    "control": {
        "intro": (
            "Control blocks decide what happens and how often. <em>If</em> runs its blocks only when something is true, "
            "<em>repeat</em> runs them several times, and <em>wait</em> pauses for a number of seconds."
        ),
        "try": "Put <strong>reply with</strong> inside an <strong>if</strong> block whose condition checks the author's name.",
    },
    "operators": {
        "intro": (
            "Operators work with values: join two pieces of text, compare two things (is this equal to that?), and do "
            "maths. Like Message info blocks they are round, so they go into sockets."
        ),
        "try": "Use <strong>join</strong> to combine <em>Hello, </em> with a name, or <strong>pick random</strong> to answer with a different number each time.",
    },
    "variables": {
        "intro": (
            "A variable is a labelled box that remembers a value. Create one with the <em>Make a variable</em> button in "
            "the editor, give it a value with <em>set</em>, and change it later with <em>change by</em>. A variable lives "
            "as long as your bot runs: when the bot stops, it forgets."
        ),
        "try": "Make a variable called <strong>count</strong>, <strong>change count by 1</strong> inside a command, then reply with it to count how often the command is used.",
    },
    "lists": {
        "intro": (
            "A list keeps several values together in order, like a shopping list: add to it, remove from it, take the "
            "first or a random item, or repeat something for every item."
        ),
        "try": "Make a list of jokes, then reply with <strong>random item of jokes</strong>.",
    },
    "data": {
        "intro": (
            "These blocks use files you uploaded on the Files tab. Read a value out of a JSON file (a small data file, "
            "good for quotes, answers or settings), or send an image or text file as an attachment."
        ),
        "try": "Upload a picture on the Files tab, then use <strong>reply with image</strong> and pick it from the dropdown.",
    },
}

#: Where a block may be used, in words. The keys are the block's ``needs`` as a set.
NEEDS = {
    frozenset({"message"}): "message and command events",
    frozenset({"command"}): "command events",
    frozenset({"member"}): "member join and leave events",
    frozenset({"reaction"}): "reaction events",
    frozenset({"message", "member"}): "message, command and member events",
    frozenset({"message", "member", "reaction"}): "message, command, member and reaction events",
}


def where_it_works(needs: tuple[str, ...] | None) -> str | None:
    if not needs:
        return None
    return NEEDS.get(frozenset(needs), ", ".join(needs) + " events")


def families() -> list[dict[str, Any]]:
    """Every family, with every block the editor offers in it, drawn the same way the landing page draws them."""
    found = []
    for key in catalogue.CATEGORY_ORDER:
        family = catalogue.CATEGORIES[key]
        blocks = []
        for type_ in catalogue.TOOLBOX[key]:
            spec = catalogue.BLOCKS[type_]
            drawn = catalogue.example(type_)
            blocks.append(
                {
                    "type": type_,
                    "spec": drawn,
                    "tooltip": spec["tooltip"],
                    "where": where_it_works(spec["needs"]),
                    "words": " ".join(part if isinstance(part, str) else str(part[1]) for part in drawn["parts"]),
                }
            )
        found.append(
            {
                "id": key,
                "name": family["name"],
                "icon": family["icon"],
                "color": family["color"],
                "blurb": family["blurb"],
                **FAMILY_GUIDE[key],
                "blocks": blocks,
            }
        )
    return found


def codename_groups() -> list[dict[str, Any]]:
    """The codenames, grouped the way the guide shows them: ``[{"name", "items": [{"code", "what", "block", "where"}]}]``."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for name, type_, group, what in catalogue.CODENAME_LIST:
        spec = catalogue.BLOCKS[type_]
        groups.setdefault(group, []).append(
            {
                "code": f"${name}",
                "what": what,
                "block": catalogue.flat_label(type_),
                "where": where_it_works(spec["needs"]) or "anywhere",
            }
        )
    return [{"name": name, "items": items} for name, items in groups.items()]
