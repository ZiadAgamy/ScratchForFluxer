"""What the compiler writes for each block, how it lays the file out, and what it says about mistakes."""

from __future__ import annotations

import ast

import bot_harness
import pytest
from blocks import (
    block,
    change_var,
    command,
    command_text,
    compare,
    forever,
    if_,
    if_else,
    join,
    list_variable,
    logic,
    math_op,
    message_text,
    not_,
    on_join,
    on_leave,
    on_message,
    on_ready,
    repeat,
    reply,
    say,
    set_var,
    show,
    text,
    var_id,
    variable,
    wait,
    workspace,
)
from demo_python import DEMO_PYTHON

from scratch_fluxer import codecheck
from scratch_fluxer.compiler import compile_workspace, default_workspace

HATS = {
    "ready": (on_ready, "on_ready"),
    "message": (on_message, "on_message"),
    "command": (lambda *steps: command("t", *steps), "t"),
    "join": (on_join, "on_member_join"),
    "leave": (on_leave, "on_member_remove"),
}


def compiled(*top, variables=(), assets=None):
    """Compile a workspace. Every test also proves that what came out passes the safety check."""
    result = compile_workspace(workspace(*top, variables=variables), assets=assets)
    if result.ok:  # code with errors never runs, so only working code has to pass
        codecheck.check(result.code)
    return result


def function_body(code: str, name: str) -> list[str]:
    tree = ast.parse(code)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name)
    return [line[4:] for line in code.splitlines()[node.lineno : node.end_lineno]]


def user_lines(body: list[str]) -> list[str]:
    """The body without the lines every script of that kind starts with (global, the bot guard, the member)."""
    lines = list(body)
    if lines and lines[0].startswith("global "):
        lines = lines[1:]
    if lines and lines[0].startswith("# Ignore bots"):
        lines = lines[3:]
    if lines and lines[0].startswith("member = fluxer.GuildMember"):
        lines = lines[1:]
    return lines


def script(*steps, hat="message", variables=(), assets=None):
    make, func = HATS[hat]
    result = compiled(make(*steps), variables=variables, assets=assets)
    return result, user_lines(function_body(result.code, func))


def messages(result, level=None):
    return [d.message for d in result.diagnostics if level in (None, d.level)]


def asset_block(type_, name, **inputs):
    return block(type_, fields={"FILE": name}, inputs=inputs or None)


def lst(name):
    return {"id": var_id(name)}


# --------------------------------------------------------------------------------------------- the starting workspace


class TestStartingWorkspace:
    def test_it_writes_exactly_what_the_landing_page_shows(self):
        result = compile_workspace(default_workspace())
        assert result.ok
        assert result.code == DEMO_PYTHON

    def test_it_follows_the_demo_values(self):
        result = compile_workspace(default_workspace(prefix="?", ready="Hello", command="hi", reply="Hey"))
        assert 'command_prefix="?"' in result.code
        assert 'print("Hello")' in result.code
        assert "async def hi(ctx):" in result.code
        assert 'await ctx.reply("Hey")' in result.code

    def test_the_lines_say_which_block_wrote_what(self):
        result = compile_workspace(default_workspace())
        lines = DEMO_PYTHON.splitlines()
        assert result.lines["setup"] == [5, 8]
        assert lines[result.lines["setup"][0] - 1] == "bot = fluxer.Bot("
        assert result.lines["ready"] == [11, 13]
        assert result.lines["print"] == [13, 13]
        assert result.lines["command"] == [16, 18]
        assert result.lines["reply"] == [18, 18]
        assert result.lines["login"] == [21, 21]

    def test_compiling_twice_gives_the_same_file(self):
        assert compile_workspace(default_workspace()).code == compile_workspace(default_workspace()).code

    def test_the_result_is_plain_data(self):
        data = compile_workspace(default_workspace()).as_dict()
        assert set(data) == {"code", "ok", "diagnostics", "lines", "assets"}
        assert data["ok"] is True and data["assets"] == [] and data["diagnostics"] == []


# --------------------------------------------------------------------------------------------- the file's layout


class TestLayout:
    def test_imports_are_only_what_the_blocks_need(self):
        result, _ = script(reply("x"))
        assert result.code.startswith("import os\n\nimport fluxer\n")
        result, _ = script(wait(1), hat="command")
        assert result.code.startswith("import asyncio\nimport os\n\nimport fluxer\n")

    def test_standard_imports_are_in_alphabetical_order(self):
        steps = [
            wait(1),
            block("sff_console_log", fields={"LEVEL": "info"}, inputs={"TEXT": "x"}),
            block("sff_round", fields={"OP": "floor"}, inputs={"N": 2.5}),
            block("sff_random", inputs={"A": 1, "B": 6}),
        ]
        result, _ = script(show(steps[2]), show(steps[3]), steps[0], steps[1], hat="ready")
        head = result.code.split("\n\nimport fluxer")[0].splitlines()
        assert head == ["import asyncio", "import logging", "import math", "import os", "import random"]

    def test_two_blank_lines_around_functions_one_elsewhere(self):
        code = compile_workspace(default_workspace()).code
        lines = code.splitlines()
        for index, line in enumerate(lines):
            if line.startswith(("@bot.", "bot.run(")):
                assert lines[index - 1] == "" and lines[index - 2] == "", f"line {index + 1}"
                assert lines[index - 3] != "", f"line {index + 1}"
        assert "\n\n\n\n" not in code

    def test_no_trailing_spaces_and_one_final_newline(self):
        result, _ = script(reply("hello"), if_(compare(message_text(), "eq", "x"), say("y")))
        assert result.code.endswith("\n") and not result.code.endswith("\n\n")
        assert all(line == line.rstrip() for line in result.code.splitlines())

    def test_the_file_ends_with_the_login_and_the_token_is_read_nowhere_else(self):
        result, _ = script(reply("x"))
        assert result.code.rstrip().endswith('bot.run(os.environ["FLUXER_TOKEN"])')
        assert result.code.count("FLUXER_TOKEN") == 1
        assert result.code.count("os.environ") == 1

    def test_scripts_are_written_top_to_bottom_as_they_sit_on_the_workspace(self):
        low = command("low", reply("l"), y=900)
        high = command("high", reply("h"), y=100)
        mid = on_ready(show("m"), y=500)
        result = compiled(low, high, mid)
        code = result.code
        assert code.index("async def high") < code.index("async def on_ready") < code.index("async def low")

    def test_the_prefix_comes_from_the_create_the_bot_block(self):
        for prefix, expected in [("?", '"?"'), ("sff ", '"sff "'), ('"', '"\\""'), ("\\", '"\\\\"')]:
            ws = workspace(on_ready(show("x")))
            ws["blocks"]["blocks"][0]["fields"]["PREFIX"] = prefix
            result = compile_workspace(ws)
            assert f"command_prefix={expected}," in result.code, prefix
            codecheck.check(result.code)

    def test_an_empty_or_long_prefix_is_fixed_with_a_warning(self):
        ws = workspace(on_ready(show("x")))
        ws["blocks"]["blocks"][0]["fields"]["PREFIX"] = "   "
        result = compile_workspace(ws)
        assert 'command_prefix="!"' in result.code
        assert any("prefix was empty" in m for m in messages(result, "warning"))
        ws["blocks"]["blocks"][0]["fields"]["PREFIX"] = "abcdefghijkl"
        assert 'command_prefix="abcdefgh",' in compile_workspace(ws).code

    def test_a_workspace_without_the_two_locked_blocks_still_compiles(self):
        result = compile_workspace(workspace(on_ready(show("x")), setup=False, login=False))
        assert result.ok
        assert 'command_prefix="!"' in result.code
        assert result.code.rstrip().endswith('bot.run(os.environ["FLUXER_TOKEN"])')


