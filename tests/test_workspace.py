"""What gets stored and shown: ``clean_workspace`` rebuilds any workspace into one the editor can open.

``workspace_check.problems`` is the independent statement of "the editor can open this"; every result here is run
through it, and the random tests do the same for thousands of damaged workspaces. ``tests/test_editor_browser.py``
then loads cleaned workspaces into the real Blockly to confirm the statement is true.
"""

from __future__ import annotations

import copy
import json
import os
import random
import time

import pytest
from blocks import (
    block,
    boolean,
    compare,
    hat,
    if_,
    join,
    list_variable,
    message_text,
    number,
    on_ready,
    show,
    stack,
    text,
    variable,
)
from blocks import (
    workspace as make_workspace,
)
from fuzz_blocks import UPLOADED, Randomizer, assert_sound, mutate
from workspace_check import assert_valid, by_id, chain, every_block, json_depth, problems

from scratch_fluxer import catalogue, workspace
from scratch_fluxer.compiler import compile_workspace, default_workspace

FIRST = int(os.environ.get("FUZZ_FIRST", "0"))
COUNT = int(os.environ.get("FUZZ_SEEDS", "200"))
SEEDS = range(FIRST, FIRST + COUNT)

VARIABLES = ("score", ("fruits", "List"))


def identical(a, b) -> bool:
    """Equal all the way down, with nan equal to nan (and no text made of a huge number, which Python refuses)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(identical(a[key], b[key]) for key in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(map(identical, a, b))
    if isinstance(a, float) and isinstance(b, float) and a != a and b != b:
        return True
    return type(a) is type(b) and a == b


def clean(raw):
    """The cleaned workspace, which must always be one the independent check accepts."""
    cleaned = workspace.clean_workspace(raw).workspace
    assert_valid(cleaned)
    return cleaned


def dropped(raw) -> int:
    return workspace.clean_workspace(raw).dropped


def ready(*steps, **kw):
    return on_ready(*steps, id=kw.pop("id", "hat"), **kw)


def one_block(type_: str, **kw):
    """The cleaned version of a single block lying on its own."""
    node = block(type_, id="only", x=10, y=20, **kw)
    return by_id(clean(make_workspace(node, variables=VARIABLES, setup=False, login=False)))["only"]


class TestTheStartingWorkspace:
    def test_it_is_already_clean(self):
        start = default_workspace()
        assert_valid(start)
        cleaned = workspace.clean_workspace(copy.deepcopy(start))
        assert cleaned.workspace == start
        assert cleaned.dropped == 0

    def test_cleaning_it_changes_nothing_about_its_python(self):
        start = default_workspace()
        assert compile_workspace(clean(copy.deepcopy(start))).code == compile_workspace(start).code

    @pytest.mark.parametrize("stored", [None, ""])
    def test_nothing_stored_gives_the_starting_workspace(self, stored):
        assert workspace.from_text(stored) == (default_workspace(), 0, False)

    def test_an_empty_object_gets_the_two_locked_blocks_and_nothing_else(self):
        cleaned = clean({})
        assert [node["type"] for node in cleaned["blocks"]["blocks"]] == ["sff_bot_setup", "sff_bot_login"]
        assert cleaned["variables"] == []


class TestWhatMayBeConnected:
    def test_an_unknown_block_is_left_out_and_the_blocks_below_it_stay(self):
        ws = make_workspace(ready(show("a"), block("sff_made_up", id="odd"), show("b")))
        result = workspace.clean_workspace(ws)
        assert chain(by_id(result.workspace)["hat"]) == ["sff_event_ready", "sff_console_print", "sff_console_print"]
        assert result.dropped == 1

    def test_an_unknown_block_that_starts_a_stack_hands_its_place_to_the_one_below(self):
        odd = block("sff_made_up", x=300, y=200, id="odd")
        odd["next"] = {"block": ready(x=0, y=0)}
        cleaned = clean(make_workspace(odd))
        head = by_id(cleaned)["hat"]
        assert (head["x"], head["y"]) == (300, 200)

    def test_an_unknown_type_of_anything_is_dropped(self):
        for junk in ({"type": None}, {"type": 5}, {"type": ["sff_text"]}, {}, {"id": "x"}, "sff_text", 7, None):
            ws = make_workspace()
            ws["blocks"]["blocks"].insert(1, junk)
            assert clean(ws) == clean(make_workspace())

    def test_a_hat_cannot_hang_from_another_block_and_neither_can_what_follows_it(self):
        ws = make_workspace(ready(show("a"), on_ready(id="inner"), show("b")))
        result = workspace.clean_workspace(ws)
        assert chain(by_id(result.workspace)["hat"]) == ["sff_event_ready", "sff_console_print"]
        assert "inner" not in by_id(result.workspace)
        assert result.dropped == 1

    def test_nothing_can_follow_a_block_that_ends_the_script(self):
        ws = make_workspace(ready(block("sff_stop_script"), show("never")))
        assert chain(by_id(clean(ws))["hat"]) == ["sff_event_ready", "sff_stop_script"]

    def test_a_block_with_a_value_cannot_be_a_step(self):
        ws = make_workspace(ready(show("a"), text("loose"), show("b")))
        assert chain(by_id(clean(ws))["hat"]) == ["sff_event_ready", "sff_console_print"]

    def test_a_statement_cannot_go_in_a_value_socket(self):
        ws = make_workspace(ready(block("sff_console_print", id="outer", inputs={"TEXT": block("sff_console_print")})))
        assert "inputs" not in by_id(clean(ws))["outer"]

    def test_a_value_cannot_start_the_inside_of_an_if(self):
        ws = make_workspace(ready(if_(True, text("not a step"), show("x")), id="hat"))
        inside = [node for node in every_block(clean(ws)) if node["type"] == "sff_if"][0]
        assert "inputs" not in inside or "DO" not in inside["inputs"]

    def test_a_hat_cannot_start_the_inside_of_an_if(self):
        ws = make_workspace(ready(if_(True, on_ready(id="inner"))))
        assert "inner" not in by_id(clean(ws))

    def test_a_locked_block_cannot_hang_from_another_block(self):
        ws = make_workspace(ready(show("a"), block("sff_bot_login", id="sneaky")), setup=False, login=False)
        cleaned = clean(ws)
        assert "sneaky" not in by_id(cleaned)  # and the real one is added
        assert [node["type"] for node in cleaned["blocks"]["blocks"]].count("sff_bot_login") == 1

    def test_loose_blocks_stay_where_they_are(self):
        loose_value = message_text()
        loose_value.update(x=500, y=600)
        loose_step = show("hi")
        loose_step.update(x=700, y=800)
        cleaned = clean(make_workspace(loose_value, loose_step))
        assert [(n["type"], n["x"], n["y"]) for n in cleaned["blocks"]["blocks"][1:3]] == [
            ("sff_msg_text", 500, 600),
            ("sff_console_print", 700, 800),
        ]

    @pytest.mark.parametrize(
        ("owner", "socket", "child", "kept"),
        [
            pytest.param("sff_wait", "SECONDS", message_text, False, id="text into a number socket"),
            pytest.param("sff_wait", "SECONDS", lambda: boolean(True), False, id="true/false into a number socket"),
            pytest.param("sff_wait", "SECONDS", lambda: list_variable("fruits"), False, id="a list into a number"),
            pytest.param("sff_wait", "SECONDS", lambda: block("sff_text_length"), True, id="a number into a number"),
            pytest.param("sff_wait", "SECONDS", lambda: variable("score"), True, id="a variable into a number"),
            pytest.param("sff_wait", "SECONDS", lambda: number(3), True, id="a number box into a number"),
            pytest.param("sff_console_print", "TEXT", lambda: boolean(True), True, id="true/false into text"),
            pytest.param("sff_console_print", "TEXT", lambda: list_variable("fruits"), True, id="a list into text"),
            pytest.param("sff_console_print", "TEXT", message_text, True, id="text into text"),
            pytest.param("sff_console_print", "TEXT", lambda: block("sff_stop_script"), False, id="a step into text"),
            pytest.param("sff_if", "COND", lambda: compare(1, "eq", 2), True, id="a test into a condition"),
            pytest.param("sff_if", "COND", message_text, False, id="text into a condition"),
            pytest.param("sff_if", "COND", lambda: number(1), False, id="a number into a condition"),
            pytest.param("sff_if", "COND", lambda: variable("score"), True, id="a variable into a condition"),
            pytest.param("sff_list_set", "VALUE", lambda: list_variable("fruits"), True, id="a list into a list"),
            pytest.param("sff_list_set", "VALUE", lambda: block("sff_list_split"), True, id="split into a list"),
            pytest.param("sff_list_set", "VALUE", lambda: variable("score"), True, id="a variable into a list"),
            pytest.param("sff_list_set", "VALUE", message_text, False, id="text into a list"),
            pytest.param("sff_for_each", "LIST", lambda: list_variable("fruits"), True, id="a list into for each"),
            pytest.param("sff_for_each", "LIST", lambda: block("sff_text_length"), False, id="a number into for each"),
        ],
    )
    def test_a_block_goes_in_a_socket_only_if_the_editor_would_let_it(self, owner, socket, child, kept):
        piece = child()
        ws = make_workspace(ready(block(owner, id="owner", inputs={socket: piece})), variables=VARIABLES)
        cleaned = clean(ws)
        assert (piece["id"] in by_id(cleaned)) is kept
        assert workspace.clean_workspace(ws).dropped == (0 if kept else 1)

    def test_a_box_in_a_socket_must_be_a_plain_box_of_the_right_kind(self):
        def socket_with(owner: str, name: str, shadow):
            return make_workspace(ready(block(owner, id="owner", inputs={})), variables=VARIABLES), shadow, name

        for owner, name, shadow, kept in [
            ("sff_wait", "SECONDS", number(5), True),
            ("sff_wait", "SECONDS", text("x"), False),  # text where a number goes
            ("sff_wait", "SECONDS", message_text(), False),  # not a box at all
            ("sff_if", "COND", number(1), False),  # nothing is a box for true/false
            ("sff_console_print", "TEXT", text("x"), True),
            ("sff_console_print", "TEXT", number(1), True),
            ("sff_console_print", "TEXT", message_text(), False),
        ]:
            ws, piece, socket = socket_with(owner, name, shadow)
            by_id(ws)["owner"]["inputs"] = {socket: {"shadow": piece}}
            assert (piece["id"] in by_id(clean(ws))) is kept, (owner, name, piece["type"])

    def test_a_block_over_a_box_keeps_both(self):
        over = block("sff_text_length")
        ws = make_workspace(ready(block("sff_wait", id="owner")))
        by_id(ws)["owner"]["inputs"] = {"SECONDS": {"shadow": number(1), "block": over}}
        slot = by_id(clean(ws))["owner"]["inputs"]["SECONDS"]
        assert set(slot) == {"shadow", "block"}
        assert slot["block"]["id"] == over["id"]

    def test_a_socket_that_ends_up_empty_is_left_out(self):
        ws = make_workspace(ready(block("sff_wait", id="owner", inputs={"SECONDS": message_text()})))
        assert "inputs" not in by_id(clean(ws))["owner"]

    def test_sockets_and_fields_the_block_does_not_have_are_dropped(self):
        node = block("sff_console_print", id="only", inputs={"TEXT": "x"}, fields={"NOPE": "x"})
        node["inputs"]["NOPE"] = {"block": text("y")}
        node["inputs"]["DO"] = {"block": show("z")}
        cleaned = by_id(clean(make_workspace(ready(node))))["only"]
        assert set(cleaned["inputs"]) == {"TEXT"}
        assert "fields" not in cleaned

    def test_a_block_with_nothing_filled_in_gets_its_fields_and_nothing_else(self):
        node = one_block("sff_math")
        assert node["fields"] == {"OP": "add"}
        assert "inputs" not in node
        assert one_block("sff_bot_name").get("fields") is None

    def test_sockets_given_as_the_wrong_kind_of_thing_are_ignored(self):
        for junk in (None, 5, "x", [], [{"block": text("a")}], True):
            node = block("sff_console_print", id="only")
            node["inputs"] = {"TEXT": junk}
            assert "inputs" not in by_id(clean(make_workspace(ready(node))))["only"]
        for junk in (None, 5, "x", [], True):
            node = block("sff_console_print", id="only")
            node["inputs"] = junk
            assert "inputs" not in by_id(clean(make_workspace(ready(node))))["only"]

    def test_a_chain_of_next_that_is_not_one_ends(self):
        for junk in (None, 5, "x", [], {"block": None}, {"block": 5}, {"nope": 1}):
            first = show("a")
            first["next"] = junk
            assert chain(by_id(clean(make_workspace(ready(first, id="hat"))))["hat"]) == [
                "sff_event_ready",
                "sff_console_print",
            ]


def label(value) -> str:
    """A name for a test case (pytest's own would choke on a number with thousands of digits)."""
    try:
        return repr(value)[:40]
    except ValueError:
        return f"a whole number with {len(hex(value)) * 5 // 4} or so digits"


def cases(rows):
    return [pytest.param(*row, id=label(row[0])) for row in rows]


class TestWhatABlockHolds:
    @pytest.mark.parametrize(
        ("given", "expected"),
        cases(
            [
                (5, 5),
                (5.0, 5),
                (-3, -3),
                (2.5, 2.5),
                (-0.0, 0),
                ("5", 5),
                (" 7.5 ", 7.5),
                ("1_0", 10),
                ("abc", 0),
                ("", 0),
                (None, 0),
                (True, 0),
                (False, 0),
                ([1], 0),
                ({"a": 1}, 0),
                (float("nan"), 0),
                (float("inf"), 0),
                (float("-inf"), 0),
                ("nan", 0),
                ("inf", 0),
                ("1e400", 0),
                (10**15, 10**15),
                (10**15 + 1, 0),
                (-(10**15) - 1, 0),
                (10**400, 0),
                (-(10**400), 0),
                (10**5000, 0),
            ]
        ),
    )
    def test_a_number_box_holds_a_finite_number_or_zero(self, given, expected):
        node = one_block("sff_number", fields={"NUM": given})
        assert node["fields"] == {"NUM": expected}
        assert type(node["fields"]["NUM"]) is type(expected)

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("hi", "hi"),
            ("", ""),
            ("a\x00b", "ab"),
            ("a\x1b[31mb", "a[31mb"),
            ("a\nb\tc", "a\nb\tc"),
            ("a‮b", "ab"),
            ("a\ud800b", "a�b"),
            ("x" * 3000, "x" * 2000),
            (5, "5"),
            (2.5, "2.5"),
            (2.0, "2"),
            (True, "true"),
            (None, ""),
            ({"x": 1}, ""),
            (["a"], ""),
            (float("nan"), "nan"),
        ],
    )
    def test_a_text_box_holds_clean_text(self, given, expected):
        assert one_block("sff_text", fields={"TEXT": given})["fields"] == {"TEXT": expected}

    def test_a_missing_text_box_takes_its_default(self):
        assert one_block("sff_text")["fields"] == {"TEXT": ""}
        assert one_block("sff_event_command")["fields"] == {"NAME": "ping"}
        assert one_block("sff_event_command", fields={"NAME": None})["fields"] == {"NAME": "ping"}
        assert one_block("sff_bot_setup")["fields"] == {"PREFIX": "!"}

    def test_a_box_keeps_to_its_own_length(self):
        assert one_block("sff_bot_setup", fields={"PREFIX": "abcdefghijkl"})["fields"] == {"PREFIX": "abcdefgh"}
        assert len(one_block("sff_event_command", fields={"NAME": "n" * 99})["fields"]["NAME"]) == 24

    @pytest.mark.parametrize("given", ["multiply", "subtract", "mod"])
    def test_a_menu_keeps_one_of_its_options(self, given):
        assert one_block("sff_math", fields={"OP": given})["fields"] == {"OP": given}

    @pytest.mark.parametrize("given", ["zzz", "", "MULTIPLY", 5, None, ["add"], {"add": 1}, True, "×"])
    def test_a_menu_that_holds_anything_else_is_reset(self, given):
        assert one_block("sff_math", fields={"OP": given})["fields"] == {"OP": "add"}

    def test_a_file_name_is_text_of_a_sensible_length(self):
        assert one_block("sff_reply_image", fields={"FILE": "cat.png"})["fields"] == {"FILE": "cat.png"}
        assert one_block("sff_reply_image", fields={"FILE": "x" * 200})["fields"] == {"FILE": "x" * 80}
        for junk in (5, None, ["cat.png"], {"a": 1}, True):
            assert one_block("sff_reply_image", fields={"FILE": junk})["fields"] == {"FILE": ""}
        assert one_block("sff_reply_image")["fields"] == {"FILE": ""}

    def test_fields_that_are_not_an_object_are_treated_as_missing(self):
        for junk in (None, 5, "x", [], ["TEXT"]):
            node = block("sff_text", id="only", x=0, y=0)
            node["fields"] = junk
            assert by_id(clean(make_workspace(node)))["only"]["fields"] == {"TEXT": ""}


