"""The generated bots really work: the code the compiler writes is run by the real fluxer.py and fed gateway events.

Only the network is faked (see bot_harness.py). Messages, members and commands go through fluxer.py's own parsing and
dispatch, so a block that relies on something the library doesn't have fails here.
"""

from __future__ import annotations

import logging

import bot_harness
from blocks import (
    block,
    change_var,
    command,
    command_text,
    compare,
    if_,
    if_else,
    join,
    list_variable,
    math_op,
    message_text,
    on_join,
    on_leave,
    on_message,
    on_ready,
    repeat,
    reply,
    say,
    set_var,
    show,
    var_id,
    variable,
    wait,
    workspace,
)

from scratch_fluxer.compiler import compile_workspace, default_workspace


def build(tmp_path, *top, variables=(), assets=None, files=None):
    result = compile_workspace(workspace(*top, variables=variables), assets=assets)
    assert result.ok, result.diagnostics
    return bot_harness.start(result.code, tmp_path, assets=files)


def lst(name):
    return {"id": var_id(name)}


# --------------------------------------------------------------------------------------------- the starting bot


async def test_the_starting_bot_answers_ping_and_prints_when_ready(tmp_path, capsys):
    running = bot_harness.start(compile_workspace(default_workspace()).code, tmp_path)
    assert running.token == "test-token"  # it logs in with the saved token, read from the environment
    await running.ready()
    assert capsys.readouterr().out == "Bot is online!\n"
    await running.message("!ping")
    assert running.sent == [{"content": "Pong!", "reply": True, "embeds": None, "files": None, "channel": "300"}]


async def test_the_starting_bot_ignores_other_messages_and_other_bots(tmp_path):
    running = bot_harness.start(compile_workspace(default_workspace()).code, tmp_path)
    await running.message("hello")
    await running.message("!ping", is_bot=True)
    await running.message("ping")  # no prefix
    assert running.sent == []


# --------------------------------------------------------------------------------------------- events


async def test_a_message_script_answers_people_but_not_bots(tmp_path):
    running = build(tmp_path, on_message(reply(join("Echo: ", message_text()))))
    await running.message("hi")
    await running.message("i am a bot", is_bot=True)
    assert running.texts == ["Echo: hi"]


async def test_message_scripts_and_commands_both_run_for_a_command_message(tmp_path):
    running = build(tmp_path, on_message(say("seen")), command("ping", reply("Pong!")))
    await running.message("!ping")
    assert sorted(running.texts) == ["Pong!", "seen"]


async def test_two_scripts_for_the_same_event_both_run(tmp_path, capsys):
    running = build(tmp_path, on_ready(show("one")), on_ready(show("two")), on_message(say("a")), on_message(say("b")))
    await running.ready()
    assert capsys.readouterr().out.splitlines() == ["one", "two"]
    await running.message("x")
    assert running.texts == ["a", "b"]


async def test_the_text_after_a_command(tmp_path):
    running = build(tmp_path, command("say", reply(command_text())))
    await running.message("!say hello   big world ")
    await running.message("!say")
    assert running.texts == ["hello   big world", ""]


async def test_a_command_with_a_space_in_its_name(tmp_path):
    running = build(tmp_path, command("role add", reply("added")), command("role", reply("just role")))
    await running.message("!role add admin")
    await running.message("!role")
    assert running.texts == ["added", "just role"]


async def test_a_command_prefix_other_than_the_bang(tmp_path):
    ws = workspace(command("ping", reply("Pong!")))
    ws["blocks"]["blocks"][0]["fields"]["PREFIX"] = "?"
    running = bot_harness.start(compile_workspace(ws).code, tmp_path)
    await running.message("!ping")
    await running.message("?ping")
    assert running.texts == ["Pong!"]


async def test_members_joining_and_leaving(tmp_path):
    welcome = block("sff_send_channel", inputs={"CHANNEL": "300", "TEXT": join("Welcome ", block("sff_member_name"))})
    goodbye = block("sff_send_channel", inputs={"CHANNEL": "300", "TEXT": join(block("sff_member_mention"), " left")})
    running = build(tmp_path, on_join(welcome, block("sff_dm_member", inputs={"TEXT": "Hi!"})), on_leave(goodbye))
    await running.member_joins("newbie", user_id=321)
    await running.member_leaves("oldtimer", user_id=654)
    assert running.texts == ["Welcome newbie", "Hi!", "<@654> left"]
    assert running.http.named("get_user") == [(("321",), {})]
    assert running.http.named("create_dm") == [((321,), {})]


