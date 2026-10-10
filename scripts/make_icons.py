"""Rebuild every logo-derived image from public/static/img/logo.png.

Replace logo.png with your own artwork (a transparent PNG, any size), then run:

    python scripts/make_icons.py

It writes logo-192.png, logo-512.png, apple-touch-icon.png and public/favicon.ico. Needs Pillow
(`pip install pillow`, it is listed in requirements-dev.txt). Run scripts/make_og.py afterwards to refresh the
link-preview image, which also contains the logo.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "public" / "static" / "img"
THEME_BG = (15, 14, 17, 255)  # the page background (#0f0e11): iOS paints transparent icons black


def trimmed(path: Path) -> Image.Image:
    logo = Image.open(path).convert("RGBA")
    box = logo.getbbox()
    if box is None:
        raise SystemExit(f"{path} is empty")
    return logo.crop(box)


def on_square(logo: Image.Image, size: int, fill: float, background: tuple[int, int, int, int]) -> Image.Image:
    """The logo scaled to ``fill`` of a square canvas and centred on it."""
    scale = size * fill / max(logo.size)
    resized = logo.resize((max(1, round(logo.width * scale)), max(1, round(logo.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), background)
    canvas.alpha_composite(resized, ((size - resized.width) // 2, (size - resized.height) // 2))
    return canvas


def main() -> None:
    source = IMG / "logo.png"
    if not source.is_file():
        raise SystemExit(f"Put your logo at {source.relative_to(ROOT)} first.")
    logo = trimmed(source)

    transparent = (0, 0, 0, 0)
    on_square(logo, 192, 0.84, transparent).save(IMG / "logo-192.png", optimize=True)
    on_square(logo, 512, 0.84, transparent).save(IMG / "logo-512.png", optimize=True)
    on_square(logo, 180, 0.66, THEME_BG).convert("RGB").save(IMG / "apple-touch-icon.png", optimize=True)

    # A favicon is tiny, so let the logo fill almost all of it.
    on_square(logo, 256, 0.94, transparent).save(ROOT / "public" / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("Wrote logo-192.png, logo-512.png, apple-touch-icon.png and favicon.ico from", source.relative_to(ROOT))


if __name__ == "__main__":
    main()