class TestVariables:
    def named(self, raw_variables, *blocks):
        ws = make_workspace(*blocks)
        ws["variables"] = raw_variables
        return clean(ws)["variables"]

    def test_variables_that_are_listed_stay(self):
        assert self.named([{"name": "score", "id": "v1"}, {"name": "fruits", "id": "v2", "type": "List"}]) == [
            {"name": "score", "id": "v1"},
            {"name": "fruits", "id": "v2", "type": "List"},
        ]

    def test_names_that_match_when_capitals_are_ignored_are_told_apart(self):
        found = self.named(
            [{"name": "Score", "id": "v1"}, {"name": "score", "id": "v2"}, {"name": " SCORE ", "id": "v3"}]
        )
        assert [v["name"] for v in found] == ["Score", "score 2", "SCORE 3"]
        assert [v["id"] for v in found] == ["v1", "v2", "v3"]

    def test_a_list_and_a_variable_cannot_share_a_name_either(self):
        found = self.named([{"name": "x", "id": "v1"}, {"name": "x", "id": "v2", "type": "List"}])
        assert [v["name"] for v in found] == ["x", "x 2"]

    @pytest.mark.parametrize(
        ("given", "expected"),
        [("", "variable"), ("   ", "variable"), (None, "variable"), (5, "5"), ("  a   b ", "a b"), ("a\x00b", "ab")],
    )
    def test_a_name_is_tidied(self, given, expected):
        assert self.named([{"name": given, "id": "v1"}])[0]["name"] == expected

    def test_a_long_name_is_cut(self):
        assert len(self.named([{"name": "n" * 500, "id": "v1"}])[0]["name"]) == 60

    def test_a_long_name_that_has_to_be_told_apart_is_cut_to_make_room_for_the_number(self):
        found = self.named([{"name": "n" * 500, "id": f"v{i}"} for i in range(12)])
        assert [len(v["name"]) for v in found] == [60] * 12
        assert len({v["name"].lower() for v in found}) == 12
        assert found[1]["name"].endswith("n 2") and found[11]["name"].endswith("n 12")
        spaced = self.named([{"name": "a " * 40, "id": "v1"}, {"name": "a " * 40, "id": "v2"}])
        assert [v["name"] for v in spaced] == [
            ("a " * 40).strip()[:60].rstrip(),
            ("a " * 40).strip()[:58].rstrip() + " 2",
        ]

    def test_a_kind_is_a_list_or_nothing(self):
        found = self.named(
            [
                {"name": f"v{i}", "id": f"i{i}", "type": kind}
                for i, kind in enumerate(["List", "Number", "", None, 5, "list"])
            ]
        )
        assert [v.get("type") for v in found] == ["List", None, None, None, None, None]

    def test_variables_are_kept_together_by_kind_in_the_order_the_editor_writes_them(self):
        """Blockly writes its variables out one kind after another, so that is the order that counts as unchanged."""
        found = self.named(
            [
                {"name": "a", "id": "v1"},
                {"name": "L1", "id": "v2", "type": "List"},
                {"name": "b", "id": "v3"},
                {"name": "L2", "id": "v4", "type": "List"},
                {"name": "c", "id": "v5"},
            ]
        )
        assert [v["name"] for v in found] == ["a", "b", "c", "L1", "L2"]

    def test_the_kind_that_appears_first_stays_first(self):
        found = self.named(
            [
                {"name": "L1", "id": "v1", "type": "List"},
                {"name": "a", "id": "v2"},
                {"name": "L2", "id": "v3", "type": "List"},
            ]
        )
        assert [v["name"] for v in found] == ["L1", "L2", "a"]

    def test_variables_that_are_already_in_the_editors_order_are_not_moved(self):
        listed = [
            {"name": "a", "id": "v1"},
            {"name": "b", "id": "v2"},
            {"name": "L1", "id": "v3", "type": "List"},
            {"name": "L2", "id": "v4", "type": "List"},
        ]
        assert self.named(copy.deepcopy(listed)) == listed

    def test_putting_the_variables_in_order_is_a_thing_that_happens_only_once(self):
        ws = make_workspace()
        ws["variables"] = [
            {"name": "L", "id": "v1", "type": "List"},
            {"name": "a", "id": "v2"},
            {"name": "M", "id": "v3", "type": "List"},
        ]
        once = clean(ws)
        assert clean(once)["variables"] == once["variables"]

    def test_variables_the_cleaner_has_to_make_up_are_put_with_the_ones_of_their_kind(self):
        plain = block("sff_set_variable", id="s", fields={"VAR": {"id": "ghost-plain"}}, inputs={"VALUE": 1})
        listed = block("sff_list_clear", id="c", fields={"LIST": {"id": "ghost-list"}})
        ws = make_workspace(ready(plain, listed), variables=("a", ("L", "List"), "b"))
        found = clean(ws)["variables"]
        assert [(v["name"], v.get("type", "")) for v in found] == [
            ("a", ""),
            ("b", ""),
            ("score", ""),
            ("L", "List"),
            ("fruits", "List"),
        ]

    def test_variables_that_are_skipped_do_not_leave_gaps_in_the_order(self):
        found = self.named(
            [
                {"name": "L", "id": "v1", "type": "List"},
                {"name": "dropped"},
                {"name": "a", "id": "v2"},
            ]
        )
        assert [v["name"] for v in found] == ["L", "a"]

    def test_variables_without_an_id_or_with_a_repeated_one_are_skipped(self):
        found = self.named(
            [
                {"name": "a"},
                {"name": "b", "id": ""},
                {"name": "c", "id": 5},
                {"name": "d", "id": "x" * 500},
                {"name": "e", "id": "ok"},
                {"name": "f", "id": "ok"},
                "junk",
                None,
                7,
            ]
        )
        assert found == [{"name": "e", "id": "ok"}]

    @pytest.mark.parametrize("junk", [None, 5, "x", {}, {"a": 1}, True])
    def test_a_list_of_variables_that_is_not_a_list_is_empty(self, junk):
        assert self.named(junk) == []

    def test_there_is_a_limit_to_how_many_variables(self):
        found = self.named([{"name": f"v{i}", "id": f"i{i}"} for i in range(workspace.MAX_VARIABLES + 50)])
        assert len(found) == workspace.MAX_VARIABLES

    def test_equal_names_get_the_next_number_each_time_and_never_one_that_is_taken(self):
        names = workspace._Names(["variable 3"])
        made = [names.make("") for _ in range(4)] + [names.make("Variable"), names.make("other")]
        assert made == ["variable", "variable 2", "variable 4", "variable 5", "Variable 6", "other"]

    def test_a_huge_number_of_equal_names_is_handled_in_no_time(self):
        started = time.perf_counter()
        found = self.named([{"name": "x", "id": f"v{i}"} for i in range(workspace.MAX_VARIABLES)])
        assert len({v["name"].lower() for v in found}) == workspace.MAX_VARIABLES
        assert time.perf_counter() - started < 2

    def test_blocks_that_point_at_missing_variables_cannot_make_more_than_the_limit(self):
        """12,000 blocks that each name a variable nobody declared once took the server about 50 seconds."""
        pointing = [
            {"type": "sff_variable", "fields": {"VAR": {"id": f"ghost{i}"}}, "x": 0, "y": i} for i in range(12_000)
        ]
        started = time.perf_counter()
        with pytest.raises(workspace.WorkspaceTooBig, match=str(workspace.MAX_VARIABLES)):
            workspace.clean_workspace({"blocks": {"languageVersion": 0, "blocks": pointing}, "variables": []})
        assert time.perf_counter() - started < 2

    def test_missing_variables_up_to_the_limit_are_declared_with_names_of_their_own(self):
        pointing = [
            {"type": "sff_variable", "fields": {"VAR": {"id": f"ghost{i}"}}, "x": 0, "y": i}
            for i in range(workspace.MAX_VARIABLES)
        ]
        cleaned = workspace.clean_workspace({"blocks": {"languageVersion": 0, "blocks": pointing}, "variables": []})
        names = [v["name"].lower() for v in cleaned.workspace["variables"]]
        assert len(names) == workspace.MAX_VARIABLES and len(set(names)) == workspace.MAX_VARIABLES

    def test_a_block_that_points_at_a_missing_variable_gets_one_declared(self):
        node = block("sff_set_variable", id="s", fields={"VAR": {"id": "ghost"}}, inputs={"VALUE": 1})
        cleaned = clean(make_workspace(ready(node)))
        assert cleaned["variables"] == [{"name": "score", "id": "ghost"}]
        assert by_id(cleaned)["s"]["fields"]["VAR"] == {"id": "ghost"}

    def test_blocks_that_point_at_the_same_missing_variable_share_it(self):
        steps = [block("sff_change_variable", fields={"VAR": {"id": "ghost"}}) for _ in range(3)]
        cleaned = clean(make_workspace(ready(*steps)))
        assert cleaned["variables"] == [{"name": "score", "id": "ghost"}]

    @pytest.mark.parametrize("state", [None, {}, {"id": None}, {"id": ""}, {"id": 5}, {"id": ["x"]}, "score", 5, ["a"]])
    def test_a_block_with_no_usable_variable_gets_the_default_one(self, state):
        node = block("sff_variable", id="v")
        node["fields"] = {"VAR": state}
        cleaned = clean(make_workspace(show(node)))
        assert [v["name"] for v in cleaned["variables"]] == ["score"]
        assert by_id(cleaned)["v"]["fields"]["VAR"] == {"id": cleaned["variables"][0]["id"]}

    def test_blocks_with_no_variable_share_one_default_per_kind_of_block(self):
        steps = [block("sff_variable"), block("sff_variable"), block("sff_list_variable"), block("sff_for_each")]
        cleaned = clean(make_workspace(show(steps[0]), show(steps[1]), show(steps[2]), steps[3]))
        assert sorted((v["name"], v.get("type", "")) for v in cleaned["variables"]) == [
            ("fruits", "List"),
            ("item", ""),
            ("score", ""),
        ]

    def test_a_list_block_pointing_at_a_plain_variable_gets_a_list_of_its_own(self):
        node = list_variable("score")
        node["id"] = "l"
        cleaned = clean(make_workspace(show(node), variables=VARIABLES))
        new_id = by_id(cleaned)["l"]["fields"]["LIST"]["id"]
        assert new_id != "var-score"
        created = [v for v in cleaned["variables"] if v["id"] == new_id][0]
        assert created == {"name": "score 2", "id": new_id, "type": "List"}
        assert {"name": "score", "id": "var-score"} in cleaned["variables"]

    def test_a_plain_block_pointing_at_a_list_gets_a_plain_variable_of_its_own(self):
        node = variable("fruits")
        node["id"] = "p"
        cleaned = clean(make_workspace(show(node), variables=VARIABLES))
        new_id = by_id(cleaned)["p"]["fields"]["VAR"]["id"]
        assert [v for v in cleaned["variables"] if v["id"] == new_id] == [{"name": "fruits 2", "id": new_id}]

    def test_many_blocks_with_the_same_wrong_variable_share_the_replacement(self):
        nodes = [list_variable("score") for _ in range(4)]
        cleaned = clean(make_workspace(*[show(n) for n in nodes], variables=VARIABLES))
        assert len(cleaned["variables"]) == 3

    def test_a_block_that_takes_either_kind_keeps_either(self):
        for name in ("score", "fruits"):
            node = block("sff_json_load", id="j", fields={"FILE": "data.json", "VAR": {"id": f"var-{name}"}})
            cleaned = clean(make_workspace(ready(node), variables=VARIABLES))
            assert by_id(cleaned)["j"]["fields"]["VAR"] == {"id": f"var-{name}"}
            assert len(cleaned["variables"]) == 2

    def test_a_variable_id_is_never_also_a_block_id(self):
        node = show("x")
        node["id"] = "var-score"
        cleaned = clean(make_workspace(ready(node, id="hat"), variables=VARIABLES))
        ids = [n["id"] for n in every_block(cleaned)] + [v["id"] for v in cleaned["variables"]]
        assert len(ids) == len(set(ids))


