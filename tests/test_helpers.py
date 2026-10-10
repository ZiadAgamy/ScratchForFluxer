"""The small functions a generated bot defines for itself behave as their docstrings say.

They are run exactly as the compiler writes them (the sources in ``compiler.HELPERS``), so a change to a helper that
breaks what a block promises fails here.
"""

from __future__ import annotations

import math
import random
from typing import Any

import pytest

from scratch_fluxer.compiler import HELPER_ORDER, HELPERS


@pytest.fixture(scope="module")
def helpers() -> dict[str, Any]:
    namespace: dict[str, Any] = {"random": random, "math": math}
    for name in HELPER_ORDER:
        if name != "update_presence":  # needs the bot and the gateway; tests/test_generated_bots.py covers it
            exec(HELPERS[name]["source"], namespace)  # noqa: S102 - these are the compiler's own templates
    return namespace


class TestToNumber:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("12", 12),
            (" 12 ", 12),
            ("-7", -7),
            ("2.5", 2.5),
            ("1e3", 1000.0),
            (2.5, 2.5),  # a number that is already a number is left alone, decimals included
            (-0.75, -0.75),
            (7, 7),
            (True, 1),
            (False, 0),
            ("abc", 0),
            ("", 0),
            (None, 0),
            ([1, 2], 0),
            ({}, 0),
            ("nan", 0),
            ("inf", 0),
            ("-infinity", 0),
            ("1e999", 0),
            (float("nan"), 0),
            (float("inf"), 0),
            (float("-inf"), 0),
            ("9" * 5000, 0),
        ],
    )
    def test_values(self, helpers, value, expected):
        result = helpers["to_number"](value)
        assert result == expected
        assert type(result) is type(expected) or isinstance(result, int | float)

    def test_the_result_is_always_a_finite_number(self, helpers):
        rng = random.Random(7)
        pieces = ["1", "-", ".", "e", "0", "x", " ", "nan", "inf", "٣", "_", "+", "9"]
        for _ in range(2000):
            text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 8)))
            result = helpers["to_number"](text)
            assert isinstance(result, int | float) and not isinstance(result, bool)
            assert math.isfinite(result)


class TestSame:
    @pytest.mark.parametrize(
        ("a", "b", "expected"),
        [
            ("5", 5, True),
            (5, "5", True),
            ("5.0", 5, True),
            ("a", "a", True),
            ("a", "b", False),
            ("5", "6", False),
            (None, 0, False),
            ("", 0, False),
            ([1], [1], True),
            ([1], [2], False),
            (True, 1, True),
            ("abc", 0, False),
        ],
    )
    def test_values(self, helpers, a, b, expected):
        assert helpers["same"](a, b) is expected

    def test_has_item_uses_the_same_rule(self, helpers):
        assert helpers["has_item"]([1, "two", 3.5], "1") is True
        assert helpers["has_item"]([1, "two", 3.5], "3.5") is True
        assert helpers["has_item"]([1, "two", 3.5], "two") is True
        assert helpers["has_item"]([1, "two", 3.5], "four") is False
        assert helpers["has_item"]("not a list", "n") is False
        assert helpers["has_item"]([], 1) is False


class TestListsAndText:
    def test_word_counts_from_one(self, helpers):
        word = helpers["word"]
        assert word("the quick brown fox", 1) == "the"
        assert word("the quick brown fox", "4") == "fox"
        assert word("the quick brown fox", 5) == ""
        assert word("the quick brown fox", 0) == ""
        assert word("the quick brown fox", -1) == ""
        assert word("  spaced   out  ", 2) == "out"
        assert word("", 1) == ""
        assert word("a b", 1.9) == "a"
        assert word("a b", 0.5) == ""  # a fraction below 1 is not a position (it must not count from the end)
        assert word("a b", -0.5) == ""

    def test_item_of_counts_from_one(self, helpers):
        item_of = helpers["item_of"]
        assert item_of(["a", "b", "c"], 1) == "a"
        assert item_of(["a", "b", "c"], "3") == "c"
        assert item_of(["a", "b", "c"], 4) == ""
        assert item_of(["a", "b", "c"], 0) == ""
        assert item_of(["a", "b", "c"], -1) == ""
        assert item_of("not a list", 1) == ""
        assert item_of([], 1) == ""
        assert item_of(["a", "b"], 1.9) == "a"
        assert item_of(["a", "b"], 0.5) == ""

    def test_delete_item_counts_from_one_and_ignores_a_missing_item(self, helpers):
        items = ["a", "b", "c"]
        helpers["delete_item"](items, 2)
        assert items == ["a", "c"]
        for number in (0, 5, -1, "x"):
            helpers["delete_item"](items, number)
        assert items == ["a", "c"]
        helpers["delete_item"]("not a list", 1)  # does nothing instead of failing
        helpers["delete_item"](items, 0.5)
        assert items == ["a", "c"]
        helpers["delete_item"](items, 1.9)
        assert items == ["c"]

    def test_random_item(self, helpers):
        assert helpers["random_item"]([]) == ""
        assert helpers["random_item"]("not a list") == ""
        assert {helpers["random_item"](["a", "b"]) for _ in range(100)} == {"a", "b"}

    def test_random_number_includes_both_ends_and_ignores_their_order(self, helpers):
        numbers = {helpers["random_number"](3, 1) for _ in range(300)}
        assert numbers == {1, 2, 3}
        assert helpers["random_number"](5, 5) == 5
        assert helpers["random_number"](1.9, 3.1) in (1, 2, 3)


class TestJsonGet:
    DATA = {"users": [{"name": "Ada", "tags": ["x", "y"]}, {"name": None}], "count": 2, "empty": "", "flag": False}

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("count", 2),
            ("users.0.name", "Ada"),
            ("users.0.tags.1", "y"),
            ("users.1.name", ""),  # null becomes empty text
            ("users.2.name", ""),  # no such item
            ("users.-1.name", ""),  # Python's own rule: -1 is the last item (which here has no name)
            ("nothing", ""),
            ("count.deeper", ""),
            ("users.x", ""),
            ("flag", False),
            ("empty", ""),
            ("users.0.tags", ["x", "y"]),
        ],
    )
    def test_paths(self, helpers, path, expected):
        assert helpers["json_get"](self.DATA, path) == expected

    def test_an_empty_path_is_the_whole_thing(self, helpers):
        assert helpers["json_get"](self.DATA, "") == self.DATA
        assert helpers["json_get"](self.DATA, "..") == self.DATA

    def test_weird_inputs_never_raise(self, helpers):
        for data in (None, 5, "text", [], {}, [1, 2], {"a": None}):
            for path in ("", "a", "a.b", "0", "-1", "9", 5, None, "..", "a..b"):
                helpers["json_get"](data, path)
