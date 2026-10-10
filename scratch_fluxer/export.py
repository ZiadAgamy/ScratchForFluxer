"""A bot taken out of the editor: its Python file, or a folder that runs anywhere Python does.

The bot's token is never part of an export. The code reads it from ``FLUXER_TOKEN`` when it starts (the same way it
does here), and the README tells the person where to put it.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterable

REQUIREMENTS = "fluxer.py>=0.4.2,<0.5\n"

README = """\
Your Fluxer bot
===============

This folder was made by Scratch For Fluxer. main.py is the Python your blocks write, and the assets folder (if there is
one) holds the files your blocks use.

To run it on your own computer:

1. Install Python 3.10 or newer from https://www.python.org/downloads/
2. Open a terminal in this folder and install what the bot needs:

       pip install -r requirements.txt

3. Give the bot its token and start it. The token is only ever read from an environment variable called FLUXER_TOKEN.

   macOS or Linux:

       FLUXER_TOKEN="paste your token here" python main.py

   Windows (PowerShell):

       $env:FLUXER_TOKEN="paste your token here"; python main.py

Keep your token private. Anyone who has it can control your bot, so never put it in a file you share or upload.
"""

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def file_stem(name: str, fallback: str = "bot") -> str:
    """A name for the download that is safe in a header and on any file system."""
    return _UNSAFE.sub("-", name).strip("-.")[:40] or fallback


def build_zip(code: str, files: Iterable[tuple[str, bytes]]) -> bytes:
    """``main.py``, ``requirements.txt``, ``README.txt`` and ``assets/<name>`` for each file, as a zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("main.py", code)
        archive.writestr("requirements.txt", REQUIREMENTS)
        archive.writestr("README.txt", README)
        for name, data in files:
            archive.writestr(f"assets/{name}", data)
    return buffer.getvalue()
