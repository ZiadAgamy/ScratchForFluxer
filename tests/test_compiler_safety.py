"""Whatever is typed into a block, the compiler writes text, numbers and names. It never writes code from it.

These tests throw awkward input at the compiler from every direction: text that tries to be Python, names that clash
with Python's own, numbers that aren't numbers, workspaces that are the wrong shape or far too big. Each result has
to be safe (`assert_sound`) and the text has to arrive exactly as it was typed.
"""

from __future__ import annotations

import ast
import copy
import time
import unicodedata

import pytest
from blocks import (
    block,
    command,
    hat,
    join,
    not_,
    on_message,
    on_ready,
    reply,
    set_var,
    show,
    var_id,
    variable,
    wait,
    workspace,
)
from fuzz_blocks import (
    HOSTILE_TEXT,
    NUMBER_FIELDS,
    STRIPPED_TEXT,
    UPLOADED,
    assert_sound,
    chaos_value,
    filled,
    parts_of,
    placed,
    string_literals,
)

from scratch_fluxer import catalogue, codecheck
from scratch_fluxer.compiler import (
    GENERATED_NAMES,
    JOIN_AS_LIST_FROM,
    MAX_BLOCKS,
    MAX_DEPTH,
    MAX_LOOP_NESTING,
    MAX_MESSAGES,
    MAX_NUMBER,
    RESERVED_NAMES,
    clean_text,
    compile_workspace,
)

AWKWARD = HOSTILE_TEXT + STRIPPED_TEXT


def label(value: object) -> str:
    try:
        return repr(value)[:28]
    except ValueError:  # a whole number with thousands of digits has no short text
        return "huge-number"


def structure(code: str) -> str:
    """The shape of the code with every piece of text blanked out."""

    class Blank(ast.NodeTransformer):
        def visit_Constant(self, node: ast.Constant) -> ast.Constant:
            return ast.Constant("") if isinstance(node.value, str) else node

    return ast.dump(Blank().visit(ast.parse(code)))


def compiled(ws, **kwargs):
    result = compile_workspace(ws, assets=kwargs.pop("assets", UPLOADED), **kwargs)
    assert_sound(result)
    return result


# --------------------------------------------------------------------------------------------- text


