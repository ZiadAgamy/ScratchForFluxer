"""The landing page and the privacy page: structure, copy that must stay true, and the demo's server-side markup."""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from demo_python import DEMO_PYTHON  # shared with the compiler tests

from scratch_fluxer import catalogue, content, deps
from scratch_fluxer.compiler import RESERVED_NAMES

ROOT = Path(__file__).resolve().parent.parent


def text_of(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def demo_lines(page: str) -> list[str]:
    return [text_of(m) for m in re.findall(r'<code class="ln"[^>]*>(.*?)</code>', page, flags=re.S)]


class ExampleBlocks(HTMLParser):
    """The example blocks the block explorer shows, per family: each block's words, with ``_`` for every socket."""

    def __init__(self) -> None:
        super().__init__()
        self.families: dict[str, list[list[str]]] = {}
        self._family: str | None = None
        self._depth = 0
        self._block: list[str] | None = None
        self._word: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "div" and "cat-panel" in classes:
            self._family = (attributes.get("id") or "").removeprefix("cat-")
            self.families[self._family] = []
            self._depth = 0
        if self._family is None:
            return
        if tag == "div":
            self._depth += 1
            if "blk" in classes:
                self._block = []
                self.families[self._family].append(self._block)
        elif tag == "span" and self._block is not None:
            if "blk-field" in classes or "blk-menu" in classes:
                self._block.append("_")
            else:
                self._word = []

    def handle_data(self, data):
        if self._word is not None:
            self._word.append(data)

    def handle_endtag(self, tag):
        if self._family is None:
            return
        if tag == "span" and self._word is not None and self._block is not None:
            self._block.append("".join(self._word))
            self._word = None
        elif tag == "div":
            if self._block is not None:
                self._block = None
            self._depth -= 1
            if self._depth <= 0:
                self._family = None


def explorer_blocks(page: str) -> dict[str, list[list[str]]]:
    parser = ExampleBlocks()
    parser.feed(page)
    return parser.families


class TestStructure:
    def test_renders_with_one_h1_and_the_main_sections(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert len(re.findall(r"<h1[ >]", r.text)) == 1
        for anchor in ("how", "blocks", "run", "safety"):
            assert f'id="{anchor}"' in r.text

    def test_nav_links_point_at_real_sections(self, client):
        page = client.get("/").text
        nav = re.search(r'<nav class="primary-nav".*?</nav>', page, flags=re.S)
        assert nav, "the landing page shows its section links in the header"
        targets = re.findall(r'href="#([a-z-]+)"', nav.group(0))
        assert targets == ["how", "blocks", "safety"]
        for target in targets:
            assert f'id="{target}"' in page

    def test_other_pages_do_not_get_the_section_links(self, client):
        assert 'class="primary-nav"' not in client.get("/login").text
        assert 'class="primary-nav"' not in client.get("/privacy").text

    def test_every_in_page_link_has_a_target(self, client):
        page = client.get("/").text
        ids = set(re.findall(r'\sid="([^"]+)"', page))
        for target in re.findall(r'href="#([^"]+)"', page):
            assert target in ids, f"#{target} has nothing to scroll to"

    def test_ids_are_unique(self, client):
        ids = re.findall(r'\sid="([^"]+)"', client.get("/").text)
        assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})

    def test_no_inline_script_and_no_inline_event_handlers(self, client):
        """The CSP forbids both; catching it here is quicker than finding out in a browser."""
        page = client.get("/").text
        assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>", page)
        assert not re.search(r"\son[a-z]+\s*=", page)
        assert "javascript:" not in page.lower()

    def test_loads_its_own_assets_only(self, client):
        page = client.get("/").text
        assert "/static/css/landing.css" in page and "/static/js/landing.js" in page
        for url in re.findall(r'(?:src|href)="(https?://[^"]+)"', page):
            assert not url.endswith((".js", ".css", ".woff2")), f"third-party asset: {url}"

    def test_every_static_file_it_points_at_exists(self, client):
        page = client.get("/").text
        for path in re.findall(r'(?:src|href)="(/static/[^"?]+)', page):
            assert (ROOT / "public" / path.lstrip("/")).is_file(), path


