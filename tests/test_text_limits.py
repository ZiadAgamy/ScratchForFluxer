"""The most a text block can hold is one number, and every place that checks it agrees."""

from __future__ import annotations

import re

from scratch_fluxer import catalogue, compiler
from scratch_fluxer.config import PUBLIC_DIR


def test_the_editor_dialog_limit_is_the_text_blocks_limit():
    script = (PUBLIC_DIR / "static" / "js" / "editor" / "blocks.js").read_text(encoding="utf-8")
    found = re.search(r"var TEXT_LIMIT = (\d+);", script)
    assert found, "blocks.js lost its TEXT_LIMIT"
    text_field = catalogue.BLOCKS["sff_text"]["lines"][0][0]
    assert int(found.group(1)) == text_field["max"] == compiler.MAX_TEXT