class TestTextStaysText:
    @pytest.mark.parametrize("words", AWKWARD, ids=label)
    def test_every_block_with_awkward_text_in_it_is_safe(self, words):
        for type_ in catalogue.BLOCKS:
            spec = catalogue.BLOCKS[type_]
            node = filled(type_, words=words)
            if type_ == "sff_bot_setup":
                ws = workspace(on_ready(), setup=False)
                ws["blocks"]["blocks"].insert(0, node)
            else:
                ws = placed(type_, node)
            result = compiled(ws)
            if spec["shape"] != "config" and type_ != "sff_event_command":
                assert result.ok, (type_, [d.message for d in result.diagnostics])

    TEXT_BLOCKS = [
        "sff_reply",
        "sff_send_here",
        "sff_console_print",
        "sff_console_log",
        "sff_set_custom_status",
        "sff_dm_author",
        "sff_send_embed",
        "sff_reply_embed",
        "sff_send_channel",
    ]

    @pytest.mark.parametrize("type_", TEXT_BLOCKS)
    @pytest.mark.parametrize("words", AWKWARD, ids=label)
    def test_the_text_arrives_exactly_as_typed_and_the_code_around_it_does_not_change(self, type_, words):
        result = compiled(placed(type_, filled(type_, words=words)))
        assert result.ok
        assert clean_text(words) in string_literals(result.code)
        baseline = compiled(placed(type_, filled(type_, words="plain")))
        assert structure(result.code) == structure(baseline.code)

    @pytest.mark.parametrize("words", AWKWARD, ids=label)
    def test_text_joined_to_other_things(self, words):
        pieces = [join(words, "!"), join("<", join(words, ">")), join(variable("a"), words), join(words, words)]
        for piece in pieces:
            result = compiled(workspace(on_ready(show(piece)), variables=("a",)))
            assert result.ok
            assert any(clean_text(words) in text for text in string_literals(result.code)) or not clean_text(words)

    @pytest.mark.parametrize("words", AWKWARD, ids=label)
    def test_the_prefix_is_text_too(self, words):
        ws = workspace(on_ready(), setup=False)
        ws["blocks"]["blocks"].insert(0, block("sff_bot_setup", fields={"PREFIX": words}, id="setup"))
        result = compiled(ws)
        prefix = clean_text(words, 200).replace("\n", "").replace("\t", " ")[:8]
        expected = prefix if prefix.strip() else "!"
        call = next(n for n in ast.walk(ast.parse(result.code)) if isinstance(n, ast.Call) and n.keywords)
        assert call.keywords[0].arg == "command_prefix"
        assert ast.literal_eval(call.keywords[0].value) == expected

    def test_control_characters_and_direction_overrides_are_removed(self):
        result = compiled(workspace(on_ready(show("a\x00b\x1b[31m‮evil⁦")), variables=()))
        assert string_literals(result.code) >= {"ab[31mevil"}
        assert "\x00" not in result.code and "\x1b" not in result.code and "‮" not in result.code

    def test_lone_surrogates_become_the_replacement_character(self):
        result = compiled(workspace(on_ready(show("a\ud800b"))))
        assert "a�b" in string_literals(result.code)

    def test_text_longer_than_a_box_is_cut_not_refused(self):
        result = compiled(workspace(on_ready(show("y" * 5000))))
        assert result.ok
        assert max(len(text) for text in string_literals(result.code)) <= 2000

    def test_line_separators_are_written_as_escapes(self):
        result = compiled(workspace(on_ready(show("a b c\nd"))))
        assert " " not in result.code and " " not in result.code
        assert "a b c\nd" in string_literals(result.code)


# --------------------------------------------------------------------------------------------- names

HOSTILE_NAMES = [
    "x; import os",
    "__import__('os')",
    "__class__",
    "__builtins__",
    "__file__",
    "class",
    "None",
    "True",
    "lambda",
    "async",
    "await",
    "match",
    "print",
    "len",
    "open",
    "exec",
    "eval",
    "bot",
    "fluxer",
    "os",
    "json",
    "random",
    "asyncio",
    "log",
    "logging",
    "presence",
    "update_presence",
    "to_number",
    "same",
    "on_ready",
    "on_message",
    "ctx",
    "message",
    "member",
    "text",
    "channel",
    "embed",
    "user",
    "data",
    "x",
    "ASSETS",
    "Path",
    "é",
    "日本語",
    "😀",
    "a b",
    "a-b",
    "  ",
    "",
    "1abc",
    "_",
    "__",
    "a" * 300,
    "ＡＢＣ",  # full-width letters fold to plain ones
    "ǅ",
    "x​y",
    "‮evil",
    'quote"name',
    "back\\slash",
    "line\nbreak",
]