# --------------------------------------------------------------------------------------------- events


class TestEvents:
    def test_ready(self):
        result, body = script(show("hi"), hat="ready")
        assert "@bot.event\nasync def on_ready():\n" in result.code
        assert body == ['print("hi")']

    def test_message_scripts_ignore_bots(self):
        result = compiled(on_message(reply("x")))
        assert function_body(result.code, "on_message") == [
            "# Ignore bots (including this one), so replies can't trigger each other forever.",
            "if message.author.bot:",
            "    return",
            'await message.reply("x")',
        ]
        assert "@bot.event\nasync def on_message(message):" in result.code

    def test_member_events_build_the_member_from_the_event_data(self):
        result = compiled(on_join(block("sff_dm_member", inputs={"TEXT": "Welcome!"})), on_leave(show("bye")))
        assert function_body(result.code, "on_member_join")[:1] == ["member = fluxer.GuildMember.from_data(data)"]
        assert "async def on_member_join(data):" in result.code
        assert "async def on_member_remove(data):" in result.code
        assert "@bot.event\nasync def on_member_remove(data):" in result.code

    def test_a_second_script_for_the_same_event_gets_its_own_name(self):
        result = compiled(on_ready(show("one")), on_ready(show("two")), on_ready(show("three")))
        code = result.code
        assert '@bot.event\nasync def on_ready():\n    print("one")' in code
        assert '@bot.on("ready")\nasync def on_ready_2():\n    print("two")' in code
        assert '@bot.on("ready")\nasync def on_ready_3():\n    print("three")' in code

    def test_commands_use_the_command_decorator(self):
        result, body = script(reply("pong"), hat="command")
        assert "@bot.command()\nasync def t(ctx):\n" in result.code
        assert body == ['await ctx.reply("pong")']

    def test_an_empty_script_still_has_a_body(self):
        result = compiled(on_ready(), command("nothing"))
        assert function_body(result.code, "on_ready") == ["pass"]
        assert function_body(result.code, "nothing") == ["pass"]
        assert result.ok

    def test_the_text_after_a_command_arrives_as_a_keyword_argument(self):
        result, body = script(reply(join("You said ", command_text())), hat="command")
        assert 'async def t(ctx, *, text: str = ""):' in result.code
        assert body == ['await ctx.reply(f"You said {text}")']

    def test_a_command_that_does_not_use_the_text_has_no_extra_argument(self):
        result, _ = script(reply("x"), hat="command")
        assert "async def t(ctx):" in result.code and "text: str" not in result.code


class TestCommandNames:
    @pytest.mark.parametrize(
        ("typed", "decorator", "function"),
        [
            ("ping", "@bot.command()", "ping"),
            ("Ping", "@bot.command()", "Ping"),
            ("pong_2", "@bot.command()", "pong_2"),
            ("say hi", '@bot.command(name="say hi")', "say_hi"),
            ("  say   hi  ", '@bot.command(name="say hi")', "say_hi"),
            ("set-name", '@bot.command(name="set-name")', "set_name"),
            ("123", '@bot.command(name="123")', "cmd_123"),
            ("_hidden", '@bot.command(name="_hidden")', "cmd__hidden"),
            ("__init__", '@bot.command(name="__init__")', "cmd___init__"),
            ("class", '@bot.command(name="class")', "class_cmd"),
            ("print", '@bot.command(name="print")', "print_cmd"),
            ("bot", '@bot.command(name="bot")', "bot_cmd"),
            ("text", '@bot.command(name="text")', "text_cmd"),
            ("on_ready", '@bot.command(name="on_ready")', "on_ready_cmd"),
            ("مرحبا", "@bot.command()", "مرحبا"),
            ("ping!", "@bot.command()", "ping"),
            ("a" * 30, "@bot.command()", "a" * 24),
        ],
    )
    def test_how_a_typed_name_becomes_a_function(self, typed, decorator, function):
        result = compiled(command(typed, reply("x")))
        assert f"{decorator}\nasync def {function}(ctx):" in result.code
        assert result.ok

    @pytest.mark.parametrize("typed", ["", "   ", "!!!", "?", "\n", None, 5, {"a": 1}])
    def test_a_command_needs_a_usable_name(self, typed):
        ws = workspace(command("x", reply("x")))
        ws["blocks"]["blocks"][1]["fields"]["NAME"] = typed
        if typed == 5:  # a number is a usable name
            assert "async def cmd_5(ctx)" in compile_workspace(ws).code
            return
        result = compile_workspace(ws)
        assert not result.ok
        assert "Give this command a name." in messages(result, "error")

    def test_two_commands_with_the_same_name_warn_and_both_exist(self):
        result = compiled(command("ping", reply("one")), command("ping", reply("two")))
        assert result.ok
        assert any("Another script already uses the command" in m for m in messages(result, "warning"))
        assert "async def ping(ctx)" in result.code
        assert '@bot.command(name="ping")\nasync def ping_2(ctx)' in result.code

    def test_a_command_never_takes_the_name_of_a_variable(self, tmp_path):
        result = compiled(command("score", change_var("score")), variables=("score",))
        assert "score = 0" in result.code
        assert '@bot.command(name="score")\nasync def score_2(ctx):' in result.code
        running = bot_harness.start(result.code, tmp_path)
        assert running.namespace["score"] == 0  # the variable is still a number, not the command's function

    def test_hyphens_and_spaces_are_kept_in_the_command_text(self):
        result = compiled(command("role add", reply("x")))
        assert '@bot.command(name="role add")' in result.code


# --------------------------------------------------------------------------------------------- statements

