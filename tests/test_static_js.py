"""Our own browser scripts never turn text into markup or code.

The page's content-security-policy already refuses inline scripts, but one `innerHTML = someText` would still let a bot
name, a file name or a log line inject markup. The scripts build their DOM with createElement and textContent instead,
and this test keeps it that way. (Vendored libraries live in public/static/vendor and are not checked here.)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scratch_fluxer.runner.clients import OFF_MESSAGE

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "public" / "static" / "js"

FORBIDDEN = {
    "innerHTML": r"\.innerHTML\b",
    "outerHTML": r"\.outerHTML\b",
    "insertAdjacentHTML": r"\binsertAdjacentHTML\s*\(",
    "document.write": r"\bdocument\.write(?:ln)?\s*\(",
    "eval": r"\beval\s*\(",
    "new Function": r"\bnew\s+Function\s*\(",
    "a string handed to setTimeout/setInterval": r"\bset(?:Timeout|Interval)\s*\(\s*['\"`]",
    "a javascript: URL": r"javascript:",
    "srcdoc": r"\.srcdoc\b",
}


def code_only(source: str) -> str:
    """The script without its comments, so a comment that says "never use innerHTML" does not trip the check."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)(^|\s)//.*$", r"\1", source)  # a `//` that starts a word, so "https://" survives


SCRIPTS = sorted(JS_DIR.rglob("*.js"))  # includes js/editor/


def test_there_are_scripts_to_check():
    names = {path.name for path in SCRIPTS}
    assert {"app.js", "bots.js", "onboard.js", "landing.js"} <= names
    assert {"main.js", "blocks.js", "files.js", "console.js", "code.js", "python.js"} <= names, "editor scripts"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: str(path.relative_to(JS_DIR)))
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
def test_script_avoids(script, label):
    found = re.findall(FORBIDDEN[label], code_only(script.read_text(encoding="utf-8")))
    assert not found, f"{script.name} uses {label}; build the nodes with createElement and textContent instead"


def test_the_comment_stripper_keeps_urls_and_drops_comments():
    sample = 'const a = "https://example.com"; // innerHTML here\n/* eval( */ const b = 1;'
    stripped = code_only(sample)
    assert "https://example.com" in stripped
    assert "innerHTML" not in stripped
    assert "eval" not in stripped


@pytest.mark.parametrize("label", sorted(FORBIDDEN))
def test_each_rule_catches_what_it_is_meant_to(label):
    samples = {
        "innerHTML": "el.innerHTML = x;",
        "outerHTML": "el.outerHTML = x;",
        "insertAdjacentHTML": "el.insertAdjacentHTML('beforeend', x);",
        "document.write": "document.write(x);",
        "eval": "eval(x);",
        "new Function": "new Function('return 1');",
        "a string handed to setTimeout/setInterval": "setTimeout('go()', 10);",
        "a javascript: URL": "a.href = 'javascript:alert(1)';",
        "srcdoc": "frame.srcdoc = x;",
    }
    assert re.search(FORBIDDEN[label], samples[label])


def test_the_run_buttons_say_the_same_as_the_server_when_running_is_switched_off():
    source = (JS_DIR / "editor" / "runner.js").read_text(encoding="utf-8")
    match = re.search(r'var OFF_NOTE = "([^"]+)";', source)
    assert match, "runner.js no longer has its OFF_NOTE line"
    assert match.group(1) == OFF_MESSAGE


def test_the_editor_page_loads_every_editor_script_and_each_after_the_ones_it_uses():
    page = (ROOT / "scratch_fluxer" / "templates" / "editor.html").read_text(encoding="utf-8")
    loaded = re.findall(r"js/editor/([a-z]+)\.js", page)
    assert len(loaded) == len(set(loaded)), "a script is loaded twice"
    assert set(loaded) == {path.stem for path in (JS_DIR / "editor").glob("*.js")}, "a script is missing from the page"
    assert loaded[0] == "util" and loaded[-1] == "main"
    assert loaded.index("console") < loaded.index("runner") < loaded.index("main")  # the runner writes to the console