class TestNames:
    @pytest.mark.parametrize("name", HOSTILE_NAMES, ids=label)
    def test_a_variable_name_becomes_a_safe_python_name(self, name):
        ws = workspace(on_ready(set_var(name, 1), show(variable(name))), variables=(name,))
        result = compiled(ws)
        assert result.ok
        tree = ast.parse(result.code)
        module_names = [t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets]
        assert module_names.count("bot") == 1
        mine = [n for n in module_names if n != "bot"]
        assert len(mine) == 1
        ident = mine[0]
        assert ident.isidentifier() and not ident.startswith("__")
        assert ident not in RESERVED_NAMES | GENERATED_NAMES or ident == "variable"
        codecheck.check(result.code)

    def test_variables_with_the_same_name_get_different_names(self):
        ws = workspace(
            on_ready(set_var("Score", 1), set_var("score", 2), set_var("SCORE ", 3), show(variable("Score"))),
            variables=("Score", "score", "SCORE "),
        )
        result = compiled(ws)
        names = [t.id for n in ast.parse(result.code).body if isinstance(n, ast.Assign) for t in n.targets]
        assert sorted(n for n in names if n != "bot") == ["score", "score_2", "score_3"]

    @pytest.mark.parametrize("name", HOSTILE_NAMES, ids=label)
    def test_a_command_name_becomes_a_safe_function_and_command(self, name):
        result = compiled(workspace(command(name, reply("x"))))
        cleaned = "".join(
            ch for ch in " ".join(clean_text(name).split()) if ch in "_- " or unicodedata.category(ch)[0] in "LNM"
        )
        if not cleaned.strip():
            assert not result.ok  # "Give this command a name."
            return
        assert result.ok
        functions = [n for n in ast.parse(result.code).body if isinstance(n, ast.AsyncFunctionDef)]
        assert len(functions) == 1
        assert functions[0].name.isidentifier() and not functions[0].name.startswith("__")
        assert functions[0].name not in RESERVED_NAMES | GENERATED_NAMES
        decorator = functions[0].decorator_list[0]
        if isinstance(decorator, ast.Call) and decorator.keywords:
            registered = ast.literal_eval(decorator.keywords[0].value)
            assert all(ch in "_- " or unicodedata.category(ch)[0] in "LNM" for ch in registered)

    def test_many_commands_with_the_same_name_do_not_clash_or_take_long(self):
        ws = workspace(*[command("ping") for _ in range(3000)])
        started = time.perf_counter()
        result = compiled(ws)
        assert result.ok and time.perf_counter() - started < 5
        names = [n.name for n in ast.parse(result.code).body if isinstance(n, ast.AsyncFunctionDef)]
        assert len(names) == len(set(names)) == 3000
        assert len(result.diagnostics) <= 2 * MAX_MESSAGES + 1  # the warnings are capped
        assert any("more messages" in d.message for d in result.diagnostics)

    def test_a_command_named_like_a_variable_does_not_clash(self):
        ws = workspace(command("score", reply(variable("score"))), variables=("score",))
        result = compiled(ws)
        assert result.ok
        names = [n.name for n in ast.parse(result.code).body if isinstance(n, ast.AsyncFunctionDef)]
        assert names != ["score"]


# --------------------------------------------------------------------------------------------- menus, numbers and files

BAD_CHOICES = ["zzz", "", 5, None, ["x"], {"a": 1}, True, 'add"; import os', "ADD", " add"]


