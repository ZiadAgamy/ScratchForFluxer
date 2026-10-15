"""The /docs guide: it is public, complete, and every link and picture on it leads somewhere."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scratch_fluxer import catalogue, guide
from scratch_fluxer.compiler import compile_workspace, default_workspace
from scratch_fluxer.export import README

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "public" / "static" / "img" / "docs"


@pytest.fixture
def page(client) -> str:
    r = client.get("/docs")
    assert r.status_code == 200
    return r.text


class TestThePage:
    def test_it_is_public(self, client):
        r = client.get("/docs")
        assert r.status_code == 200 and "Build a Fluxer bot with blocks" in r.text
        assert "Log in with Fluxer" in r.text  # an anonymous visitor can read it and is invited to log in

    def test_a_logged_in_person_can_read_it_too(self, client, make_user):
        _, csrf, cookies = make_user(username="agamy", global_name="AGAMY")
        client.cookies.update(cookies)
        r = client.get("/docs")
        assert r.status_code == 200 and "AGAMY" in r.text and 'name="csrf-token"' in r.text

    def test_it_is_not_the_api_explorer(self, page):
        assert "swagger" not in page.lower()

    def test_every_menu_link_points_at_something_on_the_page(self, page):
        ids = set(re.findall(r'\sid="([^"]+)"', page))
        links = re.findall(r'href="#([^"]+)"', page)
        assert links
        assert [link for link in links if link not in ids] == []

    def test_every_picture_exists(self, page):
        names = set(re.findall(r'/static/img/docs/([\w-]+\.png)', page))
        assert names, "the guide shows no pictures"
        assert [name for name in names if not (IMAGES / name).is_file()] == []

    def test_pictures_have_a_description_and_a_size(self, page):
        for tag in re.findall(r"<img[^>]+/static/img/docs/[^>]+>", page):
            assert re.search(r'alt="[^"]{12,}"', tag), tag
            assert 'width="' in tag and 'height="' in tag, tag

    def test_it_loads_only_its_own_scripts_and_styles(self, page):
        assert 'src="/static/js/docs.js' in page and "/static/css/docs.css" in page
        assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>", page)  # no inline script (the CSP refuses it)
        assert not re.search(r'(?:src|href)="https?://[^"]*\.(?:js|css)"', page)

    def test_link_preview_and_title(self, page):
        assert "<title>Docs - " in page
        assert 'property="og:title"' in page and 'rel="canonical"' in page

    def test_the_site_links_to_it(self, client):
        home = client.get("/").text
        assert re.search(r'<a class="header-link[^"]*" href="/docs"', home)
        assert 'href="/docs">Docs</a>' in home  # the footer

    def test_the_add_a_bot_page_points_beginners_at_the_guide(self, me):
        assert 'href="/docs#create-bot"' in me.get("/bots/new").text


class TestWhatItSays:
    def test_it_covers_the_whole_journey(self, page):
        for part in (
            "Create your bot on Fluxer",
            "Add your bot token",
            "Run your bot",
            "Download main.py",
            "Run main.py yourself",
            "Troubleshooting",
        ):
            assert part in page, part

    def test_the_token_is_never_asked_for_in_code(self, page):
        assert "FLUXER_TOKEN" in page
        assert "paste your token here" in page
        assert "Never put the token inside main.py" in page

    def test_the_invite_link_is_the_one_fluxer_documents(self, page):
        assert "https://fluxer.app/oauth2/authorize?client_id=YOUR_APPLICATION_ID&amp;scope=bot&amp;permissions=68608" in page

    def test_it_does_not_name_the_old_hosting_service(self, page):
        assert "wispbyte" not in page.lower()

    def test_the_sample_is_what_a_new_bot_really_gets(self, page):
        from html import unescape

        code = compile_workspace(default_workspace()).code.rstrip()
        assert unescape(page).count(code) == 1
        assert 'bot.run(os.environ["FLUXER_TOKEN"])' in code

    def test_the_run_instructions_match_the_ones_inside_the_zip(self, page):
        for line in ("pip install -r requirements.txt", "python3 main.py", "Python 3.10"):
            assert line in README or line in page
        assert "FLUXER_TOKEN" in README

    def test_the_limits_are_the_real_ones(self, page, settings):
        assert f"{settings.max_bots_per_user} bots" in page
        assert f"{settings.max_assets_per_bot} files" in page
        assert "There is no time limit" in page and "hours</strong>" not in page

    def test_nothing_secret_is_on_it(self, page, settings):
        assert settings.secret_key not in page and settings.fluxer_client_secret not in page


class TestTheBlocksReference:
    def test_every_block_in_the_editor_is_listed_once(self, page):
        shown = page.count('class="block-item"')
        assert shown == sum(len(blocks) for blocks in catalogue.TOOLBOX.values())

    def test_every_family_has_its_own_section_and_menu_entry(self, page):
        for key in catalogue.CATEGORY_ORDER:
            assert f'id="blocks-{key}"' in page
            assert f'href="#blocks-{key}"' in page

    def test_the_total_on_the_page_is_the_real_total(self, page):
        total = sum(len(blocks) for blocks in catalogue.TOOLBOX.values())
        assert f"<strong>{total} blocks in {len(catalogue.CATEGORY_ORDER)} families</strong>" in page

    def test_every_family_has_a_plain_introduction(self):
        assert set(guide.FAMILY_GUIDE) == set(catalogue.CATEGORY_ORDER)
        for key, text in guide.FAMILY_GUIDE.items():
            assert len(text["intro"]) > 80 and text["try"], key

    def test_every_block_has_words_to_search_for(self):
        for family in guide.families():
            for block in family["blocks"]:
                assert block["words"].strip() and block["tooltip"], block["type"]

    def test_where_a_block_works_is_said_in_words(self):
        assert guide.where_it_works(None) is None
        assert guide.where_it_works(("command",)) == "command events"
        for spec in catalogue.BLOCKS.values():
            label = guide.where_it_works(spec["needs"])
            assert label is None or "(" not in label and "'" not in label, spec["type"]

    def test_descriptions_are_escaped(self, page):
        assert "<script" not in page.split("<article", 1)[1].lower().replace("<script src", "")


def test_the_api_explorer_moved_out_of_the_way(client):
    """FastAPI's own explorer used to sit at /docs. Off production it is at /api-docs, so the guide can have /docs."""
    assert client.get("/api-docs").status_code == 200
    assert "Build a Fluxer bot" not in client.get("/api-docs").text


class TestCards:
    def test_the_embed_pop_up_is_explained(self, page):
        assert 'id="cards"' in page and "Card options" in page
        assert "img/docs/embed-popup.png" in page.replace("\\", "/") or "embed-popup" in page

    def test_the_picture_of_it_exists(self):
        from pathlib import Path

        assert (Path(__file__).resolve().parent.parent / "public" / "static" / "img" / "docs" / "embed-popup.png").exists()