STATEMENT_CASES = [
    pytest.param("message", [reply("Hi")], ['await message.reply("Hi")'], id="reply to a message"),
    pytest.param("command", [reply("Hi")], ['await ctx.reply("Hi")'], id="reply in a command"),
    pytest.param("message", [say("Hi")], ['await message.send("Hi")'], id="send to this channel"),
    pytest.param("command", [say("Hi")], ['await ctx.send("Hi")'], id="send in a command"),
    pytest.param(
        "ready",
        [block("sff_send_channel", inputs={"TEXT": "Hi", "CHANNEL": "123456789012345678"})],
        ['channel = await bot.fetch_channel("123456789012345678")', 'await channel.send("Hi")'],
        id="send to a channel by id",
    ),
    pytest.param(
        "message",
        [block("sff_send_embed", fields={"COLOR": "green"}, inputs={"TITLE": "Rules", "TEXT": "Be kind."})],
        [
            'embed = fluxer.Embed(title="Rules", description="Be kind.", color=0x52C987)',
            "await message.send(embed=embed)",
        ],
        id="send an embed",
    ),
    pytest.param(
        "command",
        [block("sff_reply_embed", fields={"COLOR": "blue"}, inputs={"TITLE": "T", "TEXT": "Body"})],
        ['embed = fluxer.Embed(title="T", description="Body", color=0x176DFF)', "await ctx.reply(embed=embed)"],
        id="reply with an embed",
    ),
    pytest.param(
        "message", [block("sff_react", fields={"EMOJI": "👍"})], ['await message.add_reaction("👍")'], id="react"
    ),
    pytest.param("message", [block("sff_delete_message")], ["await message.delete()"], id="delete the message"),
    pytest.param(
        "message",
        [block("sff_dm_author", inputs={"TEXT": "psst"})],
        ['await message.author.send("psst")'],
        id="private message to the author",
    ),
    pytest.param(
        "join",
        [block("sff_dm_member", inputs={"TEXT": "Welcome!"})],
        ["user = await bot.fetch_user(str(member.user.id))", 'await user.send("Welcome!")'],
        id="private message to a member",
    ),
    pytest.param(
        "join",
        [block("sff_give_role", inputs={"ROLE": "123456789012345678"})],
        [
            "guild = await bot.fetch_guild(str(member.guild_id))",
            "joined = await guild.fetch_member(member.user.id)",
            'await joined.add_role(int("123456789012345678"))',
        ],
        id="give a member a role",
    ),
    pytest.param(
        "ready",
        [block("sff_set_status", fields={"STATUS": "idle"})],
        ['await update_presence(status="idle")'],
        id="set the status",
    ),
    pytest.param(
        "ready",
        [block("sff_set_custom_status", inputs={"TEXT": "Learning blocks"})],
        ['await update_presence(custom_text="Learning blocks")'],
        id="set the custom status",
    ),
    pytest.param("ready", [show("hi")], ['print("hi")'], id="write to the console"),
    pytest.param(
        "ready",
        [block("sff_console_log", fields={"LEVEL": "warning"}, inputs={"TEXT": "careful"})],
        ['log.warning("careful")'],
        id="write a warning",
    ),
    pytest.param(
        "ready",
        [block("sff_console_log", fields={"LEVEL": "error"}, inputs={"TEXT": "bad"})],
        ['log.error("bad")'],
        id="write an error",
    ),
    pytest.param("ready", [wait(2)], ["await asyncio.sleep(2)"], id="wait whole seconds"),
    pytest.param("ready", [wait(0.5)], ["await asyncio.sleep(0.5)"], id="wait part of a second"),
    pytest.param("message", [block("sff_stop_script")], ["return"], id="stop this script"),
    pytest.param(
        "message",
        [if_(block("sff_msg_author_is_bot"), reply("Hi"))],
        ["if message.author.bot:", '    await message.reply("Hi")'],
        id="if",
    ),
    pytest.param(
        "message", [if_(block("sff_msg_author_is_bot"))], ["if message.author.bot:", "    pass"], id="empty if"
    ),
    pytest.param(
        "message",
        [if_else(block("sff_msg_author_is_bot"), [reply("a")], [reply("b")])],
        [
            "if message.author.bot:",
            '    await message.reply("a")',
            "else:",
            '    await message.reply("b")',
        ],
        id="if else",
    ),
    pytest.param(
        "message",
        [repeat(3, reply("x"))],
        [
            "for _ in range(3):",
            '    await message.reply("x")',
            "    await asyncio.sleep(0)  # let other things run",
        ],
        id="repeat a number of times",
    ),
    pytest.param(
        "message",
        [repeat(2.9, reply("x"))],
        ["for _ in range(2):", '    await message.reply("x")', "    await asyncio.sleep(0)  # let other things run"],
        id="repeat with a fraction counts whole rounds",
    ),
    pytest.param(
        "message",
        [repeat(math_op(1, "add", 2), reply("x"))],
        [
            "for _ in range(int(1 + 2)):",
            '    await message.reply("x")',
            "    await asyncio.sleep(0)  # let other things run",
        ],
        id="repeat with a calculated count",
    ),
    pytest.param(
        "message",
        [block("sff_repeat_until", inputs={"COND": block("sff_msg_author_is_bot"), "DO": [wait(1)]})],
        [
            "while not message.author.bot:",
            "    await asyncio.sleep(1)",
            "    await asyncio.sleep(0)  # let other things run",
        ],
        id="repeat until",
    ),
    pytest.param(
        "ready",
        [forever(wait(1), show("tick"))],
        [
            "while True:",
            "    await asyncio.sleep(1)",
            '    print("tick")',
            "    await asyncio.sleep(0)  # let other things run",
        ],
        id="forever",
    ),
]


class TestStatements:
    @pytest.mark.parametrize(("hat", "steps", "expected"), STATEMENT_CASES)
    def test_each_statement(self, hat, steps, expected):
        result, body = script(*steps, hat=hat)
        assert body == expected
        assert result.ok, messages(result)

    def test_helpers_a_statement_needs_are_written_once(self):
        result, _ = script(
            block("sff_set_status", fields={"STATUS": "dnd"}),
            block("sff_set_custom_status", inputs={"TEXT": "x"}),
            hat="ready",
        )
        assert result.code.count("async def update_presence(**changes):") == 1
        assert 'presence = {"status": "online", "custom_text": ""}' in result.code

    def test_logging_is_set_up_only_when_a_block_logs(self):
        quiet, _ = script(show("x"), hat="ready")
        assert "logging" not in quiet.code
        loud, _ = script(block("sff_console_log", fields={"LEVEL": "info"}, inputs={"TEXT": "x"}), hat="ready")
        assert 'logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")' in loud.code
        assert 'log = logging.getLogger("bot")' in loud.code
        assert loud.code.index("log = ") < loud.code.index("async def on_ready")

    def test_statements_after_a_cap_block_are_left_out_with_a_warning(self):
        stop = block("sff_stop_script")
        stop["next"] = {"block": show("never")}
        result = compiled(on_message(stop))
        assert "never" not in result.code
        assert any("never run" in m for m in messages(result, "warning"))
        assert result.ok

    def test_a_loop_without_a_wait_warns_and_one_with_a_wait_does_not(self):
        result, _ = script(forever(show("x")), hat="ready")
        assert any("no wait block" in m for m in messages(result, "warning"))
        result, _ = script(forever(if_(block("sff_msg_author_is_bot"), wait(1))), hat="message")
        assert not any("no wait block" in m for m in messages(result))