class TestChoicesNumbersAndFiles:
    @pytest.mark.parametrize("choice", BAD_CHOICES, ids=label)
    def test_a_menu_that_holds_something_else_falls_back_to_its_default(self, choice):
        for type_, spec in catalogue.BLOCKS.items():
            menus = [part["menu"] for part in parts_of(spec) if "menu" in part]
            if not menus:
                continue
            node = filled(type_)
            for name in menus:
                node["fields"][name] = copy.deepcopy(choice)
            result = compiled(placed(type_, node))
            assert result.ok, (type_, [d.message for d in result.diagnostics])
            assert any("isn't one of the choices" in d.message for d in result.diagnostics), type_

    @pytest.mark.parametrize("value", NUMBER_FIELDS, ids=label)
    def test_a_number_that_is_not_a_usable_number(self, value):
        for type_, spec in catalogue.BLOCKS.items():
            numbers = [part["number"] for part in parts_of(spec) if "number" in part]
            if not numbers:
                continue
            node = filled(type_)
            for name in numbers:
                node["fields"][name] = copy.deepcopy(value)
            assert compiled(placed(type_, node)).ok

    @pytest.mark.parametrize("value", NUMBER_FIELDS, ids=label)
    def test_a_number_box_in_a_socket(self, value):
        number_box = block("sff_number", fields={"NUM": copy.deepcopy(value)})
        for node in (
            block("sff_wait", inputs={"SECONDS": number_box}),
            block("sff_repeat", inputs={"TIMES": number_box, "DO": [show("x")]}),
            show(block("sff_math", fields={"OP": "divide"}, inputs={"A": number_box, "B": 1})),
            show(block("sff_random", inputs={"A": number_box, "B": number_box})),
            show(block("sff_round", fields={"OP": "round"}, inputs={"N": number_box})),
        ):
            assert compiled(workspace(on_ready(node))).ok

    def test_numbers_beyond_the_limit_are_replaced_with_a_warning(self):
        too_big = block("sff_number", fields={"NUM": MAX_NUMBER + 1})
        result = compiled(workspace(on_ready(block("sff_wait", inputs={"SECONDS": too_big}))))
        assert "sleep(0)" in result.code
        assert any("too big" in d.message for d in result.diagnostics)
        just_fits = block("sff_number", fields={"NUM": MAX_NUMBER})
        result = compiled(workspace(on_ready(block("sff_wait", inputs={"SECONDS": just_fits}))))
        assert f"sleep({MAX_NUMBER})" in result.code

    @pytest.mark.parametrize(
        "name",
        [
            "../secret.txt",
            "/etc/passwd",
            "a/b.png",
            "a\\b.png",
            "..",
            "",
            ".png",
            "x.py",
            "x.svg",
            "x.PNG.exe",
            "con.png",
            "a\x00.png",
            "a" * 200 + ".png",
            5,
            None,
            ["cat.png"],
            {"x": 1},
            "cat.png ",
            " cat.png",
            "ca‮t.png",
        ],
        ids=label,
    )
    def test_a_file_name_that_is_not_a_plain_name_is_an_error_and_never_reaches_the_code(self, name):
        node = block("sff_reply_image", fields={"FILE": name})
        result = compiled(workspace(on_message(node)), assets=UPLOADED)
        if result.ok:  # only "cat.png " and " cat.png" can be tidied up into the real name
            assert string_literals(result.code) >= {"cat.png"}
        else:
            assert any(d.level == "error" for d in result.diagnostics)
        assert "passwd" not in result.code and "secret" not in result.code and ".." not in result.code

    def test_file_names_are_checked_against_the_uploads_and_their_kind(self):
        ws = workspace(on_message(block("sff_reply_image", fields={"FILE": "notes.txt"})))
        result = compile_workspace(ws, assets=UPLOADED)
        assert not result.ok and any("text file" in d.message for d in result.diagnostics)
        result = compile_workspace(ws, assets={})
        assert not result.ok and any("isn't uploaded" in d.message for d in result.diagnostics)


# --------------------------------------------------------------------------------------------- the wrong shape