class TestCopy:
    def test_all_eleven_block_families_are_listed(self, client):
        page = client.get("/").text
        for category in content.CATEGORIES:
            assert f'id="cat-{category["id"]}"' in page
            assert text_of(category["name"]) in text_of(page)
        assert len(content.CATEGORIES) == 11

    def test_every_example_block_is_rendered_with_its_words(self, client):
        page = client.get("/").text
        for category in content.CATEGORIES:
            for spec in category["blocks"]:
                for part in spec["parts"]:
                    label = part if isinstance(part, str) else part[1]
                    assert html.escape(label, quote=False) in page or html.escape(label) in page, label

    def test_the_example_blocks_are_the_real_blocks(self, client):
        """What a visitor sees in the explorer is the wording of the block they will get in the editor."""
        shown = explorer_blocks(client.get("/").text)
        assert list(shown) == catalogue.CATEGORY_ORDER
        for key, drawn in shown.items():
            types = [type_ for type_, _ in content.EXAMPLES[key]]
            assert len(drawn) == len(types) == 3, key
            for words, type_ in zip(drawn, types, strict=True):
                expected = []
                for line in catalogue.BLOCKS[type_]["lines"]:
                    for part in line:
                        if isinstance(part, str):
                            expected.append(part)
                        elif "statements" not in part:
                            # The room inside "if" and "repeat" is not drawn on the landing page.
                            expected.append("_")
                assert words == expected, type_

    def test_the_families_are_the_editors_families(self):
        assert [c["id"] for c in content.CATEGORIES] == catalogue.CATEGORY_ORDER
        for category in content.CATEGORIES:
            real = catalogue.CATEGORIES[category["id"]]
            assert (category["name"], category["icon"], category["blurb"]) == (
                real["name"],
                real["icon"],
                real["blurb"],
            )

    def test_every_example_is_a_block_of_the_family_it_is_shown_in(self):
        for key, examples in content.EXAMPLES.items():
            for type_, _ in examples:
                assert catalogue.BLOCKS[type_]["category"] == key, type_

    def test_block_shapes_follow_the_spec(self, client):
        page = client.get("/").text
        hats = sum(1 for c in content.CATEGORIES for b in c["blocks"] if b.get("hat"))
        rounds = sum(1 for c in content.CATEGORIES for b in c["blocks"] if b.get("round"))
        # the explorer renders every example once; the "files" mock reuses the non-round data blocks
        assert page.count("blk--hat") >= hats
        assert page.count("blk--round") == rounds

    def test_safety_promises_are_all_shown(self, client):
        page = text_of(client.get("/").text)
        for _icon, title, body in content.SAFETY:
            assert title in page
            assert body in page

    def test_how_it_works_has_four_numbered_steps(self, client):
        page = client.get("/").text
        assert len(content.HOW_IT_WORKS) == 4
        for number in range(1, 5):
            assert f"Step {number}" in page

    def test_links_to_the_privacy_page(self, client):
        page = client.get("/").text
        assert page.count('href="/privacy"') >= 2  # the safety section and the footer

    def test_mentions_the_things_the_brief_promised(self, client):
        page = text_of(client.get("/").text).lower()
        for phrase in ("fluxer.py", "python", "console", "json", "images", "restart", "token"):
            assert phrase in page, phrase


class TestSignedInOrOut:
    def test_visitors_are_asked_to_log_in(self, client):
        page = client.get("/").text
        assert page.count('href="/auth/login"') >= 3  # header, hero, closing call to action
        assert "Open my bots" not in page

    def test_members_are_sent_to_their_bots(self, client, make_user):
        _, _, cookies = make_user()
        client.cookies.update(cookies)
        page = client.get("/").text
        assert page.count("Open my bots") == 2  # hero + closing call to action (the header says "My bots")
        assert 'class="btn btn-primary btn-lg" href="/auth/login"' not in page


