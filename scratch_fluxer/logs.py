"""Make the site's own log messages visible.

Python shows nothing below WARNING until logging is set up, and neither uvicorn nor Vercel sets it up for the loggers of
a site. So the notes the site writes for whoever runs it (which redirect address to give Fluxer, why a bot was stopped)
would never be seen. This gives them a plain handler, unless the person running the site has set logging up already.
None of the messages contain a secret.
"""

from __future__ import annotations

import logging

NAME = "scratch_fluxer"


def show_our_log() -> None:
    """Print ``scratch_fluxer`` messages from INFO up to the error output (where Docker and Vercel collect logs)."""
    ours = logging.getLogger(NAME)
    if ours.handlers or logging.getLogger().handlers:
        return  # already set up, by this function or by whoever runs the site
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    ours.addHandler(handler)
    ours.setLevel(logging.INFO)
