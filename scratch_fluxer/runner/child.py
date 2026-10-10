"""The program every bot starts through:  python -I -u -B -X utf8 child.py <main.py>

It sets the limits on this process, makes the log lines easy for the runner to read, and then runs the bot's file as if
it had been started with ``python main.py``. It only uses the standard library (and ``fluxer`` when a different Fluxer
address was asked for), and it runs inside the bot's own process, so nothing here needs to be careful about the runner.

What it writes to the error output is a plain, line-based format:

    LEVEL <tab> logger <tab> message          the first line of a log record
    <four spaces>more lines of the same record (a traceback, a message that has line breaks)
    @@sff ready                               the bot connected to Fluxer

What the bot ``print``s goes to the normal output, untouched.
"""

import contextlib
import faulthandler
import logging
import os
import runpy
import signal
import sys
import threading
import time
import traceback

READY = "@@sff ready"
QUIET = ("asyncio", "aiohttp.client", "aiohttp.internal", "aiohttp.server", "aiohttp.web", "aiohttp.websocket")


def number(name):
    try:
        return max(0, int(os.environ.get(name, "")))
    except ValueError:
        return 0


def set_limits():
    try:
        import resource
    except ImportError:  # Windows has no limits like these
        return

    def cap(which, soft, hard=None):
        try:
            resource.setrlimit(which, (soft, soft if hard is None else hard))
        except (ValueError, OSError):
            pass  # not something this system has, or already lower

    mb = 1024 * 1024
    cap(resource.RLIMIT_CORE, 0)
    cap(resource.RLIMIT_NOFILE, 256)
    if number("SFF_FILE_MB"):
        cap(resource.RLIMIT_FSIZE, number("SFF_FILE_MB") * mb)
    if number("SFF_MEMORY_MB") and hasattr(resource, "RLIMIT_DATA"):
        cap(resource.RLIMIT_DATA, number("SFF_MEMORY_MB") * mb)  # memory the bot has written to, not address space
    if number("SFF_CPU_SECONDS"):
        cap(resource.RLIMIT_CPU, number("SFF_CPU_SECONDS"), number("SFF_CPU_SECONDS") + 10)
    if number("SFF_MAX_PROCESSES") and hasattr(resource, "RLIMIT_NPROC"):
        # Counted for the whole user id, so it is only set when the id is this bot's own.
        cap(resource.RLIMIT_NPROC, number("SFF_MAX_PROCESSES"))


class Format(logging.Formatter):
    def format(self, record):
        text = record.getMessage()
        if record.exc_info and record.exc_info[0] is not None:
            text += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
        if record.stack_info:
            text += "\n" + record.stack_info
        first, *rest = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        name = record.name.replace("\t", " ")[:60]
        return "\n".join([f"{record.levelname}\t{name}\t{first}", *("    " + line for line in rest)])


class Handler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        # Fluxer says "READY: ..." when it has accepted the bot. That is the moment the bot is online.
        if record.name == "fluxer.gateway" and record.getMessage().startswith("READY:"):
            with contextlib.suppress(Exception):
                self.stream.write(READY + "\n")
                self.flush()


class DropShutdownNoise(logging.Filter):
    """When a bot is stopped, aiohttp complains about network sessions that were not closed first. The blocks never
    open one themselves, and there is nothing a person can do about the library's own, so these two are left out."""

    def filter(self, record):
        return not record.getMessage().startswith(("Unclosed client session", "Unclosed connector"))


def set_up_logging():
    handler = Handler(sys.stderr)
    handler.setFormatter(Format())
    handler.addFilter(DropShutdownNoise())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    logging.captureWarnings(True)
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
    faulthandler.enable(file=sys.stderr)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


def use_another_fluxer():
    """``SFF_FLUXER_API_URL`` points the bot at a Fluxer other than the default (a self-hosted one, or a test server)."""
    url = os.environ.get("SFF_FLUXER_API_URL", "").strip()
    if not url:
        return
    import fluxer

    original = fluxer.Client.__init__

    def init(self, *args, api_url=None, **kwargs):
        original(self, *args, api_url=api_url or url, **kwargs)

    init.__doc__ = original.__doc__
    fluxer.Client.__init__ = init


def set_up_signals():
    """Stop (the runner sends Ctrl+C) and terminate both end the bot the way Ctrl+C does: it closes its connection."""
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        signal.signal(signal.SIGTERM, signal.default_int_handler)
    except (ValueError, OSError):
        pass


def die_with_the_runner():
    """If the runner goes away without stopping this bot, stop too (nobody could ever stop it otherwise). The runner
    says which process it is, so a runner that is already gone by the time this runs is noticed as well."""
    try:
        parent = int(os.environ.pop("SFF_PARENT_PID", ""))
    except ValueError:
        parent = os.getppid()
    if os.getppid() != parent:
        os._exit(1)

    def watch():
        while True:
            time.sleep(1.0)
            if os.getppid() != parent:
                os._exit(1)

    threading.Thread(target=watch, daemon=True).start()


def belongs_to_the_runner(filename):
    """This file, and runpy, which starts the bot's file (newer Pythons call its file ``<frozen runpy>``)."""
    return filename == __file__ or filename == "<frozen runpy>" or filename.endswith(os.sep + "runpy.py")


def report(error):
    """Say why the bot stopped, in the format above, without the frames that belong to the runner."""
    summary = traceback.TracebackException.from_exception(error)
    summary.stack = traceback.StackSummary.from_list(
        [frame for frame in summary.stack if not belongs_to_the_runner(frame.filename)]
    )
    details = "".join(summary.format()).rstrip()
    logging.getLogger("sff").error("Your bot stopped because of an error.\n%s", details)


def become_the_bots_user():
    """Started as root with ``SFF_RUN_AS=<user id>:<group id>``, this is where the process stops being root, before it
    reads anything the bot wrote and before it sets up anything else. It gives up the groups of the runner as well, and
    makes sure that there is no way back. If any of it fails, the bot does not run: it must never run as root."""
    wanted = os.environ.pop("SFF_RUN_AS", "")
    if not wanted:
        return
    try:
        uid, gid = (int(part) for part in wanted.split(":"))
        if uid <= 0 or gid <= 0:
            raise ValueError("that is root, or not a user at all")
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
        if (os.getuid(), os.geteuid(), os.getgid(), os.getegid()) != (uid, uid, gid, gid) or os.getgroups():
            raise OSError("the change did not take effect")
        try:
            os.setuid(0)
        except PermissionError:
            return
        raise OSError("it could change back to root")
    except (ValueError, OverflowError, OSError) as error:
        sys.stderr.write(
            "ERROR\tsff\tThe runner could not start your bot as a user of its own "
            f"({error}). Whoever runs this site needs to look at the runner's settings.\n"
        )
        sys.stderr.flush()
        os._exit(1)


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("ERROR\tsff\tUsage: child.py <main.py>\n")
        return 2
    become_the_bots_user()
    set_limits()
    set_up_logging()
    set_up_signals()
    die_with_the_runner()
    try:
        use_another_fluxer()
        runpy.run_path(sys.argv[1], run_name="__main__")
    except KeyboardInterrupt:
        return 0
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 0
    except BaseException as error:  # noqa: BLE001 - whatever stopped the bot is reported, then the process ends
        report(error)
        return 1
    return 0


if __name__ == "__main__":
    code = main()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    os._exit(code)  # skips the teardown, where half-closed network sessions would complain