BLOCK_DAMAGE = [
    {},
    {"type": None},
    {"type": 5},
    {"type": ["sff_reply"]},
    {"type": {"x": 1}},
    {"type": ""},
    {"type": "x" * 500},
    {"type": "sff_reply", "fields": []},
    {"type": "sff_reply", "fields": "x"},
    {"type": "sff_reply", "fields": {"TEXT": ["x"]}},
    {"type": "sff_reply", "inputs": []},
    {"type": "sff_reply", "inputs": "x"},
    {"type": "sff_reply", "inputs": {"TEXT": 5}},
    {"type": "sff_reply", "inputs": {"TEXT": []}},
    {"type": "sff_reply", "inputs": {"TEXT": {}}},
    {"type": "sff_reply", "inputs": {"TEXT": {"block": 5}}},
    {"type": "sff_reply", "inputs": {"TEXT": {"block": None, "shadow": None}}},
    {"type": "sff_reply", "inputs": {"TEXT": {"shadow": {"type": 5}}}},
    {"type": "sff_reply", "inputs": {"TEXT": {"block": {"type": "sff_reply"}}}},
    {"type": "sff_reply", "inputs": {5: {"block": {"type": "sff_text"}}}},
    {"type": "sff_reply", "next": []},
    {"type": "sff_reply", "next": "x"},
    {"type": "sff_reply", "next": {"block": 5}},
    {"type": "sff_reply", "next": {"block": []}},
    {"type": "sff_reply", "next": {"block": {"type": "sff_event_ready"}}},
    {"type": "sff_reply", "x": float("nan"), "y": float("inf")},
    {"type": "sff_reply", "x": 10**400, "y": -(10**400)},
    {"type": "sff_reply", "x": "a", "y": True},
    {"type": "sff_reply", "x": [], "y": {}},
    {"type": "sff_reply", "id": 5},
    {"type": "sff_reply", "id": None},
    {"type": "sff_reply", "id": ""},
    {"type": "sff_reply", "id": "x" * 500},
    {"type": "sff_reply", "id": "same"},
    {"type": "sff_reply", "enabled": "false"},
    {"type": "sff_reply", "enabled": 0},
    {"type": "sff_reply", "disabledReasons": []},
    {"type": "sff_reply", "disabledReasons": ["x"]},
    {"type": "sff_event_ready", "next": {"block": {"type": "sff_reply", "id": "same"}}},
    {"type": "sff_set_variable", "fields": {"VAR": {"id": 5}}},
    {"type": "sff_set_variable", "fields": {"VAR": {"id": {"x": 1}}}},
    {"type": "sff_set_variable", "fields": {"VAR": []}},
    {"type": "sff_set_variable", "fields": {"VAR": "no such variable"}},
    {"type": "sff_set_variable", "fields": {"VAR": "a"}},
    {"type": "sff_event_command", "fields": {"NAME": None}},
    {"type": "sff_event_command", "fields": {"NAME": ["a"]}},
    {"type": "sff_event_command", "fields": {"NAME": 12345}},
    {"type": "sff_bot_setup", "fields": {"PREFIX": None}},
    {"type": "sff_bot_setup", "fields": {"PREFIX": ["!"]}},
]

STATE_DAMAGE = [
    None,
    [],
    "text",
    b"bytes",
    42,
    4.5,
    True,
    {},
    {"blocks": None},
    {"blocks": []},
    {"blocks": "x"},
    {"blocks": 5},
    {"blocks": {}},
    {"blocks": {"blocks": None}},
    {"blocks": {"blocks": "x"}},
    {"blocks": {"blocks": {}}},
    {"blocks": {"blocks": [None, 1, "x", [], {}, True, 4.5]}},
    {"blocks": {"blocks": [[{"type": "sff_event_ready"}]]}},
    {"variables": "x", "blocks": {"blocks": []}},
    {"variables": {"a": 1}, "blocks": {"blocks": []}},
    {
        "variables": [
            None,
            1,
            "x",
            [],
            {},
            {"id": 5},
            {"id": "a"},
            {"id": "a", "name": 5},
            {"id": "a", "name": "n", "type": 5},
        ]
    },
    {"variables": [{"id": f"v{n}", "name": "same"} for n in range(5000)], "blocks": {"blocks": []}},
]


def with_damage(node):
    """The damaged block in each of the places a block can be."""
    base = workspace(on_message(reply("ok")))
    top = copy.deepcopy(base)
    top["blocks"]["blocks"].append(copy.deepcopy(node))
    chain = workspace(on_message(reply("ok")))
    chain["blocks"]["blocks"][1]["next"]["block"]["next"] = {"block": copy.deepcopy(node)}
    socket = workspace(on_message(reply("ok")))
    socket["blocks"]["blocks"][1]["next"]["block"]["inputs"] = {"TEXT": {"block": copy.deepcopy(node)}}
    hatted = workspace(on_message(reply("ok")))
    hatted["blocks"]["blocks"].append({"type": "sff_event_ready", "id": "h2", "next": {"block": copy.deepcopy(node)}})
    return [top, chain, socket, hatted]


