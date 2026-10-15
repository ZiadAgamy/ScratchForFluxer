"""Takes the screenshots on the /docs page, from the real site.

It starts the site on a local port with a pretend Fluxer (the same one the tests use), logs a made-up person in, adds a
bot, and photographs the pages the guide talks about. Run it again whenever the design changes:

    python scripts/make_docs_images.py

Needs Playwright with Chromium and Pillow (``pip install pillow``). The pictures land in public/static/img/docs/.
Nothing here touches a real Fluxer account: every name, avatar and token is made up.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "public" / "static" / "img" / "docs"
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="sff-docs-"))

from fluxer_mock import OWNER_FLUXER_ID, TOKEN  # noqa: E402
from mock_fluxer_server import MockFluxer  # noqa: E402

VIEW = {"width": 1360, "height": 800}

#: Numbered badges drawn over a screenshot: ``(css selector, number, where on that element)``.
BADGE_JS = """
(items) => {
  for (const [selector, number, where, dx, dy] of items) {
    const el = document.querySelector(selector);
    if (!el) { console.warn('no ' + selector); continue; }
    const r = el.getBoundingClientRect();
    const x = where === 'right' ? r.right : where === 'center' ? r.left + r.width / 2 : r.left;
    const y = where === 'bottom' ? r.bottom : r.top + r.height / 2;
    const badge = document.createElement('div');
    badge.textContent = number;
    badge.className = 'docs-shot-badge';
    badge.style.cssText = 'position:absolute;z-index:99999;width:30px;height:30px;border-radius:50%;background:#176dff;'
      + 'color:#fff;font:700 16px/30px IBM Plex Sans,sans-serif;text-align:center;box-shadow:0 0 0 3px #0f0e11,'
      + '0 6px 16px rgba(0,0,0,.5);pointer-events:none;left:' + (x + dx - 15) + 'px;top:' + (y + dy - 15) + 'px';
    document.body.appendChild(badge);
  }
}
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mock = MockFluxer()
    mock.start()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    os.environ.update(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "docs-screenshots-secret-key-0123456789-abcdefghijklmnop",
            "APP_BASE_URL": base,
            "FLUXER_CLIENT_ID": "1111222233334444",
            "FLUXER_CLIENT_SECRET": "docs-screenshots-client-secret",
            "DATABASE_URL": f"sqlite:///{TMP / 'docs.db'}",
            "RUNNER_MODE": "local",
            "RUNNER_DATA_DIR": str(TMP / "runs"),
            "FLUXER_API_URL": mock.api_url,
        }
    )
    for name in ("VERCEL", "VERCEL_PROJECT_PRODUCTION_URL", "POSTGRES_URL", "DATABASE_URL_UNPOOLED"):
        os.environ.pop(name, None)

    import uvicorn
    from blocks import command, join, reply, workspace
    from blocks import block as block_of
    from playwright.sync_api import sync_playwright

    from scratch_fluxer import deps
    from scratch_fluxer.db import _call_with_session
    from scratch_fluxer.main import create_app
    from scratch_fluxer.models import Bot, User

    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)

    def insert_user(session):
        user = User(fluxer_id=OWNER_FLUXER_ID, username="agamy", global_name="AGAMY")
        session.add(user)
        session.flush()
        return user.id, user.session_epoch

    user_id, epoch = _call_with_session(insert_user, (), {})
    csrf = "docs-csrf-token"
    cookie = deps.get_codec().dump_session({"u": user_id, "e": epoch, "c": csrf, "t": int(time.time())})

    options = {"args": ["--no-sandbox"]}
    for candidate in (os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"), "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"):
        if candidate and Path(candidate).exists():
            options["executable_path"] = candidate
            break

    def shot(page, name: str, clip=None, full: bool = False, only: str | None = None) -> None:
        raw = TMP / f"{name}.png"
        if only:  # just that part of the page, so the header and footer don't take up the picture
            page.locator(only).screenshot(path=str(raw))
        else:
            page.screenshot(path=str(raw), clip=clip, full_page=full)
        from PIL import Image

        image = Image.open(raw).convert("RGB")
        image = image.quantize(colors=192, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        image.save(OUT / f"{name}.png", optimize=True)
        print("wrote", name, image.size, (OUT / f"{name}.png").stat().st_size // 1024, "KB")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**options)

        # ---- logged out: the login page
        anonymous = browser.new_context(viewport=VIEW)
        page = anonymous.new_page()
        page.goto(f"{base}/login")
        page.wait_for_load_state("networkidle")
        shot(page, "login", only="main")
        anonymous.close()

        context = browser.new_context(viewport=VIEW)
        context.add_cookies([{"name": deps.SESSION_COOKIE, "value": cookie, "url": base}])
        page = context.new_page()

        # ---- My bots, still empty
        page.goto(f"{base}/bots")
        page.wait_for_load_state("networkidle")
        shot(page, "my-bots-empty", only=".page")

        # ---- Add a bot: paste the token, then confirm
        page.goto(f"{base}/bots/new")
        page.wait_for_load_state("networkidle")
        page.fill("#token-input", TOKEN)
        page.click("#token-toggle")
        page.evaluate(BADGE_JS, [["#token-input", 1, "left", -6, 0], ["#check-btn", 2, "left", -6, 0]])
        shot(page, "add-token", only=".page")
        page.reload()
        page.wait_for_load_state("networkidle")
        page.fill("#token-input", TOKEN)
        page.click("#check-btn")
        page.wait_for_selector("#state-confirm:not([hidden])")
        page.fill("#bot-name-input", "Ping Bot")
        page.evaluate(BADGE_JS, [["#bot-name-input", 1, "left", -6, 0], ["#save-btn", 2, "left", -6, 0]])
        shot(page, "add-confirm", only=".page")
        page.click("#save-btn")
        page.wait_for_url("**/edit")
        page.goto(f"{base}/bots")

        # ---- My bots with one bot, and its menu
        page.wait_for_load_state("networkidle")
        page.click(".bot-menu summary")
        page.evaluate(
            BADGE_JS,
            [[".bot-actions .btn-primary", 1, "left", -6, 0], [".bot-menu summary", 2, "right", 8, 0]],
        )
        shot(page, "my-bots", only=".page")

        bot_id = _call_with_session(lambda s: s.query(Bot).first().id, (), {})

        # a richer set of blocks than the starter: a hello command next to !ping
        from scratch_fluxer.compiler import default_workspace

        starter = default_workspace()
        extra = command(
            "hello",
            reply(join("Hello, ", block_of("sff_msg_author_name"))),
            x=40,
            y=470,
        )
        starter["blocks"]["blocks"].append(extra)
        from scratch_fluxer import workspace as workspace_lib

        cleaned = workspace_lib.clean_workspace(starter).workspace
        import json

        def store(session):
            row = session.query(Bot).filter(Bot.id == bot_id).one()
            row.workspace_json = json.dumps(cleaned)

        _call_with_session(store, (), {})

        # ---- the editor
        page.goto(f"{base}/bots/{bot_id}/edit")
        page.wait_for_selector("#ed-loading", state="hidden", timeout=20000)
        page.wait_for_function("() => window.sffEditor.blocks.workspace().getAllBlocks(false).length > 0")
        page.wait_for_timeout(900)
        page.evaluate("() => document.getElementById('ed-tip-close')?.click()")
        page.click("#tool-tidy")
        page.wait_for_timeout(600)

        # the editor with numbers on its parts
        page.evaluate(
            BADGE_JS,
            [
                [".blocklyToolboxCategory", 1, "right", 10, 0],
                ["#blockly", 2, "center", 150, -150],
                ["#run-btn", 3, "center", 0, 34],
                ["#ed-get summary", 4, "center", 0, 34],
                ["#code-view", 5, "right", -60, 40],
                ["#console-log", 6, "right", -40, 40],
            ],
        )
        shot(page, "editor-tour")
        page.evaluate("() => document.querySelectorAll('.docs-shot-badge').forEach((b) => b.remove())")

        # ---- a family's blocks flying out of the toolbox
        rows = page.locator(".blocklyToolboxCategory")
        for index in range(rows.count()):
            if "Messages" in rows.nth(index).inner_text():
                rows.nth(index).click()
                break
        page.wait_for_timeout(700)
        shot(page, "editor-palette", clip={"x": 0, "y": 0, "width": 930, "height": 800})
        page.keyboard.press("Escape")
        page.mouse.click(900, 600)

        # ---- the Python tab is the default; show it on its own
        page.wait_for_timeout(300)
        shot(page, "editor-python")

        # ---- the Get code menu
        page.click("#ed-get summary")
        page.wait_for_timeout(300)
        page.evaluate(BADGE_JS, [["#get-py", 1, "left", -8, 0], ["#get-zip", 2, "left", -8, 0]])
        shot(page, "get-code")
        page.evaluate("() => document.querySelectorAll('.docs-shot-badge').forEach((b) => b.remove())")
        page.click("#ed-get summary")

        # ---- running: press Run, wait for it, let someone say !ping
        page.click("#run-btn")
        page.wait_for_function("() => document.querySelector('#run-state')?.textContent.includes('Running')", timeout=30000)
        mock.say("!ping")
        mock.wait_for(lambda: mock.replies() == ["Pong!"])
        page.wait_for_timeout(1200)
        page.evaluate(
            BADGE_JS,
            [["#run-state", 1, "left", -4, -26], ["#stop-btn", 2, "center", 0, 34], ["#console-log", 3, "right", -26, 60]],
        )
        shot(page, "running")
        page.evaluate("() => document.querySelectorAll('.docs-shot-badge').forEach((b) => b.remove())")
        page.click("#stop-btn")
        page.wait_for_timeout(800)

        # ---- phone width
        phone = browser.new_context(viewport={"width": 390, "height": 800}, device_scale_factor=2)
        phone.add_cookies([{"name": deps.SESSION_COOKIE, "value": cookie, "url": base}])
        small = phone.new_page()
        small.goto(f"{base}/bots/{bot_id}/edit")
        small.wait_for_selector("#ed-loading", state="hidden", timeout=20000)
        small.wait_for_timeout(900)
        shot(small, "editor-phone")
        phone.close()

        browser.close()
    server.should_exit = True
    mock.stop()


if __name__ == "__main__":
    main()
