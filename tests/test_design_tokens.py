"""The palette is part of the product: every text/background pair we use must stay readable (WCAG AA)."""

from __future__ import annotations

import colorsys
import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parent.parent / "public" / "static" / "css" / "app.css").read_text()


def _root_vars() -> dict[str, str]:
    root = CSS[CSS.index(":root") : CSS.index("}", CSS.index(":root"))]
    return dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", root))


VARS = _root_vars()


def colour(name: str) -> tuple[int, int, int]:
    raw = VARS[name].strip()
    if raw.startswith("#"):
        raw = raw.lstrip("#")
        return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    match = re.fullmatch(r"hsl\(var\(--hue\)\s+(\d+)%\s+(\d+)%\)", raw)
    assert match, f"unsupported colour syntax for {name}: {raw}"
    hue = int(VARS["--hue"])
    r, g, b = colorsys.hls_to_rgb(hue / 360, int(match.group(2)) / 100, int(match.group(1)) / 100)
    return round(r * 255), round(g * 255), round(b * 255)


def luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


WHITE = (255, 255, 255)
SURFACES = ["--bg-page", "--bg-raised", "--bg-panel", "--bg-field", "--bg-hover"]
CATEGORIES = [name for name in VARS if name.startswith("--cat-")]


def test_the_accent_is_the_logo_blue():
    assert VARS["--accent"].lower() == "#176dff"
    # The button blue may differ from the logo by a hair, never visibly.
    solid, brand = colour("--accent-solid"), colour("--accent")
    assert all(abs(a - b) <= 2 for a, b in zip(solid, brand, strict=True))


def test_all_nine_block_categories_exist():
    assert sorted(CATEGORIES) == sorted(
        f"--cat-{n}"
        for n in ("events", "bot", "messages", "info", "control", "operators", "variables", "lists", "data")
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_body_text_is_readable_on_every_surface(surface):
    assert contrast(colour("--text"), colour(surface)) >= 7
    assert contrast(colour("--text-2"), colour(surface)) >= 4.5
    assert contrast(colour("--text-3"), colour(surface)) >= 4.5


@pytest.mark.parametrize("surface", ["--bg-page", "--bg-raised", "--bg-panel", "--bg-field"])
def test_links_and_accent_text_are_readable(surface):
    assert contrast(colour("--accent-hi"), colour(surface)) >= 4.5


@pytest.mark.parametrize("surface", ["--bg-page", "--bg-raised", "--bg-panel"])
def test_status_colours_are_readable_as_text(surface):
    for name in ("--ok", "--warn", "--danger"):
        assert contrast(colour(name), colour(surface)) >= 4.5, name


def test_primary_button_text():
    assert contrast(WHITE, colour("--accent-solid")) >= 4.5
    assert contrast(WHITE, colour("--accent-lo")) >= 4.5


def test_danger_button_text():
    assert contrast(WHITE, colour("--danger-solid")) >= 4.5


@pytest.mark.parametrize("category", CATEGORIES)
def test_block_labels_are_readable(category):
    assert contrast(colour("--ink-on-block"), colour(category)) >= 4.5


@pytest.mark.parametrize("category", CATEGORIES)
def test_blocks_stand_out_from_the_workspace(category):
    # Non-text contrast: a block has to be clearly visible against the page it sits on.
    assert contrast(colour(category), colour("--bg-page")) >= 3
