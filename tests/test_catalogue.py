"""The block catalogue: every block is described once, completely, and the compiler covers exactly those blocks."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scratch_fluxer import catalogue, compiler

ROOT = Path(__file__).resolve().parent.parent
SHAPES = {"hat", "statement", "cap", "value", "boolean", "literal", "config"}
RETURNS = {"text", "number", "boolean", "list", "any"}
NEEDS = {"message", "command", "member"}
PART_KEYS = ("input", "field", "number", "menu", "var", "asset", "statements")


def parts(spec):
    return [part for line in spec["lines"] for part in line if isinstance(part, dict)]


def part_name(part):
    return next(part[key] for key in PART_KEYS if key in part)


class TestSpecs:
    def test_there_are_blocks_for_every_category(self):
        for category in catalogue.CATEGORY_ORDER:
            assert catalogue.TOOLBOX[category], f"{category} has no blocks"

    @pytest.mark.parametrize("type_", sorted(catalogue.BLOCKS))
    def test_each_spec_is_complete(self, type_):
        spec = catalogue.BLOCKS[type_]
        assert type_.startswith("sff_")
        assert spec["shape"] in SHAPES
        assert spec["tooltip"].endswith((".", "!", "?")) or spec["tooltip"][-1].isalnum()
        assert spec["lines"], "a block needs at least one row"
        assert set(spec["needs"] or ()) <= NEEDS
        if spec["shape"] in ("value", "boolean", "literal"):
            assert spec["returns"] in RETURNS
        else:
            assert spec["returns"] is None
        if spec["shape"] == "boolean":
            assert spec["returns"] == "boolean"
        if spec["category"] is None:
            assert spec["shape"] in ("config", "literal")
        else:
            assert spec["category"] in catalogue.CATEGORY_ORDER

    @pytest.mark.parametrize("type_", sorted(catalogue.BLOCKS))
    def test_part_names_are_unique_and_upper_case(self, type_):
        names = [part_name(part) for part in parts(catalogue.BLOCKS[type_])]
        assert len(names) == len(set(names))
        assert all(re.fullmatch(r"[A-Z][A-Z_]*", name) for name in names), names

    @pytest.mark.parametrize("type_", sorted(catalogue.BLOCKS))
    def test_menus_have_a_valid_default(self, type_):
        for part in parts(catalogue.BLOCKS[type_]):
            if "menu" in part:
                assert part["default"] in [option[1] for option in part["options"]]
                assert len({option[1] for option in part["options"]}) == len(part["options"])

    def test_every_asset_part_names_known_kinds(self):
        for spec in catalogue.BLOCKS.values():
            for part in parts(spec):
                if "asset" in part:
                    assert part["kinds"] and set(part["kinds"]) <= set(catalogue.ASSET_KINDS)

    def test_hats_have_no_previous_connection_and_caps_no_next(self):
        for type_ in catalogue.HATS:
            definition = catalogue.blockly_definition(catalogue.BLOCKS[type_])
            assert "previousStatement" not in definition
            assert "nextStatement" in definition
            assert definition["hat"] == "cap"
        for spec in catalogue.BLOCKS.values():
            if spec["shape"] == "cap":
                definition = catalogue.blockly_definition(spec)
                assert "previousStatement" in definition and "nextStatement" not in definition

    def test_the_locked_blocks_are_config_blocks(self):
        assert set(catalogue.LOCKED_TYPES) == {"sff_bot_setup", "sff_bot_login"}
        for type_ in catalogue.LOCKED_TYPES:
            assert catalogue.BLOCKS[type_]["shape"] == "config"

    def test_embed_colours_are_valid_and_match_the_menu(self):
        names = [name for _label, name, _rgb in catalogue.EMBED_COLORS]
        assert [option[1] for option in catalogue.EMBED_COLOR_OPTIONS] == names
        assert all(0 <= rgb <= 0xFFFFFF for _label, _name, rgb in catalogue.EMBED_COLORS)


class TestToolbox:
    def test_every_block_is_offered_once_except_the_literals_and_locked_ones(self):
        offered = [type_ for types in catalogue.TOOLBOX.values() for type_ in types]
        assert len(offered) == len(set(offered))
        expected = {
            type_
            for type_, spec in catalogue.BLOCKS.items()
            if spec["category"] is not None and spec["shape"] != "literal"
        }
        assert set(offered) == expected

    def test_blocks_that_use_a_variable_say_so(self):
        entry = catalogue.toolbox_entry("sff_set_variable")
        assert entry["variable"] == {"field": "VAR", "kinds": [""], "each": False}
        assert catalogue.toolbox_entry("sff_variable")["variable"]["each"] is True
        assert catalogue.toolbox_entry("sff_list_variable")["variable"] == {
            "field": "LIST",
            "kinds": ["List"],
            "each": True,
        }
        assert "variable" not in catalogue.toolbox_entry("sff_reply")

    def test_sockets_start_with_an_editable_box(self):
        entry = catalogue.toolbox_entry("sff_reply")
        assert entry["inputs"]["TEXT"] == {"shadow": {"type": "sff_text", "fields": {"TEXT": "Pong!"}}}
        entry = catalogue.toolbox_entry("sff_repeat")
        assert entry["inputs"]["TIMES"] == {"shadow": {"type": "sff_number", "fields": {"NUM": 10}}}
        assert "inputs" not in catalogue.toolbox_entry("sff_event_ready")
        assert "COND" not in catalogue.toolbox_entry("sff_if").get("inputs", {})  # conditions start empty

    def test_the_two_flyout_buttons(self):
        assert set(catalogue.FLYOUT_BUTTONS) == {"variables", "lists"}


class TestBlocklyDefinitions:
    @pytest.mark.parametrize("type_", sorted(catalogue.BLOCKS))
    def test_messages_and_args_line_up(self, type_):
        definition = catalogue.blockly_definition(catalogue.BLOCKS[type_])
        index = 0
        while f"message{index}" in definition:
            placeholders = re.findall(r"%(\d+)", definition[f"message{index}"].replace("%%", ""))
            args = definition.get(f"args{index}", [])
            assert placeholders == [str(n) for n in range(1, len(args) + 1)]
            index += 1
        assert index == len(catalogue.BLOCKS[type_]["lines"])

    def test_percent_signs_in_labels_are_escaped(self):
        spec = catalogue.block("sff_test", "bot", "statement", ["100% sure"], tooltip="Test.")
        assert catalogue.blockly_definition(spec)["message0"] == "100%% sure"

    def test_value_blocks_have_typed_outputs(self):
        assert catalogue.blockly_definition(catalogue.BLOCKS["sff_math"])["output"] == ["Number"]
        assert catalogue.blockly_definition(catalogue.BLOCKS["sff_join"])["output"] == ["String"]
        assert catalogue.blockly_definition(catalogue.BLOCKS["sff_compare"])["output"] == ["Boolean"]
        assert catalogue.blockly_definition(catalogue.BLOCKS["sff_variable"])["output"] is None  # plugs in anywhere

    def test_the_payload_is_json_and_holds_no_secrets(self):
        payload = catalogue.browser_payload()
        blob = json.dumps(payload)
        assert len(payload["blocks"]) == len(catalogue.BLOCKS)
        assert "FLUXER_TOKEN" not in blob
        assert len(blob) < 60_000


class TestColours:
    def test_category_colours_match_the_css_tokens(self):
        css = (ROOT / "public" / "static" / "css" / "app.css").read_text(encoding="utf-8")
        for key, info in catalogue.CATEGORIES.items():
            match = re.search(rf"--cat-{key}:\s*(#[0-9a-fA-F]{{6}})", css)
            assert match, f"--cat-{key} is missing from app.css"
            assert match.group(1).lower() == info["color"].lower()
        assert re.search(rf"--ink-on-block:\s*{catalogue.INK}", css, re.I)


class TestCompilerCoverage:
    def test_every_block_has_code_to_write(self):
        statements = {t for t, s in catalogue.BLOCKS.items() if s["shape"] in ("statement", "cap")}
        values = {t for t, s in catalogue.BLOCKS.items() if s["shape"] in ("value", "boolean", "literal")}
        assert set(compiler.STATEMENTS) == statements
        assert set(compiler.VALUES) == values
        handled = set(compiler.STATEMENTS) | set(compiler.VALUES) | compiler.HAT_TYPES | set(catalogue.LOCKED_TYPES)
        assert handled == set(catalogue.BLOCKS)

    def test_every_hat_is_a_script_the_compiler_knows(self):
        assert set(catalogue.HATS) == compiler.HAT_TYPES

    def test_helper_sources_only_need_helpers_that_exist(self):
        for name, helper in compiler.HELPERS.items():
            assert set(helper["needs"]) <= set(compiler.HELPERS), name
            assert name in compiler.HELPER_ORDER
        order = compiler.HELPER_ORDER
        for name, helper in compiler.HELPERS.items():
            assert all(order.index(need) < order.index(name) for need in helper["needs"]), name


class TestLandingExamples:
    """``catalogue.example`` is how the landing page draws a block: the real words, sockets and starting values."""

    @pytest.mark.parametrize("type_", sorted(catalogue.BLOCKS))
    def test_every_block_can_be_drawn_with_its_own_words(self, type_):
        drawn = catalogue.example(type_)
        spec = catalogue.BLOCKS[type_]
        words = [word for line in spec["lines"] for word in line if isinstance(word, str)]
        assert [part for part in drawn["parts"] if isinstance(part, str)] == words
        sockets = [
            part for line in spec["lines"] for part in line if isinstance(part, dict) and "statements" not in part
        ]
        assert len([part for part in drawn["parts"] if not isinstance(part, str)]) == len(sockets)
        assert bool(drawn.get("hat")) == (spec["shape"] == "hat")
        assert bool(drawn.get("round")) == (spec["shape"] in ("value", "boolean"))

    def test_a_value_typed_into_a_socket_is_shown(self):
        drawn = catalogue.example("sff_reply", TEXT="Hello there")
        assert drawn["parts"] == ["reply with", ("field", "Hello there")]
        assert catalogue.example("sff_event_command", NAME="hi")["parts"][1] == ("field", "hi")

    def test_a_value_for_something_the_block_does_not_have_is_an_error(self):
        with pytest.raises(ValueError, match="no TXT"):
            catalogue.example("sff_reply", TXT="oops")