class TestIds:
    def test_every_block_gets_a_different_id(self):
        steps = [show("a"), show("b"), show("c")]
        for step in steps:
            step["id"] = "same"
        cleaned = clean(make_workspace(ready(*steps)))
        ids = [node["id"] for node in every_block(cleaned)]
        assert len(ids) == len(set(ids))
        assert "same" in ids  # the first one keeps it

    @pytest.mark.parametrize("given", [None, "", 5, ["x"], "x" * 121, "a\nb", "a\x00b", {"a": 1}, True])
    def test_a_block_without_a_usable_id_gets_one(self, given):
        node = show("x")
        node["id"] = given
        cleaned = clean(make_workspace(ready(node)))
        assert all(isinstance(n["id"], str) and n["id"] for n in every_block(cleaned))

    def test_the_ids_it_makes_do_not_clash_with_ones_that_exist(self):
        node = show("x")
        node["id"] = None
        clash = show("y")
        clash["id"] = "fix-1"
        cleaned = clean(make_workspace(ready(node, clash)))
        ids = [n["id"] for n in every_block(cleaned)]
        assert len(ids) == len(set(ids))

    def test_ids_with_unusual_characters_are_kept(self):
        node = show("x")
        node["id"] = "`{|}~ émoji 😀"
        assert "`{|}~ émoji 😀" in by_id(clean(make_workspace(ready(node))))