# --------------------------------------------------------------------------------------------- values

VALUE_CASES = [
    pytest.param("message", block("sff_bot_name"), "print(bot.user.username)", id="bot name"),
    pytest.param("message", block("sff_bot_community_count"), "print(str(len(bot.guilds)))", id="community count"),
    pytest.param("message", block("sff_msg_text"), "print(message.content)", id="message text"),
    pytest.param("message", block("sff_msg_author_name"), "print(message.author.display_name)", id="author name"),
    pytest.param("message", block("sff_msg_author_mention"), "print(message.author.mention)", id="author mention"),
    pytest.param("message", block("sff_msg_author_id"), "print(str(message.author.id))", id="author id"),
    pytest.param("message", block("sff_msg_channel_id"), "print(str(message.channel_id))", id="channel id"),
    pytest.param("message", block("sff_msg_id"), "print(str(message.id))", id="message id"),
    pytest.param("message", block("sff_community_id"), 'print(str(message.guild_id or ""))', id="community id"),
    pytest.param("join", block("sff_community_id"), 'print(str(member.guild_id or ""))', id="community id of a member"),
    pytest.param("message", block("sff_msg_author_is_bot"), "print(str(message.author.bot))", id="author is a bot"),
    pytest.param("command", block("sff_msg_text"), "print(ctx.content)", id="message text in a command"),
    pytest.param("command", block("sff_command_text"), "print(text)", id="text after the command"),
    pytest.param("join", block("sff_member_name"), "print(member.display_name)", id="member name"),
    pytest.param("leave", block("sff_member_mention"), "print(member.mention)", id="member mention"),
    pytest.param("join", block("sff_member_id"), "print(str(member.user.id))", id="member id"),
    pytest.param(
        "ready", block("sff_text_length", inputs={"TEXT": "hello"}), 'print(str(len("hello")))', id="length of text"
    ),
    pytest.param(
        "ready",
        block("sff_word", inputs={"N": 2, "TEXT": "hello big world"}),
        'print(word("hello big world", 2))',
        id="word of text",
    ),
    pytest.param(
        "ready",
        block("sff_replace", inputs={"A": "a", "B": "o", "TEXT": "banana"}),
        'print("banana".replace("a", "o"))',
        id="replace",
    ),
    pytest.param(
        "ready",
        block("sff_to_number", inputs={"TEXT": "5"}),
        'print(str(to_number("5")))',
        id="number from text",
    ),
    pytest.param(
        "ready",
        block("sff_text_case", fields={"OP": "upper"}, inputs={"TEXT": "hi"}),
        'print("hi".upper())',
        id="uppercase",
    ),
    pytest.param(
        "ready",
        block("sff_text_case", fields={"OP": "title"}, inputs={"TEXT": "hi there"}),
        'print("hi there".title())',
        id="title case",
    ),
    pytest.param(
        "ready",
        block("sff_text_case", fields={"OP": "strip"}, inputs={"TEXT": " hi "}),
        'print(" hi ".strip())',
        id="trimmed",
    ),
    pytest.param(
        "ready", block("sff_round", fields={"OP": "round"}, inputs={"N": 2.6}), "print(str(round(2.6)))", id="round"
    ),
    pytest.param(
        "ready",
        block("sff_round", fields={"OP": "floor"}, inputs={"N": 2.6}),
        "print(str(math.floor(2.6)))",
        id="round down",
    ),
    pytest.param(
        "ready",
        block("sff_round", fields={"OP": "ceil"}, inputs={"N": 2.1}),
        "print(str(math.ceil(2.1)))",
        id="round up",
    ),
    pytest.param(
        "ready", block("sff_round", fields={"OP": "abs"}, inputs={"N": -3}), "print(str(abs(-3)))", id="absolute value"
    ),
    pytest.param(
        "ready", block("sff_random", inputs={"A": 1, "B": 6}), "print(str(random.randint(1, 6)))", id="random between"
    ),
    pytest.param(
        "ready",
        block("sff_random", inputs={"A": 6, "B": 1}),
        "print(str(random.randint(1, 6)))",
        id="random between, either way round",
    ),
    pytest.param(
        "ready",
        block("sff_contains", inputs={"A": "Hello World", "B": "WORLD"}),
        'print(str("world" in "hello world"))',
        id="contains ignores capitals",
    ),
    pytest.param(
        "message",
        block("sff_contains", inputs={"A": block("sff_msg_text"), "B": "hello"}),
        'print(str("hello" in message.content.lower()))',
        id="message contains",
    ),
    pytest.param("ready", block("sff_boolean", fields={"BOOL": "true"}), 'print("True")', id="true"),
    pytest.param("ready", block("sff_boolean", fields={"BOOL": "false"}), 'print("False")', id="false"),
    pytest.param("ready", block("sff_number", fields={"NUM": 7}), 'print("7")', id="a number as text"),
    pytest.param("ready", block("sff_number", fields={"NUM": 2.5}), 'print("2.5")', id="a fraction as text"),
]


class TestValues:
    @pytest.mark.parametrize(("hat", "value", "expected"), VALUE_CASES)
    def test_each_value_block(self, hat, value, expected):
        result, body = script(show(value), hat=hat)
        assert body == [expected]
        assert result.ok, messages(result)

    def test_values_that_need_a_helper_get_it(self):
        result, _ = script(show(block("sff_word", inputs={"N": 1, "TEXT": "a b"})), hat="ready")
        code = result.code
        assert "def to_number(value):" in code and "def word(text, number):" in code
        assert code.index("def to_number") < code.index("def word")  # the helper's own helper comes first

    def test_a_value_block_that_needs_a_message_is_refused_without_one(self):
        result, _ = script(show(block("sff_msg_text")), hat="ready")
        assert not result.ok
        assert any("needs a message" in m and "when the bot is ready" in m for m in messages(result, "error"))

    def test_text_after_the_command_is_only_for_commands(self):
        result, _ = script(show(block("sff_command_text")), hat="message")
        assert any("needs a command" in m for m in messages(result, "error"))

    def test_member_blocks_are_only_for_member_events(self):
        result, _ = script(show(block("sff_member_name")), hat="message")
        assert any("needs a member who joined or left" in m for m in messages(result, "error"))
        result, _ = script(block("sff_dm_member", inputs={"TEXT": "x"}), hat="ready")
        assert any("needs a member" in m for m in messages(result, "error"))

    def test_message_statements_are_refused_in_a_ready_script(self):
        result, _ = script(reply("x"), hat="ready")
        assert any("needs a message" in m for m in messages(result, "error"))

    def test_the_community_id_works_with_a_message_or_a_member_but_not_alone(self):
        result, _ = script(show(block("sff_community_id")), hat="ready")
        assert any("needs a message or a member" in m for m in messages(result, "error"))


