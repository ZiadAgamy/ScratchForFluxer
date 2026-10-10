"""Vercel entrypoint.

Vercel looks for a FastAPI instance named ``app`` in ``index.py`` (see also
``[tool.vercel] entrypoint`` in pyproject.toml). Locally you can run either
``python -m scratch_fluxer`` or ``uvicorn index:app --reload``.
"""

from scratch_fluxer.main import app  # noqa: F401
