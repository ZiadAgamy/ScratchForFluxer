"""Server-rendered HTML pages."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .. import __version__, changelog, guide, store
from ..compiler import RESERVED_NAMES, compile_workspace, default_workspace
from ..config import get_settings
from ..content import CATEGORIES, DEMO, HOW_IT_WORKS, SAFETY
from ..deps import current_user, render
from ..security import safe_next

router = APIRouter()


def _public(request: Request, path: str) -> dict[str, Any]:
    """Absolute address + preview image for link previews (they must be absolute URLs)."""
    base = get_settings().public_base_url or str(request.base_url).rstrip("/")
    return {"canonical": f"{base}{path}", "og_image": f"{base}/static/img/og.png"}


@router.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "version": __version__}, headers={"Cache-Control": "no-store"})


@router.get("/", include_in_schema=False)
async def landing(request: Request) -> Response:
    await current_user(request)
    return render(
        request,
        "landing.html",
        {
            "page": "landing",
            "landing_nav": True,
            "categories": CATEGORIES,
            "how_it_works": HOW_IT_WORKS,
            "safety": SAFETY,
            "demo": DEMO,
            "reserved": sorted(RESERVED_NAMES),
            **_public(request, "/"),
        },
    )


@router.get("/docs", include_in_schema=False)
async def docs(request: Request) -> Response:
    """The guide: from creating a bot on Fluxer to running main.py on your own machine."""
    await current_user(request)
    settings = get_settings()
    families = guide.families()
    return render(
        request,
        "docs.html",
        {
            "page": "docs",
            "families": families,
            "codename_groups": guide.codename_groups(),
            "block_total": sum(len(family["blocks"]) for family in families),
            "limits": store.limits(settings),
            "sample_code": compile_workspace(default_workspace()).code.rstrip(),
            "max_bots": settings.max_bots_per_user,
            "site": get_settings().public_base_url or str(request.base_url).rstrip("/"),
            **_public(request, "/docs"),
        },
    )


@router.get("/changelog", include_in_schema=False)
async def changelog_page(request: Request) -> Response:
    """Everything new on the site, by date."""
    await current_user(request)
    return render(
        request,
        "changelog.html",
        {"page": "changelog", "groups": changelog.by_date(), **_public(request, "/changelog")},
    )


@router.get("/privacy", include_in_schema=False)
async def privacy(request: Request) -> Response:
    await current_user(request)
    settings = get_settings()
    return render(
        request,
        "privacy.html",
        {"page": "privacy", "contact_url": settings.contact_url, **_public(request, "/privacy")},
    )


@router.get("/login", include_in_schema=False)
async def login_page(request: Request, next: str | None = None) -> Response:
    """The page never shows text that came from its address (a made-up link could otherwise put its own words on it)."""
    user = await current_user(request)
    destination = safe_next(next)
    if user is not None:
        return RedirectResponse(destination, status_code=303)
    settings = get_settings()
    return render(
        request,
        "login.html",
        {
            "page": "login",
            "next": destination,
            "login_ready": bool(settings.fluxer_client_id and settings.fluxer_client_secret),
        },
    )
