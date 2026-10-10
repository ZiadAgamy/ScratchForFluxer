"""``python -m scratch_fluxer`` - run the site locally, or one of the other commands (see ``cli.py``)."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
