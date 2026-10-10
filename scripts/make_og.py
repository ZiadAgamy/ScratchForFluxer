"""Render the link-preview image (public/static/img/og.png, 1200x630) from scripts/og.html.

    python scripts/make_og.py

Needs Playwright with Chromium (`pip install playwright && playwright install chromium`) and Pillow. The card is
plain HTML that reuses the site's CSS, so edit scripts/og.html and run this again to change the preview.
"""

from __future__ import annotations

import functools
import http.server
import os
import threading
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
CARD = ROOT / "scripts" / "og.html"
OUT = PUBLIC / "static" / "img" / "og.png"


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: D401 - silence the access log
        pass


def chromium_path() -> str | None:
    explicit = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if explicit:
        return explicit
    sandbox_build = Path("/opt/pw-browsers/chromium")
    return str(sandbox_build) if sandbox_build.exists() else None


def main() -> None:
    handler = functools.partial(Quiet, directory=str(PUBLIC))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    card = CARD.read_text(encoding="utf-8")

    try:
        with sync_playwright() as p:
            options: dict = {"args": ["--no-sandbox"]}
            if chromium_path():
                options["executable_path"] = chromium_path()
            browser = p.chromium.launch(**options)
            page = browser.new_page(viewport={"width": 1200, "height": 630}, device_scale_factor=1)
            # The card is served from memory but lives on the same origin as the fonts it uses.
            page.route(
                f"{base}/og-card", lambda route: route.fulfill(body=card, content_type="text/html; charset=utf-8")
            )
            page.goto(f"{base}/og-card", wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            raw = OUT.with_suffix(".raw.png")
            page.screenshot(path=str(raw), clip={"x": 0, "y": 0, "width": 1200, "height": 630})
            browser.close()
    finally:
        server.shutdown()

    # Flat colours compress well as a palette image, which keeps the file small.
    image = Image.open(raw).convert("RGB")
    image.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(OUT, optimize=True)
    raw.unlink()
    print(f"Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