# --------------------------------------------------------------------------------------------- joining text


class TestJoin:
    def test_two_literals_become_one(self):
        _, body = script(show(join("Hello, ", "world")), hat="ready")
        assert body == ['print("Hello, world")']

    def test_a_name_inside_text_becomes_an_f_string(self):
        _, body = script(show(join(join("Hi ", block("sff_msg_author_name")), "!")))
        assert body == ['print(f"Hi {message.author.display_name}!")']

    def test_several_pieces_are_flattened_into_one_f_string(self):
        value = join(join("a", message_text()), join(" b ", block("sff_msg_author_name")))
        _, body = script(show(value))
        assert body == ['print(f"a{message.content} b {message.author.display_name}")']

    def test_calls_are_added_with_plus_and_numbers_are_turned_into_text(self):
        _, body = script(show(join("ID: ", block("sff_msg_author_id"))))
        assert body == ['print("ID: " + str(message.author.id))']
        _, body = script(show(join("Total: ", math_op(1, "add", 2))), hat="ready")
        assert body == ['print("Total: " + str(1 + 2))']

    def test_braces_and_quotes_in_the_text_are_escaped_for_the_f_string(self):
        _, body = script(show(join('{"a": "b"} ', message_text())))
        assert body == ['print(f"{{\\"a\\": \\"b\\"}} {message.content}")']
        code = compile_workspace(workspace(on_message(show(join("{x} \\ {", message_text()))))).code
        assert 'f"{{x}} \\\\ {{{message.content}"' in code

    def test_text_that_looks_like_a_number_is_kept_exactly_as_typed(self):
        _, body = script(show(join("Version ", "5.0")), hat="ready")
        assert body == ['print("Version 5.0")']
        _, body = script(show(join("007", join("-", "1.50"))), hat="ready")
        assert body == ['print("007-1.50")']

    def test_a_variable_inside_text(self):
        _, body = script(show(join("Score: ", variable("score"))), variables=("score",))
        assert body == ['print(f"Score: {score}")']

    def test_empty_pieces_disappear(self):
        _, body = script(show(join("", message_text())))
        assert body == ["print(message.content)"]
        _, body = script(show(join("", "")), hat="ready")
        assert body == ['print("")']

    def test_an_empty_socket_in_a_join_is_just_empty_text(self):
        result, body = script(show(block("sff_join", inputs={"A": "x"})), hat="ready")
        assert body == ['print("x")']
        assert result.ok and not result.diagnostics

    def test_a_join_of_a_join_of_a_variable_alone(self):
        _, body = script(show(join(variable("score"), "")), variables=("score",))
        assert body == ["print(str(score))"]


# --------------------------------------------------------------------------------------------- maths and logic


class TestOperators:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (math_op(1, "add", 2), "1 + 2"),
            (math_op(math_op(1, "add", 2), "multiply", 3), "(1 + 2) * 3"),
            (math_op(1, "add", math_op(2, "multiply", 3)), "1 + 2 * 3"),
            (math_op(1, "subtract", math_op(2, "subtract", 3)), "1 - (2 - 3)"),
            (math_op(math_op(1, "subtract", 2), "subtract", 3), "1 - 2 - 3"),
            (math_op(8, "divide", math_op(4, "divide", 2)), "8 / (4 / 2)"),
            (math_op(math_op(8, "divide", 4), "multiply", 2), "8 / 4 * 2"),
            (math_op(7, "mod", 3), "7 % 3"),
            (math_op(5, "subtract", -3), "5 - -3"),
            (math_op(-2, "multiply", 3), "-2 * 3"),
            (math_op(2.5, "add", 0.5), "2.5 + 0.5"),
        ],
    )
    def test_maths_gets_exactly_the_brackets_it_needs(self, value, expected):
        _, body = script(show(value), hat="ready")
        assert body == [f"print(str({expected}))"]

    def test_text_in_a_number_socket_is_turned_into_a_number(self):
        _, body = script(show(math_op(block("sff_to_number", inputs={"TEXT": "4"}), "add", 1)), hat="ready")
        assert body == ['print(str(to_number("4") + 1))']

    def test_a_text_box_that_holds_a_number_is_a_number(self):
        _, body = script(show(math_op("4", "add", "1")), hat="ready")
        assert body == ["print(str(4 + 1))"]

    def test_a_text_box_that_is_not_a_number_counts_as_zero_with_a_warning(self):
        result, body = script(show(math_op("four", "add", 1)), hat="ready")
        assert body == ["print(str(0 + 1))"]
        assert any("isn't a number" in m for m in messages(result, "warning"))

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (compare(message_text(), "eq", "hello"), 'message.content == "hello"'),
            (compare(message_text(), "ne", "hello"), 'message.content != "hello"'),
            (compare(message_text(), "eq", "5"), 'message.content == "5"'),
            (compare(block("sff_text_length", inputs={"TEXT": "x"}), "eq", "5"), 'len("x") == 5'),
            (compare("5", "eq", block("sff_text_length", inputs={"TEXT": "x"})), '5 == len("x")'),
            (compare(variable("score"), "eq", "5"), "same(score, 5)"),
            (compare(variable("score"), "ne", "5"), "not same(score, 5)"),
            (compare(variable("score"), "eq", "hello"), 'score == "hello"'),
            (compare(variable("score"), "gt", "5"), "score > 5"),
            (compare(variable("score"), "le", variable("score")), "score <= score"),
            (compare(message_text(), "gt", "5"), "to_number(message.content) > 5"),
            (compare("3", "lt", "10"), "3 < 10"),
            (compare(math_op(1, "add", 1), "ge", 2), "1 + 1 >= 2"),
        ],
    )
    def test_comparisons(self, value, expected):
        _, body = script(if_(value, show("yes")), variables=("score",))
        assert body[0] == f"if {expected}:"

    def test_same_lets_text_equal_the_number_it_spells(self):
        result, _ = script(if_(compare(variable("score"), "eq", "5"), show("yes")), variables=("score",))
        assert "def same(a, b):" in result.code

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (logic(compare(1, "eq", 1), "and", compare(2, "eq", 2)), "1 == 1 and 2 == 2"),
            (logic(compare(1, "eq", 1), "or", compare(2, "eq", 2)), "1 == 1 or 2 == 2"),
            (
                logic(logic(compare(1, "eq", 1), "or", compare(2, "eq", 2)), "and", compare(3, "eq", 3)),
                "(1 == 1 or 2 == 2) and 3 == 3",
            ),
            (
                logic(compare(1, "eq", 1), "and", logic(compare(2, "eq", 2), "or", compare(3, "eq", 3))),
                "1 == 1 and (2 == 2 or 3 == 3)",
            ),
            (not_(compare(1, "eq", 2)), "not (1 == 2)"),
            (not_(block("sff_msg_author_is_bot")), "not message.author.bot"),
            (
                logic(not_(block("sff_msg_author_is_bot")), "and", block("sff_boolean", fields={"BOOL": "true"})),
                "not message.author.bot and True",
            ),
        ],
    )
    def test_logic_gets_exactly_the_brackets_it_needs(self, value, expected):
        _, body = script(if_(value, show("yes")))
        assert body[0] == f"if {expected}:"

    def test_an_empty_condition_is_false_with_a_warning(self):
        result, body = script(block("sff_if", inputs={"DO": [show("x")]}), hat="ready")
        assert body[0] == "if False:"
        assert any("condition is empty" in m for m in messages(result, "warning"))

    def test_a_text_block_in_a_condition_is_tested_for_being_empty(self):
        ws_if = block("sff_if", inputs={"COND": message_text(), "DO": [show("x")]})
        result, body = script(ws_if)
        assert body[0] == "if bool(message.content):"
        assert result.ok