class TestTheLockedBlocks:
    def test_missing_ones_are_added(self):
        cleaned = clean(make_workspace(ready(), setup=False, login=False))
        types = [node["type"] for node in cleaned["blocks"]["blocks"]]
        assert types == ["sff_bot_setup", "sff_event_ready", "sff_bot_login"]
        setup, _hat, login = cleaned["blocks"]["blocks"]
        assert (setup["x"], setup["y"]) == (40, 40)
        assert setup["fields"] == {"PREFIX": "!"}
        assert login["y"] > _hat["y"]
        assert setup["deletable"] is login["deletable"] is False

    def test_the_login_goes_below_everything_else(self):
        low = ready(id="low")
        low["y"] = 5000
        cleaned = clean(make_workspace(low, setup=False, login=False))
        login = cleaned["blocks"]["blocks"][-1]
        assert login["type"] == "sff_bot_login" and login["y"] == 5160

    def test_the_login_is_still_on_the_page_when_everything_else_is_at_the_edge(self):
        far = ready(id="far")
        cleaned = clean(make_workspace(far, setup=False, login=False))
        by_id(cleaned)["far"]["y"] = 1e9
        ws = make_workspace(ready(), setup=False, login=False)
        by_id(ws)["hat"]["y"] = 10**9
        cleaned = clean(ws)
        assert cleaned["blocks"]["blocks"][-1]["y"] == workspace.MAX_COORDINATE

    def test_only_the_first_of_each_is_kept(self):
        first = block("sff_bot_setup", id="s1", x=1, y=1, fields={"PREFIX": "?"})
        second = block("sff_bot_setup", id="s2", x=2, y=2, fields={"PREFIX": "$"})
        third = block("sff_bot_login", id="l2", x=2, y=2)
        ws = make_workspace(first, second, ready(), third, setup=False, login=True)
        result = workspace.clean_workspace(ws)
        ids = by_id(result.workspace)
        assert "s1" in ids and "s2" not in ids and "l2" in ids and "login" not in ids  # the builder adds a login too
        assert result.dropped == 2
        assert ids["s1"]["fields"] == {"PREFIX": "?"}

    def test_they_are_locked_whatever_was_saved(self):
        for flag in (True, None, "no", 0):
            setup = block("sff_bot_setup", id="s", x=0, y=0)
            setup["deletable"] = flag
            assert by_id(clean(make_workspace(setup, setup=False)))["s"]["deletable"] is False

    def test_other_blocks_are_never_locked(self):
        node = show("x")
        node["deletable"] = False
        assert "deletable" not in by_id(clean(make_workspace(ready(node, id="hat"))))[node["id"]]