async def test_giving_a_new_member_a_role(tmp_path):
    running = build(tmp_path, on_join(block("sff_give_role", inputs={"ROLE": "555000111222333444"})))
    await running.member_joins("newbie", user_id=321, guild_id=777)
    assert running.http.named("get_guild") == [(("777",), {})]
    assert running.http.named("get_guild_member") == [((777, 321), {})]
    assert running.http.named("add_guild_member_role") == [((777, 321, 555000111222333444), {"reason": None})]


async def test_a_role_id_that_is_not_a_number_is_reported_and_the_bot_carries_on(tmp_path):
    running = build(
        tmp_path,
        on_join(block("sff_give_role", inputs={"ROLE": "Members"}), block("sff_dm_member", inputs={"TEXT": "Hi!"})),
    )
    await running.member_joins("newbie", user_id=321)
    assert running.http.named("add_guild_member_role") == []
    assert running.texts == []


async def test_community_ids(tmp_path):
    running = build(
        tmp_path, on_join(block("sff_send_channel", inputs={"CHANNEL": "300", "TEXT": block("sff_community_id")}))
    )
    await running.member_joins(guild_id=777)
    assert running.texts == ["777"]


# --------------------------------------------------------------------------------------------- values from the message


async def test_message_info(tmp_path):
    info = join(
        join(join(block("sff_msg_author_name"), " / "), block("sff_msg_author_id")),
        join(join(" / ", block("sff_msg_channel_id")), join(" / ", block("sff_msg_author_mention"))),
    )
    running = build(tmp_path, on_message(reply(info)))
    await running.message("x", author="alice", author_id=42, channel_id=77, global_name="Alice W")
    assert running.texts == ["Alice W / 42 / 77 / <@42>"]


async def test_the_bot_knows_its_own_name(tmp_path):
    running = build(tmp_path, command("who", reply(join("I am ", block("sff_bot_name")))))
    await running.ready()
    await running.message("!who")
    assert running.texts == ["I am testbot"]


async def test_the_number_of_communities(tmp_path):
    running = build(tmp_path, command("n", reply(join("Communities: ", block("sff_bot_community_count")))))
    await running.ready(guilds=[{"id": "1", "name": "a"}, {"id": "2", "name": "b"}])
    await running.message("!n")
    assert running.texts == ["Communities: 2"]


# --------------------------------------------------------------------------------------------- decisions and loops


async def test_if_else_on_the_text_of_a_message(tmp_path):
    decide = if_else(compare(message_text(), "eq", "hello"), [reply("Hi!")], [reply("?")])
    running = build(tmp_path, on_message(decide))
    await running.message("hello")
    await running.message("Hello")
    assert running.texts == ["Hi!", "?"]


async def test_contains_ignores_capitals(tmp_path):
    running = build(
        tmp_path, on_message(if_(block("sff_contains", inputs={"A": message_text(), "B": "Hello"}), reply("Hey!")))
    )
    await running.message("oh HELLO there")
    await running.message("goodbye")
    assert running.texts == ["Hey!"]


async def test_stop_this_script(tmp_path):
    running = build(
        tmp_path,
        on_message(
            if_(
                block("sff_contains", inputs={"A": message_text(), "B": "stop"}),
                reply("stopping"),
                block("sff_stop_script"),
            ),
            reply("went on"),
        ),
    )
    await running.message("stop it")
    await running.message("go")
    assert running.texts == ["stopping", "went on"]


async def test_repeat_and_wait(tmp_path):
    running = build(tmp_path, command("spam", repeat(3, say("x"), wait(0.001)), reply("done")))
    await running.message("!spam")
    assert running.texts == ["x", "x", "x", "done"]


async def test_repeat_until(tmp_path):
    loop = block(
        "sff_repeat_until",
        inputs={"COND": compare(variable("n"), "ge", 3), "DO": [change_var("n"), wait(0.001)]},
    )
    running = build(tmp_path, command("go", loop, reply(join("n is ", variable("n")))), variables=("n",))
    await running.message("!go")
    assert running.texts == ["n is 3"]


