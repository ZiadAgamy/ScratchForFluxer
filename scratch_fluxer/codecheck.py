"""A last safety check on the Python the compiler wrote.

The compiler is a whitelist, so nothing a person types can become code. This module does not trust that: it reads
the finished file with ``ast`` and refuses anything the compiler is not supposed to write. It runs

* after every compile (a compiler bug becomes an error message instead of unsafe code), and
* again before a bot is started, on the exact file that will run.

The rules are deny-by-default. Only the statements, expressions, imports, built-ins, module functions and method names
that the compiler's templates use are allowed. The bot token may be read in exactly one place, as the only argument
of ``bot.run(...)`` that ends the file, so no block can print it, send it or store it.
"""

from __future__ import annotations

import ast
import builtins
import re

from .assets import is_safe_name

ALLOWED_IMPORTS = frozenset({"asyncio", "datetime", "json", "logging", "math", "os", "random", "time", "fluxer"})

#: Built-in names the generated code may use. Everything else in ``dir(builtins)`` is refused.
ALLOWED_BUILTINS = frozenset(
    {
        "print",
        "len",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "range",
        "round",
        "abs",
        "sorted",
        "any",
        "isinstance",
        "Exception",
        "TypeError",
        "ValueError",
        "KeyError",
        "IndexError",
    }
)

#: What may be taken from each imported module (``fluxer`` is the library the bot runs on).
MODULE_ATTRS: dict[str, frozenset[str]] = {
    "os": frozenset({"environ"}),
    "asyncio": frozenset({"sleep"}),
    "datetime": frozenset({"datetime", "timezone", "timedelta"}),
    "json": frozenset({"loads"}),
    "logging": frozenset({"basicConfig", "getLogger", "INFO"}),
    "math": frozenset({"floor", "ceil"}),
    "random": frozenset({"randint", "choice"}),
    "time": frozenset({"monotonic"}),
    "fluxer": frozenset({"Bot", "Intents", "Embed", "File", "GuildMember", "GatewayOpcode", "gateway"}),
}

#: Attribute and method names on everything else (messages, members, strings, lists, loggers, the bot).
ALLOWED_ATTRS = frozenset(
    {
        # fluxer objects
        "content",
        "author",
        "display_name",
        "mention",
        "id",
        "channel_id",
        "bot",
        "user",
        "username",
        "guilds",
        "guild_id",
        "reply",
        "send",
        "add_reaction",
        "delete",
        "fetch_channel",
        "fetch_user",
        "fetch_guild",
        "fetch_member",
        "add_role",
        "remove_role",
        "kick",
        "ban",
        "unban",
        "timeout",
        "roles",
        "mentions",
        "emoji",
        "unicode",
        "name",
        "message_id",
        "user_id",
        "now",
        "utc",
        "isoformat",
        "isdigit",
        "isalnum",
        "edit",
        "fetch_message",
        "delete_message",
        "trigger_typing",
        "to_dict",
        "set_image",
        "set_thumbnail",
        "set_author",
        "set_footer",
        "execute_webhook",
        "create_guild_channel",
        "delete_channel",
        "create_guild_role",
        "delete_guild_role",
        "send_message",
        "request",
        "startswith",
        "event",
        "command",
        "on",
        "default",
        "from_data",
        "GatewayPayload",
        "PRESENCE_UPDATE",
        # strings, lists, dicts, paths, loggers
        "lower",
        "upper",
        "title",
        "strip",
        "split",
        "replace",
        "join",
        "append",
        "clear",
        "update",
        "read_text",
        "parent",
        "info",
        "warning",
        "error",
    }
)
#: The methods of ``bot._http`` a bot file may call by name (``helpers_manage.py`` and the webhook helper use them).
HTTP_METHODS = frozenset(
    {
        "execute_webhook",
        "create_guild_channel",
        "delete_channel",
        "create_guild_role",
        "delete_guild_role",
        "send_message",
    }
)
#: The only requests a bot file may make with ``bot._http.request(bot._http._route(METHOD, PATH, ...), json=...)``.
HTTP_ROUTES = frozenset(
    {
        ("POST", "/channels/{channel_id}/threads"),
        ("POST", "/channels/{channel_id}/messages/{message_id}/threads"),
        ("PATCH", "/channels/{channel_id}"),
        ("GET", "/channels/{channel_id}"),
        ("POST", "/channels/{channel_id}/tags"),
        ("DELETE", "/channels/{channel_id}/tags/{tag_id}"),
    }
)
_ROUTE_PARTS = frozenset({"channel_id", "message_id", "tag_id"})