class TestDamagedWorkspaces:
    @pytest.mark.parametrize("state", STATE_DAMAGE, ids=label)
    def test_a_workspace_of_the_wrong_shape(self, state):
        result = compile_workspace(state)
        assert_sound(result)
        assert isinstance(result.diagnostics, list)

    @pytest.mark.parametrize("node", BLOCK_DAMAGE, ids=label)
    def test_a_damaged_block_anywhere(self, node):
        for state in with_damage(node):
            assert_sound(compile_workspace(state, assets=UPLOADED))

    def test_a_workspace_that_is_not_a_dict_is_an_error(self):
        for state in (None, [], "x", 5):
            assert not compile_workspace(state).ok

    def test_blocks_that_point_back_at_themselves(self):
        loop = {"type": "sff_console_print", "id": "loop"}
        loop["next"] = {"block": loop}
        result = compile_workspace(workspace(on_ready(), loop))
        assert not result.ok and any("more than" in d.message for d in result.diagnostics)

        inside = {"type": "sff_console_print", "id": "inside"}
        inside["inputs"] = {"TEXT": {"block": inside}}
        result = compile_workspace(workspace(on_ready(inside)))
        assert not result.ok and any("nested too deeply" in d.message for d in result.diagnostics)

    def test_the_same_block_used_twice(self):
        shared = show("shared")
        ws = workspace(on_ready(shared), on_message(shared))
        assert_sound(compile_workspace(ws))

    def test_a_pile_of_random_values_everywhere(self):
        import random

        rng = random.Random(5)
        for _ in range(200):
            state = {
                "blocks": {"blocks": [chaos_value(rng) for _ in range(rng.randint(0, 4))]},
                "variables": chaos_value(rng),
            }
            assert_sound(compile_workspace(state))

    def test_disabled_blocks_write_nothing(self):
        ws = workspace(on_ready(show("kept"), show("dropped")))
        ws["blocks"]["blocks"][1]["next"]["block"]["next"]["block"]["enabled"] = False
        result = compiled(ws)
        assert "kept" in result.code and "dropped" not in result.code
        for reasons in ({"disabledReasons": ["user"]}, {"disabled": True}):
            ws = workspace(on_ready(show("kept")))
            ws["blocks"]["blocks"][1]["next"]["block"].update(reasons)
            assert "kept" not in compiled(ws).code


# --------------------------------------------------------------------------------------------- limits


def run_of_waits(count: int):
    return workspace(on_ready(*[block("sff_wait") for _ in range(count)]))


def nested_nots(levels: int):
    inner = block("sff_boolean", fields={"BOOL": "true"})
    for _ in range(levels):
        inner = not_(inner)
    return workspace(on_ready(block("sff_if", inputs={"COND": inner, "DO": [show("x")]})))


def nested_loops(levels: int):
    inner = show("x")
    for _ in range(levels):
        inner = block("sff_repeat", inputs={"TIMES": 1, "DO": [inner]})
    return workspace(on_ready(inner))