class TestPositions:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            (12, 12),
            (12.0, 12),
            (5.678, 5.68),
            (-40, -40),
            (0.004, 0),
            (1e300, 1_000_000),
            (-1e300, -1_000_000),
            (10**400, 1_000_000),
            (float("inf"), 1_000_000),
            (float("-inf"), -1_000_000),
            (float("nan"), 0),
            ("12", 0),
            (None, 0),
            (True, 0),
            ([1], 0),
        ],
    )
    def test_x_and_y_are_ordinary_numbers(self, given, expected):
        ws = make_workspace(ready())
        by_id(ws)["hat"]["x"] = by_id(ws)["hat"]["y"] = given
        head = by_id(clean(ws))["hat"]
        assert (head["x"], head["y"]) == (expected, expected)
        assert type(head["x"]) is type(expected)

    def test_a_missing_position_is_zero(self):
        ws = make_workspace(ready())
        del by_id(ws)["hat"]["x"], by_id(ws)["hat"]["y"]
        head = by_id(clean(ws))["hat"]
        assert (head["x"], head["y"]) == (0, 0)

    def test_blocks_inside_other_blocks_have_no_position(self):
        node = show("x")
        node["x"], node["y"] = 5, 6
        cleaned = clean(make_workspace(ready(node, id="hat")))
        assert "x" not in by_id(cleaned)[node["id"]]