#: Private names the presence helper needs (fluxer.py has no public way to set a custom status yet).
PRIVATE_ATTRS = frozenset({"_gateway", "_send"})

#: The name a picture is sent under when an embed shows it: image.png or thumbnail.jpg.
_PLAIN_FILENAME = re.compile(r"^(image|thumbnail)\.[a-z0-9]{1,5}$")

DECORATOR_METHODS = frozenset({"event", "command", "on"})
CONSTANT_TYPES = (str, int, float, bool, type(None))

#: The AST node types the generated code is made of.
ALLOWED_NODES = (
    ast.Module,
    ast.Import,
    ast.ImportFrom,
    ast.alias,
    ast.Assign,
    ast.AugAssign,
    ast.Expr,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.arguments,
    ast.arg,
    ast.Return,
    ast.If,
    ast.For,
    ast.While,
    ast.Pass,
    ast.Continue,
    ast.Break,
    ast.Global,
    ast.Delete,
    ast.Try,
    ast.ExceptHandler,
    ast.Constant,
    ast.Name,
    ast.Attribute,
    ast.Call,
    ast.keyword,
    ast.Subscript,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.IfExp,
    ast.Compare,
    ast.Tuple,
    ast.List,
    ast.Dict,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.GeneratorExp,
    ast.comprehension,
    ast.Await,
    ast.Load,
    ast.Store,
    ast.Del,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.USub,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
)


class UnsafeCode(ValueError):
    """The code contains something the compiler is not supposed to write."""

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(f"line {line}: {message}" if line else message)
        self.line = line


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _is_token_read(node: ast.AST) -> bool:
    """``os.environ["FLUXER_TOKEN"]``"""
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and _is_name(node.value.value, "os")
        and node.value.attr == "environ"
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == "FLUXER_TOKEN"
    )


def _is_asset_path(node: ast.AST) -> bool:
    """``ASSETS / "photo.png"``: a file in the bot's assets folder, named in the code, never worked out while running."""
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Div)
        and _is_name(node.left, "ASSETS")
        and isinstance(node.right, ast.Constant)
        and is_safe_name(node.right.value)
    )


def _is_login(node: ast.AST) -> bool:
    """``bot.run(os.environ["FLUXER_TOKEN"])``"""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and _is_name(node.func.value, "bot")
        and node.func.attr == "run"
        and len(node.args) == 1
        and not node.keywords
        and _is_token_read(node.args[0])
    )


def _is_bot_http(node: ast.AST) -> bool:
    """``bot._http``"""
    return isinstance(node, ast.Attribute) and node.attr == "_http" and _is_name(node.value, "bot")