class TestLimits:
    def test_the_number_of_blocks(self):
        # the two locked blocks and the event block count too
        assert compiled(run_of_waits(MAX_BLOCKS - 3)).ok
        result = compile_workspace(run_of_waits(MAX_BLOCKS - 2))
        assert not result.ok and any("more than" in d.message for d in result.diagnostics)
        assert result.code == ""

    def test_how_deeply_blocks_may_sit_inside_each_other(self):
        assert compiled(nested_nots(MAX_DEPTH - 1)).ok
        result = compile_workspace(nested_nots(MAX_DEPTH))
        assert not result.ok and any("nested too deeply" in d.message for d in result.diagnostics)

    def test_loops_inside_loops(self):
        result = compiled(nested_loops(MAX_LOOP_NESTING))
        assert result.ok and compile(result.code, "<bot>", "exec")
        result = compile_workspace(nested_loops(MAX_LOOP_NESTING + 1))
        assert not result.ok and any("loops are nested too deeply" in d.message.lower() for d in result.diagnostics)
        assert_sound(result)

    def test_if_blocks_nest_as_deep_as_blocks_may(self):
        inner = show("x")
        for _ in range(MAX_DEPTH - 2):
            inner = block("sff_if", inputs={"COND": True, "DO": [inner]})
        result = compiled(workspace(on_ready(inner)))
        assert result.ok

    def test_a_join_of_hundreds_of_pieces_stays_flat(self):
        def piece():  # not a plain name, so the join can't be written as an f-string
            return block("sff_text_length", inputs={"TEXT": variable("a")})

        def tree(levels: int):
            return piece() if levels == 0 else join(tree(levels - 1), tree(levels - 1))

        for levels in (3, 4, 6, 8):
            result = compiled(workspace(on_ready(show(join(tree(levels), "!"))), variables=("a",)))
            assert result.ok
            assert result.code.count("len(") == 2**levels
        assert '"".join([' in result.code
        small = compiled(workspace(on_ready(show(join(join(piece(), "x"), piece()))), variables=("a",)))
        assert '"".join' not in small.code
        assert JOIN_AS_LIST_FROM >= 3

    def test_the_whole_thing_is_quick_even_when_it_is_big(self):
        started = time.perf_counter()
        compiled(run_of_waits(MAX_BLOCKS - 3))
        assert time.perf_counter() - started < 5

    def test_a_giant_text_box_is_cut_quickly(self):
        started = time.perf_counter()
        result = compiled(workspace(on_ready(show("z" * 3_000_000))))
        assert result.ok and time.perf_counter() - started < 2

    def test_code_that_would_be_too_long_is_refused(self):
        words = "q" * 1900
        ws = workspace(on_ready(*[show(words) for _ in range(300)]))
        result = compile_workspace(ws)
        assert not result.ok and any("too long" in d.message for d in result.diagnostics)

    def test_waiting_in_a_loop_is_not_needed_for_the_code_to_be_valid(self):
        ws = workspace(on_ready(block("sff_forever", inputs={"DO": [show("x")]})))
        result = compiled(ws)
        assert result.ok and any("no wait block" in d.message for d in result.diagnostics)
        ws = workspace(on_ready(block("sff_forever", inputs={"DO": [wait(1)]})))
        assert not any("no wait block" in d.message for d in compiled(ws).diagnostics)


# --------------------------------------------------------------------------------------------- the token


class TestTheTokenIsNeverInTheCode:
    TOKENISH = [
        "os.environ['FLUXER_TOKEN']",
        'os.environ["FLUXER_TOKEN"]',
        "FLUXER_TOKEN",
        "bot.run(os.environ['FLUXER_TOKEN'])",
        "{os.environ}",
    ]

    @pytest.mark.parametrize("words", TOKENISH, ids=label)
    def test_text_that_names_the_token_stays_text(self, words):
        ws = workspace(
            on_message(reply(words), show(join("token: ", words))),
            command(words, reply(words)),
            hat("sff_event_member_join", block("sff_dm_member", inputs={"TEXT": words})),
        )
        result = compiled(ws)
        assert result.ok
        tree = ast.parse(result.code)
        reads = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "environ"]
        assert len(reads) == 1  # the login, and nothing else
        assert clean_text(words) in string_literals(result.code)  # ...and the text itself is just text

    def test_no_block_can_read_the_environment(self):
        for type_, spec in catalogue.BLOCKS.items():
            for part in parts_of(spec):
                assert "env" not in str(part.get("default", "")).lower(), type_
        blob = repr(catalogue.browser_payload())
        assert "FLUXER_TOKEN" not in blob and "environ" not in blob

    def test_variables_cannot_be_called_environ_or_os(self):
        for name in ("os", "environ", "OS", "os.environ"):
            ws = workspace(on_ready(set_var(name, "x"), show(variable(name))), variables=(name,))
            result = compiled(ws)
            assert result.ok
            tree = ast.parse(result.code)
            assert len([n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "environ"]) == 1
            assert var_id(name)