class TestSwitchedOffAndFoldedBlocks:
    @pytest.mark.parametrize(
        "flags",
        [
            {"disabledReasons": ["MANUALLY_DISABLED"]},
            {"disabledReasons": ["ORPHANED_BLOCK", "x"]},
            {"enabled": False},
            {"disabled": True},
            {"disabledReasons": "yes"},
        ],
    )
    def test_a_block_that_is_turned_off_stays_off(self, flags):
        node = show("x")
        node.update(flags)
        cleaned = by_id(clean(make_workspace(ready(node))))[node["id"]]
        assert cleaned["disabledReasons"] == ["MANUALLY_DISABLED"]
        assert "enabled" not in cleaned and "disabled" not in cleaned

    @pytest.mark.parametrize(
        "flags",
        [
            {},
            {"enabled": True},
            {"disabled": False},
            {"disabledReasons": []},
            {"disabledReasons": None},
            {"enabled": 0},
        ],
    )
    def test_a_block_that_is_not_turned_off_stays_on(self, flags):
        node = show("x")
        node.update(flags)
        assert "disabledReasons" not in by_id(clean(make_workspace(ready(node))))[node["id"]]

    def test_the_python_does_not_change_for_a_turned_off_block(self):
        node = show("x")
        node["disabledReasons"] = ["MANUALLY_DISABLED"]
        ws = make_workspace(ready(show("a"), node, show("b")))
        assert compile_workspace(clean(ws)).code == compile_workspace(ws).code
        assert '"x"' not in compile_workspace(clean(ws)).code

    def test_a_folded_block_stays_folded(self):
        node = show("x")
        node["collapsed"] = True
        assert by_id(clean(make_workspace(ready(node))))[node["id"]]["collapsed"] is True
        for junk in (False, None, "yes", 1):
            node["collapsed"] = junk
            assert "collapsed" not in by_id(clean(make_workspace(ready(node))))[node["id"]]


