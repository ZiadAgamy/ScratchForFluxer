"""Every Phosphor icon the project uses must exist in the vendored *Fill* font (the brief says Fill, not Regular)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PHOSPHOR_CSS = (ROOT / "public/static/vendor/phosphor/fill/style.css").read_text()
KNOWN = set(re.findall(r"\.ph-fill\.ph-([a-z0-9-]+):before", PHOSPHOR_CSS))

# Class-name tokens that are part of the icon-font machinery, not icon names.
NOT_ICONS = {"fill"}


def source_files():
    for pattern in (
        "scratch_fluxer/**/*.html",
        "scratch_fluxer/**/*.py",
        "public/static/js/**/*.js",
        "public/static/css/*.css",
    ):
        yield from (p for p in ROOT.glob(pattern) if "vendor" not in p.parts)


def used_icons():
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        for name in re.findall(r"""icon\(\s*["']([a-z0-9-]+)["']""", text):  # {{ icon("robot") }}
            yield path, name
        for name in re.findall(r"""(?<![\w-])ph-([a-z0-9]+(?:-[a-z0-9]+)*)""", text):  # "ph-robot" in classes/JS/CSS
            if name not in NOT_ICONS:
                yield path, name


def test_the_vendored_icon_font_is_the_fill_weight():
    assert len(KNOWN) > 1000
    assert "robot" in KNOWN and "play" in KNOWN
    assert not re.search(
        r"(?m)^\.(?:ph|ph-bold|ph-light|ph-thin|ph-duotone)[ .{:]", PHOSPHOR_CSS
    )  # only Fill is vendored


def test_every_icon_used_exists():
    missing = sorted({f"{path.relative_to(ROOT)}: {name}" for path, name in used_icons() if name not in KNOWN})
    assert not missing, "Unknown Phosphor Fill icons:\n" + "\n".join(missing)


def test_icons_are_always_the_fill_style():
    """`ph-xyz` classes must come with `ph-fill` (a bare `ph ph-xyz` would silently render nothing)."""
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"""class=["'][^"']*\bph\b(?!-)[^"']*["']""", text):
            raise AssertionError(f"{path.relative_to(ROOT)} uses the Regular icon class: {match.group(0)}")