async def test_the_wait_does_not_block_other_messages(tmp_path):
    import asyncio

    running = build(tmp_path, command("slow", wait(0.05), reply("slow done")), command("fast", reply("fast done")))
    slow = asyncio.create_task(running.message("!slow"))
    await asyncio.sleep(0.005)
    await running.message("!fast")
    assert running.texts == ["fast done"]
    await slow
    assert running.texts == ["fast done", "slow done"]


# --------------------------------------------------------------------------------------------- variables, maths, text


async def test_a_counter_remembers_between_commands(tmp_path):
    running = build(
        tmp_path,
        command("count", change_var("count"), reply(join("Count: ", variable("count")))),
        variables=("count",),
    )
    for _ in range(3):
        await running.message("!count")
    assert running.texts == ["Count: 1", "Count: 2", "Count: 3"]
    assert running.namespace["count"] == 3


async def test_scripts_share_variables(tmp_path):
    running = build(
        tmp_path,
        command("set", set_var("note", command_text())),
        command("get", reply(variable("note"))),
        variables=("note",),
    )
    await running.message("!set remember me")
    await running.message("!get")
    assert running.texts == ["remember me"]


async def test_text_that_spells_a_number_equals_the_number(tmp_path):
    running = build(
        tmp_path,
        command("set", set_var("n", command_text())),
        command("check", if_else(compare(variable("n"), "eq", "5"), [reply("five")], [reply("not five")])),
        variables=("n",),
    )
    await running.message("!set 5")
    await running.message("!check")
    await running.message("!set 6")
    await running.message("!check")
    await running.message("!set five")
    await running.message("!check")
    assert running.texts == ["five", "not five", "not five"]


async def test_numbers_typed_into_a_command(tmp_path):
    total = math_op(block("sff_to_number", inputs={"TEXT": command_text()}), "multiply", 2)
    running = build(tmp_path, command("double", reply(join("Double: ", total))))
    await running.message("!double 21")
    await running.message("!double 2.5")
    await running.message("!double abc")
    assert running.texts == ["Double: 42", "Double: 5.0", "Double: 0"]


async def test_maths(tmp_path):
    cases = [
        (math_op(7, "mod", 3), "1"),
        (math_op(10, "divide", 4), "2.5"),
        (math_op(math_op(1, "add", 2), "multiply", 3), "9"),
        (math_op(1, "subtract", math_op(2, "subtract", 3)), "2"),
        (block("sff_round", fields={"OP": "floor"}, inputs={"N": 2.9}), "2"),
        (block("sff_round", fields={"OP": "ceil"}, inputs={"N": 2.1}), "3"),
        (block("sff_round", fields={"OP": "abs"}, inputs={"N": -4}), "4"),
        (block("sff_round", fields={"OP": "round"}, inputs={"N": 2.6}), "3"),
    ]
    running = build(tmp_path, *[command(f"m{i}", reply(value)) for i, (value, _) in enumerate(cases)])
    for i in range(len(cases)):
        await running.message(f"!m{i} ")
    assert running.texts == [expected for _, expected in cases]


async def test_text_blocks(tmp_path):
    def text_of(type_, **kw):
        return block(type_, **kw)

    cases = [
        (text_of("sff_text_case", fields={"OP": "upper"}, inputs={"TEXT": "hi there"}), "HI THERE"),
        (text_of("sff_text_case", fields={"OP": "lower"}, inputs={"TEXT": "HI There"}), "hi there"),
        (text_of("sff_text_case", fields={"OP": "title"}, inputs={"TEXT": "hi there"}), "Hi There"),
        (text_of("sff_text_case", fields={"OP": "strip"}, inputs={"TEXT": "  hi  "}), "hi"),
        (text_of("sff_replace", inputs={"A": "a", "B": "o", "TEXT": "banana"}), "bonono"),
        (text_of("sff_text_length", inputs={"TEXT": "héllo"}), "5"),
        (text_of("sff_word", inputs={"N": 2, "TEXT": "one two three"}), "two"),
        (text_of("sff_word", inputs={"N": 9, "TEXT": "one two three"}), ""),
        (text_of("sff_word", inputs={"N": 0, "TEXT": "one two three"}), ""),
    ]
    running = build(tmp_path, *[command(f"t{i}", reply(value)) for i, (value, _) in enumerate(cases)])
    for i in range(len(cases)):
        await running.message(f"!t{i} ")
    assert running.texts == [expected for _, expected in cases]