class TestTheWholeWorkspace:
    @pytest.mark.parametrize("junk", [None, 5, "x", [], True, [{"blocks": []}]])
    def test_something_that_is_not_a_workspace_is_refused(self, junk):
        with pytest.raises(workspace.WorkspaceTooBig):
            workspace.clean_workspace(junk)

    @pytest.mark.parametrize(
        "raw",
        [{"blocks": None}, {"blocks": 5}, {"blocks": {}}, {"blocks": {"blocks": None}}, {"blocks": {"blocks": {}}}],
    )
    def test_a_workspace_with_no_usable_blocks_has_just_the_locked_ones(self, raw):
        assert [n["type"] for n in clean(raw)["blocks"]["blocks"]] == ["sff_bot_setup", "sff_bot_login"]

    def test_cleaning_does_not_touch_what_it_was_given(self):
        rng = random.Random(1)
        ws = mutate(rng, Randomizer(rng, mistakes=0.2).workspace(), 8)
        before = copy.deepcopy(ws)
        cleaned = clean(ws)
        assert identical(ws, before)
        for node in every_block(cleaned):
            node["id"] = "changed"
        assert identical(ws, before)

    def test_cleaning_twice_gives_the_same_thing(self):
        for seed in range(30):
            rng = random.Random(seed)
            ws = mutate(rng, Randomizer(rng, mistakes=0.2).workspace(), 8)
            once = clean(ws)
            assert clean(once) == once
            assert workspace.clean_workspace(once).dropped == 0

    def test_a_clean_workspace_is_json_that_reads_back_the_same(self):
        for seed in range(30):
            rng = random.Random(seed)
            cleaned = clean(mutate(rng, Randomizer(rng, mistakes=0.2).workspace(), 8))
            text = workspace.dumps(cleaned)
            assert "\n" not in text
            assert workspace.loads(text) == cleaned
            assert workspace.from_text(text) == (cleaned, 0, False)

    def test_the_text_holds_unicode_as_it_is_and_nothing_that_is_not_valid_utf8(self):
        node = show("héllo 😀 日本語")
        text = workspace.dumps(clean(make_workspace(ready(node))))
        assert "héllo 😀 日本語" in text
        text.encode("utf-8")  # a lone surrogate would raise here
        lone = show("a\ud800b")
        workspace.dumps(clean(make_workspace(ready(lone)))).encode("utf-8")


class TestLimits:
    def row(self, count: int, make=lambda i: show(str(i))):
        return make_workspace(
            hat("sff_event_ready", *[make(i) for i in range(count - 1)], id="hat"), variables=VARIABLES
        )

    @staticmethod
    def clear_list(_i: int):
        """A block with nothing in a socket: its variable is the deepest thing it holds."""
        return block("sff_list_clear", fields={"LIST": {"id": "var-fruits"}})

    def test_a_row_of_the_length_people_are_told_about_reads_back(self):
        cleaned = clean(self.row(workspace.MAX_IN_A_ROW))
        assert len(chain(by_id(cleaned)["hat"])) == workspace.MAX_IN_A_ROW
        assert json_depth(cleaned) <= workspace.MAX_JSON_DEPTH
        assert workspace.loads(workspace.dumps(cleaned)) == cleaned

    def test_the_numbers_in_workspace_py_are_exact(self):
        longest = (workspace.MAX_JSON_DEPTH - 6) // 2 + 1  # 4 brackets to the first block, 2 more for each, 2 inside
        cleaned = clean(self.row(longest, self.clear_list))
        assert len(chain(by_id(cleaned)["hat"])) == longest
        assert json_depth(cleaned) == workspace.MAX_JSON_DEPTH
        with pytest.raises(workspace.WorkspaceTooBig):
            workspace.clean_workspace(self.row(longest + 1, self.clear_list))

    def test_a_longer_row_is_refused_with_advice(self):
        with pytest.raises(workspace.WorkspaceTooBig, match="in a row") as caught:
            workspace.clean_workspace(self.row(workspace.MAX_IN_A_ROW + 10))
        assert "Split it" in str(caught.value)

    def test_whatever_is_kept_is_never_deeper_than_the_limit(self):
        outcomes = []
        for count in range(workspace.MAX_IN_A_ROW - 5, workspace.MAX_IN_A_ROW + 15):
            try:
                cleaned = workspace.clean_workspace(self.row(count)).workspace
            except workspace.WorkspaceTooBig:
                outcomes.append(False)
                continue
            assert json_depth(cleaned) <= workspace.MAX_JSON_DEPTH, count
            outcomes.append(True)
        assert outcomes == sorted(outcomes, reverse=True)  # once a row is too long, every longer one is too
        assert outcomes[0] is True and outcomes[-1] is False

    def test_a_row_inside_an_if_counts_from_where_it_starts(self):
        longest = (workspace.MAX_JSON_DEPTH - 6) // 2 + 1

        def inside(count: int):
            return [self.clear_list(i) for i in range(count)]  # new blocks each time: stacking links them up

        clean(make_workspace(hat("sff_event_ready", *inside(longest - 1)), variables=VARIABLES))  # fine on its own
        with pytest.raises(workspace.WorkspaceTooBig):
            workspace.clean_workspace(make_workspace(ready(if_(True, *inside(longest - 1))), variables=VARIABLES))
        clean(make_workspace(ready(if_(True, *inside(50))), variables=VARIABLES))

    def test_blocks_can_be_nested_a_long_way_but_not_without_end(self):
        def nested(depth: int):
            node = text("x")
            for _ in range(depth):
                node = join(node, "y")
            return make_workspace(ready(show(node)))

        assert json_depth(clean(nested(workspace.MAX_NESTING - 2))) <= workspace.MAX_JSON_DEPTH
        with pytest.raises(workspace.WorkspaceTooBig):
            workspace.clean_workspace(nested(workspace.MAX_NESTING + 5))

    def test_there_is_a_limit_to_how_many_blocks(self):
        many = make_workspace(*[on_ready() for _ in range(workspace.MAX_NODES + 1)])
        with pytest.raises(workspace.WorkspaceTooBig):
            workspace.clean_workspace(many)

    def test_text_nested_too_deeply_to_read_is_refused_not_a_crash(self):
        deep = "[" * 200_000 + "]" * 200_000
        with pytest.raises(workspace.WorkspaceTooBig):
            workspace.loads(deep)
        deep_objects = '{"a":' * 200_000 + "1" + "}" * 200_000
        assert workspace.from_text(deep_objects) == (default_workspace(), 0, True)