# --------------------------------------------------------------------------------------------- variables and lists


class TestVariables:
    def test_set_and_change(self):
        result, body = script(set_var("score", 0), change_var("score", 2), variables=("score",))
        assert body == ["score = 0", "score += 2"]
        assert function_body(result.code, "on_message")[0] == "global score"
        assert "\nscore = 0\n\n\n" in result.code  # and it starts at 0 before any script runs

    def test_variables_start_at_zero_and_lists_start_empty(self):
        result, _ = script(
            show(variable("score")),
            block("sff_list_clear", fields={"LIST": lst("fruits")}),
            variables=("score", ("fruits", "List")),
        )
        assert "\nscore = 0\nfruits = []\n" in result.code

    def test_only_the_variables_the_blocks_use_are_created(self):
        result, _ = script(show("x"), variables=("score", "unused"))
        assert "score" not in result.code and "unused" not in result.code

    def test_a_typed_number_becomes_a_number_and_other_text_stays_text(self):
        _, body = script(
            set_var("a", "10"), set_var("a", "ten"), set_var("a", "007"), set_var("a", "2.50"), variables=("a",)
        )
        assert body == ["a = 10", 'a = "ten"', 'a = "007"', 'a = "2.50"']

    def test_a_variable_set_from_another_block(self):
        _, body = script(set_var("name", message_text()), set_var("n", math_op(1, "add", 1)), variables=("name", "n"))
        assert body == ["name = message.content", "n = 1 + 1"]

    def test_global_names_are_declared_first_and_sorted(self):
        result, _ = script(set_var("b", 1), set_var("a", 2), variables=("b", "a"))
        assert function_body(result.code, "on_message")[0] == "global a, b"

    def test_reading_a_variable_needs_no_global(self):
        result, _ = script(show(variable("score")), variables=("score",))
        assert "global" not in result.code

    def test_scripts_that_set_variables_really_change_them(self):
        result, _ = script(change_var("score", 5), hat="ready", variables=("score",))
        assert function_body(result.code, "on_ready")[0] == "global score"

    @pytest.mark.parametrize(
        ("typed", "ident"),
        [
            ("score", "score"),
            ("Score", "score"),
            ("Hello World!", "hello_world"),
            ("my-var", "my_var"),
            ("  spaced  out  ", "spaced_out"),
            ("123", "v_123"),
            ("1st place", "v_1st_place"),
            ("message", "message_"),
            ("class", "class_"),
            ("list", "list_"),
            ("print", "print_"),
            ("bot", "bot_"),
            ("os", "os_"),
            ("ASSETS", "assets"),
            ("on_ready", "on_ready_"),
            ("___", "variable"),
            ("", "variable"),
            ("نقاط", "نقاط"),
            ("a" * 100, "a" * 40),
            ("x", "x_"),
        ],
    )
    def test_names_become_safe_python_names(self, typed, ident):
        result, body = script(set_var(typed, 1), variables=(typed,))
        assert body == [f"{ident} = 1"], result.code
        assert result.ok

    def test_two_names_that_become_the_same_get_different_variables(self):
        result, body = script(set_var("Score", 1), set_var("score", 2), variables=("Score", "score"))
        assert body == ["score = 1", "score_2 = 2"]

    def test_a_variable_block_with_nothing_chosen_is_an_error(self):
        broken = block("sff_set_variable", fields={"VAR": {"id": "missing"}}, inputs={"VALUE": 1})
        result, _ = script(broken)
        assert not result.ok
        assert "Pick a variable in this block." in messages(result, "error")

    def test_old_saves_that_store_the_name_still_work(self):
        step = block("sff_set_variable", fields={"VAR": "score"}, inputs={"VALUE": 1})
        _, body = script(step, variables=("score",))
        assert body == ["score = 1"]