async def test_random_numbers_stay_in_range(tmp_path):
    running = build(
        tmp_path,
        command("roll", reply(block("sff_random", inputs={"A": 1, "B": 6}))),
        command("back", reply(block("sff_random", inputs={"A": 6, "B": 1}))),
        command(
            "calc", reply(block("sff_random", inputs={"A": math_op(1, "add", 1), "B": math_op(10, "subtract", 4)}))
        ),
    )
    for name in ("roll", "back", "calc"):
        for _ in range(40):
            await running.message(f"!{name} ")
    numbers = [int(t) for t in running.texts]
    assert len(numbers) == 120
    assert all(1 <= n <= 6 for n in numbers[:80]) and all(2 <= n <= 6 for n in numbers[80:])
    assert len(set(numbers[:40])) > 1


# --------------------------------------------------------------------------------------------- lists


async def test_lists(tmp_path):
    fruits = {"LIST": lst("fruits")}
    add = lambda item: block("sff_list_add", fields=fruits, inputs={"ITEM": item})  # noqa: E731
    list_blocks = [
        add("apple"),
        add("banana"),
        add("cherry"),
        reply(block("sff_list_join", fields=fruits, inputs={"SEP": ", "})),
        reply(block("sff_list_length", fields=fruits)),
        reply(block("sff_list_item", fields=fruits, inputs={"N": 2})),
        reply(block("sff_list_item", fields=fruits, inputs={"N": 9})),
        if_(block("sff_list_contains", fields=fruits, inputs={"ITEM": "banana"}), reply("has banana")),
        if_(block("sff_list_contains", fields=fruits, inputs={"ITEM": "kiwi"}), reply("has kiwi")),
        block("sff_list_delete", fields=fruits, inputs={"N": 1}),
        reply(block("sff_list_join", fields=fruits, inputs={"SEP": "+"})),
        block("sff_list_clear", fields=fruits),
        reply(block("sff_list_length", fields=fruits)),
    ]
    running = build(tmp_path, command("fruit", *list_blocks), variables=(("fruits", "List"),))
    await running.message("!fruit")
    assert running.texts == ["apple, banana, cherry", "3", "banana", "", "has banana", "banana+cherry", "0"]


async def test_the_list_from_the_last_run_is_still_there(tmp_path):
    fruits = {"LIST": lst("fruits")}
    running = build(
        tmp_path,
        command("add", block("sff_list_add", fields=fruits, inputs={"ITEM": command_text()})),
        command("all", reply(block("sff_list_join", fields=fruits, inputs={"SEP": " & "}))),
        variables=(("fruits", "List"),),
    )
    await running.message("!add kiwi")
    await running.message("!add plum")
    await running.message("!all")
    assert running.texts == ["kiwi & plum"]


async def test_split_and_for_each(tmp_path):
    loop = block(
        "sff_for_each",
        fields={"VAR": lst("word")},
        inputs={
            "LIST": block("sff_list_split", inputs={"TEXT": command_text(), "SEP": ""}),
            "DO": [say(join("> ", variable("word")))],
        },
    )
    running = build(tmp_path, command("each", loop), variables=("word",))
    await running.message("!each red green  blue")
    assert running.texts == ["> red", "> green", "> blue"]


async def test_items_that_look_like_numbers_are_found_in_lists_made_from_text(tmp_path):
    numbers = {"LIST": lst("numbers")}
    running = build(
        tmp_path,
        command(
            "has",
            block(
                "sff_list_set",
                fields=numbers,
                inputs={"VALUE": block("sff_list_split", inputs={"TEXT": "1,2,3", "SEP": ","})},
            ),
            if_(block("sff_list_contains", fields=numbers, inputs={"ITEM": "2"}), reply("found 2")),
            if_(block("sff_list_contains", fields=numbers, inputs={"ITEM": "7"}), reply("found 7")),
        ),
        variables=(("numbers", "List"),),
    )
    await running.message("!has")
    assert running.texts == ["found 2"]


