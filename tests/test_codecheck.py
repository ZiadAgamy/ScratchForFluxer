"""The safety check refuses anything the compiler isn't supposed to write, and accepts everything it does write."""

from __future__ import annotations

import pytest
from blocks import block, command, on_join, on_message, on_ready, reply, show, workspace

from scratch_fluxer import codecheck
from scratch_fluxer.compiler import HELPER_ORDER, HELPERS, compile_workspace, default_workspace

LOGIN = 'bot.run(os.environ["FLUXER_TOKEN"])\n'
HEAD = "import os\n\nimport fluxer\n\nbot = fluxer.Bot(command_prefix='!', intents=fluxer.Intents.default())\n\n"


def wrap(middle: str, *modules: str) -> str:
    """A whole file around ``middle``: imports (``os`` and any extra ones), the bot, then the login."""
    imports = "".join(f"import {name}\n" for name in sorted({"os", *modules}))
    head = HEAD.replace("import os\n", imports, 1)
    return head + middle + "\n\n" + LOGIN


def with_assets(statement: str) -> str:
    """``statement`` after the lines that define ``ASSETS`` the way the compiler does."""
    return 'from pathlib import Path\n\nASSETS = Path(__file__).parent / "assets"\n' + statement


def helper_with_what_it_needs(name: str) -> tuple[str, tuple[str, ...]]:
    """The source of one helper after the helpers it calls (in the order the compiler writes them), and its imports."""
    wanted = {name}
    for item in list(wanted):
        wanted.update(HELPERS[item]["needs"])
    ordered = [item for item in HELPER_ORDER if item in wanted]
    source = "\n\n".join(HELPERS[item]["source"].rstrip("\n") + "\n" for item in ordered)
    return source, tuple(module for item in ordered for module in HELPERS[item]["imports"])


def refused(code: str, fragment: str | None = None) -> None:
    with pytest.raises(codecheck.UnsafeCode) as caught:
        codecheck.check(code)
    if fragment:
        assert fragment in str(caught.value), str(caught.value)


class TestWhatIsAccepted:
    def test_the_starting_bot(self):
        codecheck.check(compile_workspace(default_workspace()).code)

    @pytest.mark.parametrize("name", HELPER_ORDER)
    def test_every_helper(self, name):
        source, modules = helper_with_what_it_needs(name)
        codecheck.check(wrap(source, *modules))

    def test_files_are_reached_through_the_assets_folder_by_a_fixed_name(self):
        code = wrap(
            'ASSETS = Path(__file__).parent / "assets"\n\n\n'
            "@bot.event\n"
            "async def on_ready():\n"
            '    data = json.loads((ASSETS / "scores.json").read_text(encoding="utf-8"))\n'
            '    note = (ASSETS / "notes (1).txt").read_text(encoding="utf-8")\n'
            '    await bot.fetch_channel("1")\n'
            '    picture = fluxer.File(ASSETS / "cat.png")\n'
            "    print(data, note, picture)",
            "json",
        ).replace("import os\n", "import os\nfrom pathlib import Path\n", 1)
        codecheck.check(code)

    def test_a_handler_with_loops_and_a_shared_counter(self):
        handler = (
            "count = 0\n\n\n"
            "@bot.event\n"
            "async def on_ready():\n"
            "    global count\n"
            "    count = 0\n"
            "    for _ in range(3):\n"
            "        count += 1\n"
            "        await asyncio.sleep(0)\n"
            "    while True:\n"
            "        break\n"
            '    print(f"{count}" + str(count))'
        )
        codecheck.check(wrap(handler, "asyncio"))