class TestLists:
    L = (("fruits", "List"),)

    def test_list_statements(self):
        fruits = {"LIST": lst("fruits")}
        words = block("sff_list_split", inputs={"TEXT": "a b", "SEP": ""})
        steps = [
            block("sff_list_add", fields=fruits, inputs={"ITEM": "apple"}),
            block("sff_list_add", fields=fruits, inputs={"ITEM": "3"}),
            block("sff_list_delete", fields=fruits, inputs={"N": 1}),
            block("sff_list_clear", fields=fruits),
            block("sff_list_set", fields=fruits, inputs={"VALUE": words}),
        ]
        result, body = script(*steps, variables=self.L)
        assert body == [
            'fruits.append("apple")',
            "fruits.append(3)",
            "delete_item(fruits, 1)",
            "fruits.clear()",
            'fruits = "a b".split()',
        ]
        assert function_body(result.code, "on_message")[0] == "global fruits"
        assert "def delete_item(items, number):" in result.code

    def test_list_values(self):
        cases = {
            "sff_list_item": ({"N": 2}, "item_of(fruits, 2)"),
            "sff_list_length": ({}, "len(fruits)"),
            "sff_list_random": ({}, "random_item(fruits)"),
            "sff_list_join": ({"SEP": ", "}, '", ".join(str(x) for x in fruits)'),
        }
        for type_, (inputs, expected) in cases.items():
            step = show(block(type_, fields={"LIST": lst("fruits")}, inputs=inputs or None))
            _, body = script(step, variables=self.L)
            assert body[0].startswith("print("), type_
            assert expected in body[0], (type_, body)

    def test_a_list_contains_something(self):
        has = block("sff_list_contains", fields={"LIST": lst("fruits")}, inputs={"ITEM": "apple"})
        _, body = script(if_(has, show("yes")), variables=self.L)
        assert body[0] == 'if "apple" in fruits:'
        has = block("sff_list_contains", fields={"LIST": lst("fruits")}, inputs={"ITEM": "3"})
        result, body = script(if_(has, show("yes")), variables=self.L)
        assert body[0] == "if has_item(fruits, 3):"
        assert "def has_item(items, value):" in result.code and "def same(a, b):" in result.code

    def test_for_each(self):
        loop = block(
            "sff_for_each",
            fields={"VAR": {"id": var_id("item")}},
            inputs={"LIST": list_variable("fruits"), "DO": [show(variable("item"))]},
        )
        result, body = script(loop, variables=(("fruits", "List"), "item"))
        assert body == [
            "for item in fruits:",
            "    print(str(item))",
            "    await asyncio.sleep(0)  # let other things run",
        ]
        assert function_body(result.code, "on_message")[0] == "global item"

    def test_setting_a_list_to_another_list_copies_it(self):
        fruits = {"LIST": lst("fruits")}
        step = block(
            "sff_list_set", fields=fruits, inputs={"VALUE": block("sff_list_variable", fields={"LIST": lst("more")})}
        )
        _, body = script(step, variables=(("fruits", "List"), ("more", "List")))
        assert body == ["fruits = list(more)"]
        _, body = script(block("sff_list_set", fields=fruits), variables=self.L)
        assert body == ["fruits = []"]  # nothing plugged in: an empty list, with nothing to copy

    def test_split_forms(self):
        def set_from_split(text_in, sep):
            split = block("sff_list_split", inputs={"TEXT": text_in, "SEP": sep})
            return block("sff_list_set", fields={"LIST": lst("fruits")}, inputs={"VALUE": split})

        _, body = script(set_from_split("a,b", ","), variables=self.L)
        assert body == ['fruits = "a,b".split(",")']
        _, body = script(set_from_split(message_text(), message_text()), variables=self.L)
        assert body == ["fruits = message.content.split(message.content or None)"]

    def test_a_plain_variable_where_a_list_is_needed_warns(self):
        step = block("sff_list_add", fields={"LIST": lst("score")}, inputs={"ITEM": "x"})
        result, _ = script(step, variables=("score",))
        assert result.ok
        assert any("isn't a list" in m for m in messages(result, "warning"))

    def test_an_empty_list_socket_warns(self):
        step = block("sff_for_each", fields={"VAR": {"id": var_id("item")}}, inputs={"DO": [show("x")]})
        result, body = script(step, variables=("item",))
        assert body[0] == "for item in []:"
        assert any("list socket is empty" in m for m in messages(result, "warning"))


# --------------------------------------------------------------------------------------------- files and JSON


class TestAssets:
    ASSETS = {"cat.png": "image", "quotes.json": "json", "notes.txt": "text"}

    def test_sending_an_image_defines_the_assets_folder(self):
        step = asset_block("sff_reply_image", "cat.png")
        result, body = script(step, assets=self.ASSETS)
        assert body == ['await message.reply(file=fluxer.File(ASSETS / "cat.png"))']
        assert "from pathlib import Path\n" in result.code
        assert 'ASSETS = Path(__file__).parent / "assets"' in result.code
        assert result.assets == ["cat.png"]
        assert result.ok

    def test_the_other_file_blocks(self):
        _, body = script(
            asset_block("sff_send_image", "cat.png"),
            asset_block("sff_reply_file", "notes.txt"),
            show(block("sff_file_text", fields={"FILE": "notes.txt"})),
            assets=self.ASSETS,
        )
        assert body == [
            'await message.send(file=fluxer.File(ASSETS / "cat.png"))',
            'await message.reply(file=fluxer.File(ASSETS / "notes.txt"))',
            'print((ASSETS / "notes.txt").read_text(encoding="utf-8"))',
        ]

    def test_loading_json_and_reading_from_it(self):
        load = block("sff_json_load", fields={"FILE": "quotes.json", "VAR": {"id": var_id("quotes")}})
        get = show(block("sff_json_get", fields={"VAR": {"id": var_id("quotes")}}, inputs={"KEY": "0.text"}))
        result, body = script(load, get, assets=self.ASSETS, variables=("quotes",))
        assert body == [
            'quotes = json.loads((ASSETS / "quotes.json").read_text(encoding="utf-8"))',
            'print(str(json_get(quotes, "0.text")))',
        ]
        assert "import json\n" in result.code and "def json_get(data, path):" in result.code
        assert function_body(result.code, "on_message")[0] == "global quotes"

    def test_no_assets_folder_unless_a_block_uses_a_file(self):
        result, _ = script(reply("x"), assets=self.ASSETS)
        assert "ASSETS" not in result.code and "pathlib" not in result.code and result.assets == []

    def test_each_file_is_listed_once(self):
        steps = [asset_block("sff_reply_image", "cat.png"), asset_block("sff_send_image", "cat.png")]
        result, _ = script(*steps, assets=self.ASSETS)
        assert result.assets == ["cat.png"]

    @pytest.mark.parametrize(
        ("step", "fragment"),
        [
            (asset_block("sff_reply_image", "dog.png"), "isn't uploaded"),
            (asset_block("sff_reply_image", "quotes.json"), "is a json file, but this block needs image"),
            (block("sff_reply_image", fields={"FILE": ""}), "Choose a file"),
            (block("sff_reply_image"), "Choose a file"),
            (asset_block("sff_reply_image", "../secret.png"), "isn't a file name"),
            (asset_block("sff_reply_image", "a/b.png"), "isn't a file name"),
            (asset_block("sff_reply_image", "x.svg"), "isn't a file name"),
        ],
    )
    def test_a_file_must_exist_and_be_the_right_kind(self, step, fragment):
        result, _ = script(
            step, assets=dict(self.ASSETS, **{"../secret.png": "image", "a/b.png": "image", "x.svg": "image"})
        )
        assert not result.ok
        assert any(fragment in m for m in messages(result, "error")), messages(result)

    def test_without_a_list_of_uploads_only_the_name_is_checked(self):
        result, body = script(asset_block("sff_reply_image", "anything.png"))
        assert result.ok
        assert 'ASSETS / "anything.png"' in body[0]
        result, _ = script(asset_block("sff_reply_image", "../x.png"))
        assert not result.ok

    def test_a_json_block_accepts_json_only(self):
        load = block("sff_json_load", fields={"FILE": "notes.txt", "VAR": {"id": var_id("q")}})
        result, _ = script(load, assets=self.ASSETS, variables=("q",))
        assert not result.ok
        assert any("needs json" in m for m in messages(result, "error"))