class _Checker(ast.NodeVisitor):
    def __init__(self, bound: set[str]) -> None:
        self.bound = bound
        self.login: ast.Call | None = None  # the final `bot.run(os.environ["FLUXER_TOKEN"])`, the only place for either
        self.http_ok: set[int] = set()  # the `bot._http` in `bot._http.execute_webhook(...)`, the only use of it
        self.route_ok: set[int] = set()  # the `_route` in `bot._http.request(bot._http._route(...))`

    @staticmethod
    def fail(node: ast.AST, message: str) -> None:
        raise UnsafeCode(message, getattr(node, "lineno", None))

    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, ALLOWED_NODES):
            self.fail(node, f"{type(node).__name__} is not allowed in a bot file")
        super().generic_visit(node)

    def check_bound(self, node: ast.AST, name: str) -> None:
        if name.startswith("__"):
            self.fail(node, f"the name {name!r} is not allowed")
        if name in dir(builtins):
            self.fail(node, f"{name!r} would hide a built-in name")
        if name in ALLOWED_IMPORTS:
            self.fail(node, f"{name!r} would hide an imported module")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name not in ALLOWED_IMPORTS or alias.asname is not None:
                self.fail(node, f"import {alias.name} is not allowed")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module != "pathlib" or node.level != 0 or [a.name for a in node.names] != ["Path"]:
            self.fail(node, "only `from pathlib import Path` is allowed")
        if any(alias.asname is not None for alias in node.names):
            self.fail(node, "`as` is not allowed in imports")

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, CONSTANT_TYPES):
            self.fail(node, "this kind of value is not allowed")

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store | ast.Del):
            self.check_bound(node, node.id)
        elif node.id != "__file__" and node.id not in self.bound and node.id not in ALLOWED_BUILTINS:
            self.fail(node, f"the name {node.id!r} is not defined in the file")

    def in_login(self, node: ast.AST) -> bool:
        """True for the parts of the final login call: ``bot.run`` and the ``os.environ`` inside it."""
        login = self.login
        if login is None:
            return False
        return node is login.func or (isinstance(login.args[0], ast.Subscript) and node is login.args[0].value)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if self.in_login(node):
            self.generic_visit(node)
            return
        if attr == "run" or _is_name(node.value, "os"):
            self.fail(node, "the bot token may only be read by the last line of the file, to log in")
        if attr.startswith("_"):
            allowed = (attr == "_http" and id(node) in self.http_ok) or (attr == "_route" and id(node) in self.route_ok)
            if attr not in PRIVATE_ATTRS and not allowed:
                self.fail(node, f"the attribute {attr!r} is not allowed")
        elif isinstance(node.value, ast.Name) and node.value.id in MODULE_ATTRS:
            if attr not in MODULE_ATTRS[node.value.id]:
                self.fail(node, f"{node.value.id}.{attr} is not allowed")
        elif attr not in ALLOWED_ATTRS:
            self.fail(node, f"the attribute {attr!r} is not allowed")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and _is_bot_http(func.value):
            if func.attr in HTTP_METHODS:
                self.http_ok.add(id(func.value))
            elif func.attr == "request":
                self.check_request(node)
        if not isinstance(node.func, ast.Name | ast.Attribute):
            self.fail(node, "only named functions and methods may be called")
        if any(keyword.arg is None for keyword in node.keywords):
            self.fail(node, "** arguments are not allowed in calls")
        if any(isinstance(arg, ast.Starred) for arg in node.args):
            self.fail(node, "* arguments are not allowed in calls")
        self.generic_visit(node)
        self.check_file_access(node)

    def check_request(self, node: ast.Call) -> None:
        """``bot._http.request(bot._http._route("POST", "/channels/{channel_id}/threads", channel_id=...), json=...)``:
        one of the routes in ``HTTP_ROUTES``, with every ID made by ``str(as_id(...))``."""
        func = node.func
        assert isinstance(func, ast.Attribute)
        route = node.args[0] if len(node.args) == 1 else None
        if not (
            isinstance(route, ast.Call)
            and isinstance(route.func, ast.Attribute)
            and route.func.attr == "_route"
            and _is_bot_http(route.func.value)
        ):
            self.fail(node, "bot._http.request may only be used with bot._http._route(...)")
        assert isinstance(route, ast.Call) and isinstance(route.func, ast.Attribute)
        if any(kw.arg != "json" for kw in node.keywords):
            self.fail(node, "bot._http.request only takes json=")
        constants = [arg.value if isinstance(arg, ast.Constant) else None for arg in route.args]
        if len(constants) != 2 or (constants[0], constants[1]) not in HTTP_ROUTES:
            self.fail(node, "this request to Fluxer is not one a bot file may make")
        for kw in route.keywords:
            made_of_id = (
                kw.arg in _ROUTE_PARTS
                and isinstance(kw.value, ast.Call)
                and _is_name(kw.value.func, "str")
                and len(kw.value.args) == 1
                and isinstance(kw.value.args[0], ast.Call)
                and _is_name(kw.value.args[0].func, "as_id")
            )
            if not made_of_id:
                self.fail(node, "the IDs in a request must be written as str(as_id(...))")
        self.http_ok.add(id(func.value))
        self.http_ok.add(id(route.func.value))
        self.route_ok.add(id(route.func))

    def check_file_access(self, node: ast.Call) -> None:
        """Files are reached in three ways only, all through the assets folder and a name fixed in the code."""
        func = node.func
        if _is_name(func, "Path") and not (
            len(node.args) == 1 and not node.keywords and _is_name(node.args[0], "__file__")
        ):
            self.fail(node, "Path() may only be used as Path(__file__)")
        if isinstance(func, ast.Attribute):
            if func.attr == "read_text" and not _is_asset_path(func.value):
                self.fail(node, "only files in the assets folder can be read, by a name written in the blocks")
            if func.attr == "File" and _is_name(func.value, "fluxer"):
                plain_name = all(
                    kw.arg == "filename"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                    and _PLAIN_FILENAME.match(kw.value.value)
                    for kw in node.keywords
                )
                if len(node.args) != 1 or len(node.keywords) > 1 or not plain_name or not _is_asset_path(node.args[0]):
                    self.fail(node, "only files in the assets folder can be sent, by a name written in the blocks")

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Div):
            if _is_name(node.left, "ASSETS") and not _is_asset_path(node):
                self.fail(node, "a file in the assets folder needs a plain name written in the blocks")
            right = node.right
            if isinstance(right, ast.Constant) and isinstance(right.value, str):
                if right.value != "assets" and not is_safe_name(right.value):
                    self.fail(node, "that is not a name an uploaded file can have")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.slice, ast.Slice):
            self.fail(node, "slices are not allowed")
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        if None in node.keys:
            self.fail(node, "** is not allowed in a dict")
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        if not all(isinstance(target, ast.Subscript) for target in node.targets):
            self.fail(node, "only items of a list may be deleted")
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        for name in node.names:
            self.check_bound(node, name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            self.fail(node, "`except ... as` is not allowed")
        self.generic_visit(node)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.check_bound(node, node.name)
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            ok = (
                isinstance(target, ast.Attribute) and _is_name(target.value, "bot") and target.attr in DECORATOR_METHODS
            )
            if not ok:
                self.fail(decorator, "only @bot.event, @bot.command() and @bot.on() may decorate a function")
        args = node.args
        if args.posonlyargs or args.vararg:
            self.fail(node, "this kind of parameter is not allowed")
        defaults = [*args.defaults, *(default for default in args.kw_defaults if default is not None)]
        if any(not isinstance(default, ast.Constant) for default in defaults):
            self.fail(node, "parameter defaults must be plain values")
        for arg in [*args.args, *args.kwonlyargs, *([args.kwarg] if args.kwarg else [])]:
            self.check_bound(arg, arg.arg)
            if arg.annotation is not None and not _is_name(arg.annotation, "str"):
                self.fail(arg, "only `str` annotations are allowed")
        if node.returns is not None:
            self.fail(node, "return annotations are not allowed")
        for child in [*node.decorator_list, args.defaults, args.kw_defaults, node.body]:
            for item in child if isinstance(child, list) else [child]:
                if item is not None:
                    self.visit(item)

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_Module(self, node: ast.Module) -> None:
        last = node.body[-1] if node.body else None
        if not (isinstance(last, ast.Expr) and _is_login(last.value)):
            self.fail(node, 'the file must end with bot.run(os.environ["FLUXER_TOKEN"])')
        self.login = last.value  # type: ignore[assignment]
        self.generic_visit(node)


def _bound_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
            args = node.args
            names.update(arg.arg for arg in [*args.args, *args.kwonlyargs, *([args.kwarg] if args.kwarg else [])])
    return names


def check(code: str) -> None:
    """Raises ``UnsafeCode`` if the file holds anything the compiler isn't meant to write. Returns if it is fine."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise UnsafeCode(f"the file doesn't parse: {exc}") from exc
    try:
        _Checker(_bound_names(tree)).visit(tree)
    except RecursionError as exc:
        raise UnsafeCode("the file is nested too deeply") from exc