async def test_setting_a_list_to_another_list_makes_a_copy(tmp_path):
    original, copy = {"LIST": lst("original")}, {"LIST": lst("copy")}
    running = build(
        tmp_path,
        command(
            "copy",
            block("sff_list_add", fields=original, inputs={"ITEM": "a"}),
            block("sff_list_set", fields=copy, inputs={"VALUE": list_variable("original")}),
            block("sff_list_add", fields=copy, inputs={"ITEM": "b"}),
            block("sff_list_add", fields=original, inputs={"ITEM": "c"}),
            reply(block("sff_list_join", fields=original, inputs={"SEP": ","})),
            reply(block("sff_list_join", fields=copy, inputs={"SEP": ","})),
        ),
        variables=(("original", "List"), ("copy", "List")),
    )
    await running.message("!copy")
    assert running.texts == ["a,c", "a,b"]


# --------------------------------------------------------------------------------------------- talking to Fluxer


async def test_replies_are_replies_and_sends_are_not(tmp_path):
    running = build(tmp_path, command("both", reply("a reply"), say("a message")))
    await running.message("!both", channel_id=88)
    assert [(s["content"], s["reply"], s["channel"]) for s in running.sent] == [
        ("a reply", True, "88"),
        ("a message", False, "88"),
    ]
    reference = running.http.named("send_message")[0][1]["message_reference"]
    assert reference["channel_id"] == "88"


async def test_send_to_a_channel_by_id(tmp_path):
    running = build(
        tmp_path, on_ready(block("sff_send_channel", inputs={"CHANNEL": "123456789012345678", "TEXT": "I'm up"}))
    )
    await running.ready()
    assert running.http.named("get_channel") == [(("123456789012345678",), {})]
    assert running.sent[0]["content"] == "I'm up" and running.sent[0]["channel"] == "123456789012345678"


async def test_embeds(tmp_path):
    rules = block("sff_send_embed", fields={"COLOR": "green"}, inputs={"TITLE": "Rules", "TEXT": "Be kind."})
    card = block(
        "sff_reply_embed",
        fields={"COLOR": "blue"},
        inputs={"TITLE": "Hi", "TEXT": join("Hello ", block("sff_msg_author_name"))},
    )
    running = build(tmp_path, on_message(rules, card))
    await running.message("x", author="alice")
    first, second = running.sent
    assert first["embeds"][0]["title"] == "Rules" and first["embeds"][0]["description"] == "Be kind."
    assert first["embeds"][0]["color"] == 0x52C987 and first["reply"] is False
    assert second["embeds"][0]["description"] == "Hello alice" and second["embeds"][0]["color"] == 0x176DFF
    assert second["reply"] is True


async def test_reactions_deleting_and_private_messages(tmp_path):
    running = build(
        tmp_path,
        command(
            "all",
            block("sff_react", fields={"EMOJI": "🎉"}),
            block("sff_dm_author", inputs={"TEXT": "psst"}),
            block("sff_delete_message"),
        ),
    )
    await running.message("!all", author_id=42)
    message_id = int(running.http.named("delete_message")[0][0][1])
    assert running.http.named("add_reaction") == [((300, message_id, "🎉"), {})]
    assert running.http.named("create_dm") == [((42,), {})]
    assert running.texts == ["psst"] and running.sent[0]["channel"] == "5555"


async def test_status_and_custom_status(tmp_path):
    running = build(
        tmp_path,
        on_ready(
            block("sff_set_status", fields={"STATUS": "idle"}),
            block("sff_set_custom_status", inputs={"TEXT": "Learning blocks"}),
            block("sff_set_status", fields={"STATUS": "dnd"}),
            block("sff_set_custom_status", inputs={"TEXT": ""}),
        ),
    )
    await running.ready()
    sent = [payload.d for payload in running.gateway.payloads]
    assert [p["status"] for p in sent] == ["idle", "idle", "dnd", "dnd"]
    assert [p["custom_status"] for p in sent] == [None, {"text": "Learning blocks"}, {"text": "Learning blocks"}, None]
    assert all(p["activities"] == [] and p["afk"] is False for p in sent)
    assert all(payload.op == 3 for payload in running.gateway.payloads)  # the gateway's "update presence"