# --------------------------------------------------------------------------------------------- what is said about mistakes


class TestDiagnostics:
    def test_a_new_workspace_has_nothing_to_say(self):
        assert compile_workspace(default_workspace()).diagnostics == []

    def test_diagnostics_point_at_the_block(self):
        step = reply("x")
        result = compiled(on_ready(step))
        problem = next(d for d in result.diagnostics if d.level == "error")
        assert problem.block == step["id"]
        assert result.as_dict()["diagnostics"][0]["block"] == step["id"]

    def test_a_workspace_with_no_events_says_so(self):
        result = compile_workspace(workspace())
        assert result.ok
        assert any("no event block" in m for m in messages(result, "warning"))

    def test_blocks_lying_around_are_pointed_out(self):
        stray_statement, stray_value, stray_box = reply("x"), join("a", message_text()), text("hi")
        result = compiled(on_ready(show("x")), stray_statement, stray_value, stray_box)
        by_block = {d.block: d for d in result.diagnostics}
        assert "isn't under an event" in by_block[stray_statement["id"]].message
        assert "isn't plugged into anything" in by_block[stray_value["id"]].message
        assert stray_box["id"] not in by_block  # a stray box of text is harmless
        assert result.ok
        assert "reply" not in result.code

    def test_disabled_blocks_write_nothing_and_say_nothing(self):
        off = reply("never")
        off["enabled"] = False
        also_off = reply("also never")
        also_off["disabledReasons"] = ["MANUALLY_DISABLED"]
        flag = reply("and never")
        flag["disabled"] = True
        result = compiled(on_message(off, reply("yes"), also_off, flag))
        assert "never" not in result.code and 'reply("yes")' in result.code
        assert result.diagnostics == []

    def test_a_disabled_script_is_left_out(self):
        off = command("off", reply("x"))
        off["enabled"] = False
        result = compiled(off, on_ready(show("x")))
        assert "async def off" not in result.code and result.ok

    def test_a_disabled_block_in_a_socket_counts_as_empty(self):
        inner = message_text()
        inner["enabled"] = False
        result, body = script(show(join("a", inner)), hat="ready")
        assert body == ['print("a")']
        assert result.diagnostics == []

    def test_an_unknown_block_is_an_error_and_the_rest_still_compiles(self):
        mystery = block("sff_does_not_exist")
        result = compiled(on_message(mystery, reply("still here")))
        assert not result.ok
        assert any("doesn't know a block called “sff_does_not_exist”" in m for m in messages(result, "error"))
        assert 'await message.reply("still here")' in result.code

    def test_an_unknown_block_in_a_socket(self):
        result, body = script(show(block("sff_nope")), hat="ready")
        assert not result.ok and body == ['print("")']

    def test_a_statement_in_a_socket_and_a_value_in_a_stack_are_refused(self):
        result, _ = script(show(reply("x")), hat="message")
        assert any("can't be used inside another block" in m for m in messages(result, "error"))
        result, _ = script(message_text(), hat="message")
        assert any("can't be used as a step" in m for m in messages(result, "error"))

    def test_a_menu_value_that_is_not_a_choice_falls_back_with_a_warning(self):
        step = block("sff_set_status", fields={"STATUS": "wide awake"})
        result, body = script(step, hat="ready")
        assert body == ['await update_presence(status="online")']
        assert any("isn't one of the choices" in m for m in messages(result, "warning"))

    def test_two_create_the_bot_blocks_warn(self):
        ws = workspace(on_ready(show("x")))
        ws["blocks"]["blocks"].append(block("sff_bot_setup", fields={"PREFIX": "?"}))
        ws["blocks"]["blocks"].append(block("sff_bot_login"))
        result = compile_workspace(ws)
        assert result.ok
        assert sum("Only one" in m for m in messages(result, "warning")) == 2
        assert 'command_prefix="!"' in result.code

    def test_errors_stop_a_bot_from_running_but_warnings_do_not(self):
        assert compile_workspace(workspace(forever(show("x")))).ok
        assert not compiled(on_ready(reply("x"))).ok

    def test_the_wording_is_in_plain_english(self):
        result, _ = script(show(block("sff_msg_text")), hat="ready")
        assert messages(result, "error") == [
            "“message text” needs a message, but “when the bot is ready” doesn't have one."
        ]


# --------------------------------------------------------------------------------------------- which lines a block wrote


class TestLineMap:
    def test_nested_blocks_sit_inside_their_parent(self):
        inner = reply("a")
        outer = if_(block("sff_msg_author_is_bot"), inner)
        last = reply("z")
        hat = on_message(outer, last)
        result = compiled(hat)
        code_lines = result.code.splitlines()
        first, final = result.lines[outer["id"]]
        assert code_lines[first - 1] == "    if message.author.bot:"
        assert code_lines[final - 1] == '        await message.reply("a")'
        assert result.lines[inner["id"]] == [final, final]
        assert result.lines[last["id"]] == [final + 1, final + 1]
        assert result.lines[hat["id"]][0] < first and result.lines[hat["id"]][1] == final + 1

    def test_if_else_covers_both_branches(self):
        a, b = reply("a"), reply("b")
        step = if_else(block("sff_msg_author_is_bot"), [a], [b])
        result = compiled(on_message(step))
        first, last = result.lines[step["id"]]
        assert last - first == 3
        assert result.lines[b["id"]][1] == last

    def test_every_line_of_a_script_belongs_to_its_hat(self):
        steps = [reply("1"), say("2"), show("3")]
        hat = on_message(*steps)
        result = compiled(hat)
        first, last = result.lines[hat["id"]]
        for step in steps:
            a, b = result.lines[step["id"]]
            assert first < a <= b <= last
        assert result.code.splitlines()[first - 1] == "@bot.event"

    def test_blocks_that_wrote_nothing_have_no_lines(self):
        broken = block("sff_json_load", fields={"FILE": "", "VAR": {"id": var_id("q")}})
        result = compiled(on_ready(broken), variables=("q",))
        assert broken["id"] not in result.lines

    def test_lines_are_one_based_and_inside_the_file(self):
        result = compiled(on_message(reply("1"), repeat(2, reply("x"))))
        total = len(result.code.splitlines())
        assert all(1 <= a <= b <= total for a, b in result.lines.values())


class TestHelpersAreValidPython:
    def test_every_helper_parses_on_its_own_with_the_helpers_it_needs(self):
        from scratch_fluxer.compiler import HELPER_ORDER, HELPERS

        for name in HELPER_ORDER:
            ast.parse(HELPERS[name]["source"])