class TestDemoMarkup:
    def test_python_pane_is_the_real_file(self, client):
        assert "\n".join(demo_lines(client.get("/").text)) + "\n" == DEMO_PYTHON

    def test_the_demo_is_told_the_names_the_compiler_will_not_let_a_command_use(self, client):
        page = client.get("/").text
        raw = re.search(r"data-reserved='([^']*)'", page)
        assert raw, "the demo needs the compiler's list of reserved names"
        assert json.loads(html.unescape(raw.group(1))) == sorted(RESERVED_NAMES)

    def test_it_matches_the_shared_demo_values(self, client):
        d = content.DEMO
        text = "\n".join(demo_lines(client.get("/").text))
        assert f'command_prefix="{d["prefix"]}"' in text
        assert f'print("{d["ready"]}")' in text
        assert f"async def {d['command']}(ctx):" in text
        assert f'ctx.reply("{d["reply"]}")' in text

    def test_the_token_is_never_written_into_the_code(self, client):
        page = client.get("/").text
        assert 'os.environ["FLUXER_TOKEN"]' in text_of(page)
        assert not re.search(r"\d{10,}\.[A-Za-z0-9_\-]{20,}", page)  # nothing token-shaped

    def test_every_block_has_code_to_link_to(self, client):
        page = client.get("/").text
        blocks = set(re.findall(r'class="blk[^"]*" data-block="([a-z]+)"', page))
        lines = set(re.findall(r'<code class="ln" data-block="([a-z]+)"', page))
        assert blocks == {"ready", "print", "cmd", "reply", "run"}
        assert blocks <= lines

    def test_controls_start_disabled_until_the_script_runs(self, client):
        page = client.get("/").text
        for action in ("run", "restart", "stop"):
            assert re.search(rf'data-act="{action}" disabled', page)

    def test_every_field_has_an_accessible_name(self, client):
        page = client.get("/").text
        inputs = re.findall(r"<input [^>]*>", page.split('id="demo"', 1)[1].split('id="how"', 1)[0])
        assert len(inputs) == 4  # three block fields + the chat box
        for tag in inputs:
            assert "aria-label=" in tag, tag


class TestPrivacyPage:
    def test_renders(self, client):
        r = client.get("/privacy")
        assert r.status_code == 200
        assert "<h1>Privacy</h1>" in r.text

    def test_names_the_cookies_the_site_really_sets(self, client):
        page = client.get("/privacy").text
        assert deps.SESSION_COOKIE in page
        assert deps.OAUTH_COOKIE in page

    def test_says_what_is_stored_and_how_to_delete_it(self, client):
        page = text_of(client.get("/privacy").text)
        for phrase in ("encrypted", "last four", "revoked", "Delete my data", "No analytics"):
            assert phrase in page, phrase

    def test_questions_section_only_when_there_is_somewhere_to_ask(self, app, monkeypatch):
        from fastapi.testclient import TestClient

        from scratch_fluxer import config

        monkeypatch.setenv("COMMUNITY_URL", "")  # no community link, no contact link: nothing to say
        config.reset_settings_cache()
        with TestClient(app) as c:
            assert "Questions" not in c.get("/privacy").text

    @pytest.mark.parametrize("value", ["mailto:hello@example.com", "https://example.com/contact"])
    def test_shows_a_safe_contact_link(self, app, monkeypatch, value):
        from fastapi.testclient import TestClient

        from scratch_fluxer import config

        monkeypatch.setenv("CONTACT_URL", value)
        config.reset_settings_cache()
        with TestClient(app) as c:
            page = c.get("/privacy").text
        assert f'href="{value}"' in page

    @pytest.mark.parametrize("value", ["javascript:alert(1)", "data:text/html,hi", "//evil.example", "ftp://x"])
    def test_never_links_an_unsafe_contact_url(self, app, monkeypatch, value):
        from fastapi.testclient import TestClient

        from scratch_fluxer import config

        monkeypatch.setenv("CONTACT_URL", value)
        monkeypatch.setenv(
            "COMMUNITY_URL", ""
        )  # so that the only possible reason for a "Questions" section is the contact
        config.reset_settings_cache()
        with TestClient(app) as c:
            page = c.get("/privacy").text
        assert "Questions" not in page
        assert value not in page


COMMUNITY = "https://fluxer.gg/oHy1OzcO"
COMMUNITY_LINK = re.compile(r'<a [^>]*href="([^"]*)"[^>]*>')


def links_to(page: str, target: str) -> list[str]:
    """The opening tags of every link on the page that goes to ``target``."""
    return [tag.group(0) for tag in COMMUNITY_LINK.finditer(page) if tag.group(1) == target]


def with_community(app, monkeypatch, value: str | None):
    """A client for an app whose COMMUNITY_URL is ``value`` (``None`` leaves the default alone)."""
    from fastapi.testclient import TestClient

    from scratch_fluxer import config

    if value is not None:
        monkeypatch.setenv("COMMUNITY_URL", value)
    config.reset_settings_cache()
    return TestClient(app)