class TestWhatIsRefused:
    @pytest.mark.parametrize(
        ("middle", "fragment"),
        [
            ("import subprocess", "import subprocess is not allowed"),
            ("import sys", "import sys is not allowed"),
            ("import os as o", "is not allowed"),
            ("from os import system", "only `from pathlib import Path`"),
            ("from pathlib import Path as P", "`as` is not allowed"),
            ("from . import x", "only `from pathlib import Path`"),
            ("open('x')", "'open' is not defined"),
            ("exec('1')", "'exec' is not defined"),
            ("eval('1')", "'eval' is not defined"),
            ("compile('1', 'x', 'exec')", "'compile' is not defined"),
            ("__import__('os')", "'__import__' is not defined"),
            ("getattr(bot, 'run')", "'getattr' is not defined"),
            ("globals()", "'globals' is not defined"),
            ("x = os.system('id')", "the bot token may only be read by the last line"),
            ("x = os.getenv('A')", "the bot token may only be read by the last line"),
            ("x = os.environ", "the bot token may only be read by the last line"),
            ("x = os.environ.get('FLUXER_TOKEN')", "is not allowed"),
            ("os.environ.update({'A': 'b'})", "the bot token may only be read by the last line"),
            ("x = os.environ['HOME']", "the bot token may only be read by the last line"),
            ("print(os.environ['FLUXER_TOKEN'])", "the bot token may only be read by the last line"),
            ("x = bot.run", "the bot token may only be read by the last line"),
            ("bot.run('some-other-token')", "the bot token may only be read by the last line"),
            ("x = (1).__class__", "'__class__' is not allowed"),
            ("x = ''.__class__.__mro__", "is not allowed"),
            ("x = 'a'.format(1)", "'format' is not allowed"),
            ("x = bot._http", "'_http' is not allowed"),
            ("x = fluxer.Client", "fluxer.Client is not allowed"),
            ("x = fluxer.http", "fluxer.http is not allowed"),
            ("x = json.load", "json.load is not allowed"),
            ("x = asyncio.create_subprocess_exec", "is not allowed"),
            ("lambda: 1", "Lambda is not allowed"),
            ("class A:\n    pass", "ClassDef is not allowed"),
            ("with open('x') as f:\n    pass", "With is not allowed"),
            ("x = [i for i in range(3)]", "ListComp is not allowed"),
            ("x = {1, 2}", "Set is not allowed"),
            ("x = b'bytes'", "this kind of value is not allowed"),
            ("x = 1j", "this kind of value is not allowed"),
            ("x = ...", "this kind of value is not allowed"),
            ("x = print(*[1])", "* arguments are not allowed"),
            ("x = print(**{})", "** arguments are not allowed"),
            ("x = {**{}}", "** is not allowed in a dict"),
            ("x = [1][1:2]", "slices are not allowed"),
            ("(lambda: 1)()", "only named functions and methods may be called"),
            ("x = bot.fetch_channel('1')()", "only named functions and methods may be called"),
            ("raise ValueError()", "Raise is not allowed"),
            ("assert True", "Assert is not allowed"),
            ("async def f():\n    async with bot:\n        pass", "AsyncWith is not allowed"),
            ("def f():\n    yield 1", "Yield is not allowed"),
            ("del bot", "only items of a list may be deleted"),
            ("x = y", "'y' is not defined"),
            ("def open():\n    pass", "would hide a built-in name"),
            ("open = 1", "would hide a built-in name"),
            ("os = 1", "would hide an imported module"),
            ("def __init__():\n    pass", "is not allowed"),
            ("x = 1\nglobal __builtins__", "is not allowed"),
            ("@bot.something\nasync def f():\n    pass", "may decorate"),
            ("@print\nasync def f():\n    pass", "may decorate"),
            ("async def f(*args):\n    pass", "this kind of parameter is not allowed"),
            ("async def f(a=print):\n    pass", "parameter defaults must be plain values"),
            ("async def f(a: int):\n    pass", "only `str` annotations"),
            ("async def f() -> int:\n    pass", "return annotations are not allowed"),
            ("try:\n    pass\nexcept ValueError as e:\n    pass", "`except ... as` is not allowed"),
            ("x = 1 if True else 2 if False else (yield)", "is not allowed"),
            ("from pathlib import Path\nx = Path('/etc/passwd')", "Path() may only be used as Path(__file__)"),
            ("from pathlib import Path\nx = Path(__file__, 'x')", "Path() may only be used as Path(__file__)"),
            ("from pathlib import Path\nx = Path(__file__).read_text()", "only files in the assets folder can be read"),
            (with_assets("x = ASSETS.read_text()"), "only files in the assets folder can be read"),
            (with_assets("x = (ASSETS / '../x.txt').read_text()"), "needs a plain name written in the blocks"),
            (with_assets("x = (ASSETS / '/etc/passwd').read_text()"), "needs a plain name written in the blocks"),
            (with_assets("x = (ASSETS / 'a.py').read_text()"), "needs a plain name written in the blocks"),
            (with_assets("x = (ASSETS / 'sub/a.txt').read_text()"), "needs a plain name written in the blocks"),
            (
                with_assets("x = (ASSETS / 'a.txt' / 'b.txt').read_text()"),
                "only files in the assets folder can be read",
            ),
            (with_assets("y = 'a.txt'\nx = (ASSETS / y).read_text()"), "needs a plain name written in the blocks"),
            (with_assets("x = (ASSETS / str(1)).read_text()"), "needs a plain name written in the blocks"),
            ("x = fluxer.File('/etc/passwd')", "only files in the assets folder can be sent"),
            ("x = fluxer.File(fp='a.png')", "only files in the assets folder can be sent"),
            (with_assets("x = fluxer.File(ASSETS / 'a.png', 'b')"), "only files in the assets folder can be sent"),
            (
                with_assets("x = fluxer.File(ASSETS / 'a.png', filename='b.png')"),
                "only files in the assets folder can be sent",
            ),
        ],
    )
    def test_each_rule(self, middle, fragment):
        refused(wrap(middle), fragment)

    def test_the_file_must_log_in_exactly_at_the_end(self):
        refused(HEAD, 'must end with bot.run(os.environ["FLUXER_TOKEN"])')
        refused(HEAD + LOGIN + "print('after')\n", "must end with")
        refused(HEAD + 'bot.run("literal-token")\n', "must end with")
        refused(HEAD + 'bot.run(os.environ["FLUXER_TOKEN"], reconnect=False)\n', "must end with")
        refused(HEAD + 'bot.run(os.environ["OTHER"])\n', "must end with")
        refused(
            HEAD + "async def f():\n    bot.run(os.environ['FLUXER_TOKEN'])\n" + LOGIN,
            "the bot token may only be read by the last line",
        )

    def test_code_that_does_not_parse(self):
        refused("def (:\n", "doesn't parse")
        refused("x = 1\x00\n", "doesn't parse")
        refused("x = " + "(" * 500 + "1" + ")" * 500 + "\n", None)

    def test_deeply_nested_code_is_refused_not_crashed_on(self):
        code = wrap("x = " + "-" * 3000 + "1")
        refused(code, None)

    def test_the_error_names_the_line(self):
        with pytest.raises(codecheck.UnsafeCode) as caught:
            codecheck.check(wrap("x = os.system('id')"))
        assert caught.value.line == 7
        assert str(caught.value).startswith("line 7:")


class TestTheCompilerNeverWritesWhatTheCheckRefuses:
    def test_a_workspace_full_of_text_that_looks_like_code(self):
        nasty = '"); import os; os.system("id"); ("'
        ws = workspace(
            on_message(reply(nasty), show(nasty)),
            command(nasty, reply("x")),
            on_join(block("sff_send_channel", inputs={"CHANNEL": nasty, "TEXT": nasty})),
            on_ready(show("FLUXER_TOKEN"), show('os.environ["FLUXER_TOKEN"]')),
        )
        result = compile_workspace(ws)
        codecheck.check(result.code)
        assert result.code.count("os.environ") == 2  # the login, and one piece of plain text