# --------------------------------------------------------------------------------------------- files


async def test_images_and_files_are_attached(tmp_path):
    assets = {"cat.png": "image", "notes.txt": "text"}
    running = build(
        tmp_path,
        command(
            "pic",
            block("sff_reply_image", fields={"FILE": "cat.png"}),
            block("sff_send_image", fields={"FILE": "cat.png"}),
            block("sff_reply_file", fields={"FILE": "notes.txt"}),
            reply(block("sff_file_text", fields={"FILE": "notes.txt"})),
        ),
        assets=assets,
        files={"cat.png": b"\x89PNG-bytes", "notes.txt": "Héllo\n".encode()},
    )
    await running.message("!pic")
    sent = running.sent
    assert sent[0]["files"] == [{"data": b"\x89PNG-bytes", "filename": "cat.png"}] and sent[0]["reply"] is True
    assert sent[1]["files"] == [{"data": b"\x89PNG-bytes", "filename": "cat.png"}] and sent[1]["reply"] is False
    assert sent[2]["files"] == [{"data": "Héllo\n".encode(), "filename": "notes.txt"}]
    assert sent[3]["content"] == "Héllo\n"


async def test_json_files(tmp_path):
    load = block("sff_json_load", fields={"FILE": "quotes.json", "VAR": lst("quotes")})
    pick = lambda path: reply(block("sff_json_get", fields={"VAR": lst("quotes")}, inputs={"KEY": path}))  # noqa: E731
    running = build(
        tmp_path,
        command(
            "quote", load, pick("0.text"), pick("1.by.name"), pick("2"), pick("9.text"), pick("0.nothing"), pick("")
        ),
        assets={"quotes.json": "json"},
        files={"quotes.json": b'[{"text": "Hello", "n": null}, {"by": {"name": "Ada"}}]'},
        variables=("quotes",),
    )
    await running.message("!quote")
    assert running.texts[:5] == ["Hello", "Ada", "", "", ""]
    assert running.texts[5].startswith("[{")  # an empty path gives the whole thing


async def test_json_objects_and_lists_in_loops(tmp_path):
    load = block("sff_json_load", fields={"FILE": "data.json", "VAR": lst("data")})
    loop = block(
        "sff_for_each",
        fields={"VAR": lst("item")},
        inputs={"LIST": list_variable("names"), "DO": [say(variable("item"))]},
    )
    running = build(
        tmp_path,
        command(
            "names",
            load,
            block(
                "sff_list_set",
                fields={"LIST": lst("names")},
                inputs={"VALUE": block("sff_list_variable", fields={"LIST": lst("data")})},
            ),
            loop,
        ),
        assets={"data.json": "json"},
        files={"data.json": b'["a", "b", "c"]'},
        variables=(("data", "List"), ("names", "List"), "item"),
    )
    await running.message("!names")
    assert running.texts == ["a", "b", "c"]


# --------------------------------------------------------------------------------------------- the console


async def test_console_levels(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    steps = [
        block("sff_console_log", fields={"LEVEL": level}, inputs={"TEXT": f"a {level} 100%"})
        for level in ("info", "warning", "error")
    ]
    running = build(tmp_path, on_ready(*steps))
    await running.ready()
    records = [(r.name, r.levelname, r.getMessage()) for r in caplog.records if r.name == "bot"]
    assert records == [
        ("bot", "INFO", "a info 100%"),
        ("bot", "WARNING", "a warning 100%"),
        ("bot", "ERROR", "a error 100%"),
    ]


async def test_a_mistake_in_one_script_is_logged_and_the_bot_carries_on(tmp_path, caplog):
    running = build(tmp_path, command("boom", reply(math_op(1, "divide", 0))), command("ok", reply("still here")))
    await running.message("!boom")
    await running.message("!ok")
    assert running.texts == ["still here"]
    assert any("ZeroDivisionError" in (r.exc_text or "") for r in caplog.records)
    assert any("Error in command 'boom'" in r.getMessage() for r in caplog.records)