class TestCommunityLink:
    """The community server is linked from the footer, the end of the home page and the privacy page."""

    @pytest.mark.parametrize("path", ["/", "/privacy", "/login"])
    def test_the_footer_links_to_the_community_server_safely(self, client, path):
        (tag,) = [t for t in links_to(client.get(path).text, COMMUNITY) if "footer-community" in t]
        assert 'target="_blank"' in tag and 'rel="noopener noreferrer"' in tag

    def test_the_home_page_asks_for_questions_there(self, client):
        page = client.get("/").text
        assert len(links_to(page, COMMUNITY)) == 2  # footer + closing call to action
        assert "community server" in text_of(page)

    def test_the_privacy_page_says_nothing_is_sent_there(self, client):
        page = client.get("/privacy").text
        assert len(links_to(page, COMMUNITY)) == 2  # footer + questions section
        assert "Nothing from this site is sent there" in text_of(page)

    def test_every_link_that_leaves_the_site_is_safe(self, client):
        for path in ("/", "/privacy", "/login"):
            for tag in re.findall(r"<a [^>]*>", client.get(path).text):
                if 'target="_blank"' in tag:
                    assert 'rel="noopener noreferrer"' in tag, tag

    def test_the_community_link_is_the_only_new_outside_address(self, client):
        page = client.get("/").text
        outside = set(re.findall(r'href="(https?://[^"]+)"', page))
        assert COMMUNITY in outside
        assert all(address.startswith(("https://", "http://testserver")) for address in outside)

    def test_an_empty_setting_hides_it_everywhere(self, app, monkeypatch):
        with with_community(app, monkeypatch, "") as c:
            for path in ("/", "/privacy", "/login"):
                page = c.get(path).text
                assert "fluxer.gg" not in page and "community server" not in page.lower()
                assert "footer-community" not in page

    def test_another_https_link_replaces_it(self, app, monkeypatch):
        with with_community(app, monkeypatch, "https://fluxer.gg/another") as c:
            page = c.get("/").text
        assert len(links_to(page, "https://fluxer.gg/another")) == 2 and COMMUNITY not in page

    @pytest.mark.parametrize(
        "value",
        [
            "javascript:alert(1)",
            "data:text/html,hi",
            "http://fluxer.gg/oHy1OzcO",  # not https
            "//evil.example",
            "fluxer.gg/oHy1OzcO",
            "https:///no-host-name",
            "https://fluxer.gg/x y",
            'https://fluxer.gg/"onmouseover="alert(1)',
            "https://fluxer.gg/<script>",
            "https://fluxer.gg/\\evil.example",
            "https://fluxer.gg/\nx",
            "mailto:me@example.com",
        ],
    )
    def test_nothing_unsafe_is_ever_linked(self, app, monkeypatch, value):
        with with_community(app, monkeypatch, value) as c:
            for path in ("/", "/privacy"):
                page = c.get(path).text
                assert value not in page and "alert(1)" not in page
                assert "footer-community" not in page

    def test_the_privacy_questions_section_shows_both_ways_to_reach_someone(self, app, monkeypatch):
        monkeypatch.setenv("CONTACT_URL", "mailto:hello@example.com")
        with with_community(app, monkeypatch, None) as c:
            page = c.get("/privacy").text
        assert 'href="mailto:hello@example.com"' in page and len(links_to(page, COMMUNITY)) == 2
        assert page.count("Questions</h2>") == 1


class TestLinkPreviews:
    def test_landing_has_absolute_preview_tags(self, client, settings):
        page = client.get("/").text
        base = settings.public_base_url
        assert f'<meta property="og:image" content="{base}/static/img/og.png">' in page
        assert f'<link rel="canonical" href="{base}/">' in page
        assert '<meta name="twitter:card" content="summary_large_image">' in page

    def test_privacy_has_its_own_canonical_address(self, client, settings):
        assert f'<link rel="canonical" href="{settings.public_base_url}/privacy">' in client.get("/privacy").text

    def test_the_preview_image_is_a_1200_by_630_png(self):
        data = (ROOT / "public/static/img/og.png").read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        assert (width, height) == (1200, 630)
        assert len(data) < 400_000  # link previews are fetched often; keep it light

    def test_login_page_is_not_advertised(self, client):
        assert 'content="noindex' in client.get("/login").text