class TestReadingAndWritingText:
    def test_what_is_stored_comes_back_cleaned(self):
        ws = make_workspace(ready(show("a"), block("sff_made_up"), show("b")))
        cleaned, removed, damaged = workspace.from_text(json.dumps(ws))
        assert (removed, damaged) == (1, False)
        assert_valid(cleaned)

    @pytest.mark.parametrize(
        "stored",
        [
            "not json",
            "{",
            "[]",
            "5",
            "null",
            '"text"',
            '{"blocks": NaN}',
            '{"x": Infinity}',
            '{"x": -Infinity}',
            '{"x": ' + "9" * 5000 + "}",
            "\x00",
            '{"a": "\ud800"}'.encode("utf-16", "surrogatepass").decode("utf-16", "ignore") + "{",
        ],
    )
    def test_text_that_is_not_a_workspace_gives_the_starting_one_and_says_so(self, stored):
        assert workspace.from_text(stored) == (default_workspace(), 0, True)

    @pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
    def test_json_constants_that_are_not_json_are_refused(self, constant):
        with pytest.raises(ValueError, match="not allowed in JSON"):
            workspace.loads(f'{{"x": {constant}}}')

    def test_bytes_are_read_too(self):
        assert workspace.loads(b'{"a": [1, 2]}') == {"a": [1, 2]}

    def test_a_huge_number_is_not_a_crash(self):
        with pytest.raises(ValueError):
            workspace.loads('{"x": ' + "9" * 5000 + "}")
        assert workspace.loads('{"x": 1e999}')["x"] == float("inf")  # and the cleaner treats it as no number
        raw = workspace.loads(json.dumps(make_workspace(ready(show("a")))).replace('"a"', "1e999"))
        assert_valid(workspace.clean_workspace(raw).workspace)


NOISE = {
    "extraState": {"a": 1},
    "data": "anything",
    "movable": False,
    "editable": False,
    "inline": True,
    "icons": {},
    "comment": "hi",
    "mutation": "<x/>",
}


def add_noise(rng: random.Random, ws):
    """The same workspace with things Blockly or a person could have added that mean nothing to the bot."""
    ws = copy.deepcopy(ws)
    top = ws["blocks"]["blocks"]
    on_top = {id(node) for node in top}
    for node in every_block(ws):
        inner = id(node) not in on_top
        if rng.random() < 0.3:
            key = rng.choice(list(NOISE))
            node[key] = copy.deepcopy(NOISE[key])
        if rng.random() < 0.2:
            node.setdefault("fields", {})["ZZZ"] = "x"
        if rng.random() < 0.2:
            node.setdefault("inputs", {})["ZZZ"] = {"block": {"type": "sff_text", "id": "zz"}}
        if inner and rng.random() < 0.2:
            node["x"], node["y"] = rng.randint(-5, 5), rng.random()
        if node["type"] not in catalogue.LOCKED_TYPES and rng.random() < 0.1:
            node["deletable"] = False
        for name, value in list(node.get("fields", {}).items()):
            if isinstance(value, int | float) and not isinstance(value, bool) and rng.random() < 0.3:
                node["fields"][name] = str(value)  # a number written as text
        if "disabledReasons" in node:
            node.pop("disabledReasons")
            node.update(rng.choice([{"enabled": False}, {"disabled": True}, {"disabledReasons": ["X"]}]))
        if "next" in node and rng.random() < 0.2:
            node["next"] = {"block": {"type": "nope", "id": "gap", "next": node["next"]}}
        for slot in node.get("inputs", {}).values():
            first = slot.get("block")
            if isinstance(first, dict) and first.get("type") in catalogue.BLOCKS and rng.random() < 0.2:
                if catalogue.BLOCKS[first["type"]]["shape"] in ("statement", "cap"):
                    slot["block"] = {"type": "nope", "next": {"block": first}}
    for node in list(top):
        if node["type"] in catalogue.LOCKED_TYPES and rng.random() < 0.5:
            top.append({**copy.deepcopy(node), "id": "again" + node["id"]})  # a second one: the first is kept
    for _ in range(rng.randint(0, 3)):
        junk = rng.choice([None, 5, "x", [], {}, {"type": "nope", "x": 1, "y": 2}, {"type": 5}])
        top.insert(rng.randint(0, len(top)), junk)
    return ws


class TestNoiseThatMeansNothing:
    def test_what_does_not_change_the_bot_does_not_change_what_is_kept(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            wanted = clean(Randomizer(rng, mistakes=0.0).workspace())
            noisy = add_noise(rng, wanted)
            try:
                assert clean(noisy) == wanted
            except AssertionError:
                print(f"seed {seed}: FUZZ_FIRST={seed} FUZZ_SEEDS=1")
                raise

    def test_a_workspace_that_the_editor_wrote_is_kept_as_it_is(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            wanted = clean(Randomizer(rng, mistakes=0.0).workspace())
            result = workspace.clean_workspace(copy.deepcopy(wanted))
            assert result.workspace == wanted and result.dropped == 0
            assert (
                compile_workspace(wanted, assets=UPLOADED).code
                == compile_workspace(result.workspace, assets=UPLOADED).code
            )


class TestRandomDamage:
    def test_whatever_arrives_comes_out_as_something_the_editor_can_open(self):
        refused = 0
        for seed in SEEDS:
            rng = random.Random(seed)
            raw = mutate(rng, Randomizer(rng, mistakes=rng.choice([0.0, 0.05, 0.2])).workspace(), rng.randint(0, 14))
            try:
                try:
                    cleaned = workspace.clean_workspace(raw).workspace
                except workspace.WorkspaceTooBig:
                    refused += 1
                    continue
                found = problems(cleaned)
                assert not found, found[:5]
                assert workspace.clean_workspace(cleaned).workspace == cleaned
                assert workspace.loads(workspace.dumps(cleaned)) == cleaned
                assert_sound(compile_workspace(cleaned, assets=UPLOADED))
            except Exception:
                print(f"seed {seed}: FUZZ_FIRST={seed} FUZZ_SEEDS=1")
                raise
        assert refused < max(1, COUNT // 20), "random damage is refused too often, so it isn't being tested"

    def test_every_block_of_the_catalogue_survives_being_cleaned_with_nothing_filled_in(self):
        for type_, spec in catalogue.BLOCKS.items():
            if spec["shape"] in ("hat", "config"):
                ws = make_workspace(block(type_, id="only"), setup=False, login=False)
            elif spec["shape"] in ("value", "boolean", "literal"):
                ws = make_workspace(show(block(type_, id="only")))
            else:
                ws = make_workspace(ready(block(type_, id="only")))
            cleaned = clean(ws)
            assert by_id(cleaned)["only"]["type"] == type_, type_
            assert workspace.clean_workspace(ws).dropped == 0, type_
        assert stack  # the builder module's stack() is used by the other classes
