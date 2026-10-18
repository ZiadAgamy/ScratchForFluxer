"""The changelog page: dated entries, newest first, drawn from one list."""

from __future__ import annotations

import datetime
import re

import pytest

from scratch_fluxer import changelog


def test_every_entry_is_well_formed():
    assert changelog.ENTRIES
    for entry in changelog.ENTRIES:
        datetime.date.fromisoformat(entry["date"])
        assert entry["title"].strip() and entry["summary"].strip()
        assert entry["changes"], entry["title"]
        for kind, text in entry["changes"]:
            assert kind in changelog.KINDS, (entry["title"], kind)
            assert text.strip() and "<" not in text and ">" not in text


def test_entries_are_newest_first_and_only_the_first_is_latest():
    shown = changelog.entries()
    days = [e["day"] for e in shown]
    assert days == sorted(days, reverse=True)
    assert [e["latest"] for e in shown] == [True] + [False] * (len(shown) - 1)


def test_dates_are_written_out():
    assert changelog.entries()[0]["pretty"] == "18 October 2026"
    assert re.fullmatch(r"\d{1,2} [A-Z][a-z]+ \d{4}", changelog.entries()[-1]["pretty"])


def test_entries_of_one_day_share_a_group():
    groups = changelog.by_date()
    assert sum(len(g["entries"]) for g in groups) == len(changelog.ENTRIES)
    assert len({g["day"] for g in groups}) == len(groups)


class TestThePage:
    def test_it_is_public_and_lists_everything(self, client):
        r = client.get("/changelog")
        assert r.status_code == 200
        for entry in changelog.ENTRIES:
            assert entry["title"] in r.text
        assert "Latest" in r.text and "16 October 2026" in r.text

    def test_each_jump_link_points_at_a_date_on_the_page(self, client):
        page = client.get("/changelog").text
        targets = re.findall(r'href="#(d-[0-9-]+)"', page)
        assert targets
        for target in targets:
            assert f'id="{target}"' in page

    def test_it_is_linked_from_the_header_and_the_footer(self, client):
        for path in ("/", "/docs", "/changelog"):
            page = client.get(path).text
            assert 'href="/changelog"' in page, path

    def test_the_current_page_is_marked(self, client):
        page = client.get("/changelog").text
        assert re.search(r'<a class="header-link header-link-changelog is-current" href="/changelog"', page)
        assert 'aria-current="page"' in page

    def test_text_is_escaped(self, client, monkeypatch):
        monkeypatch.setattr(
            changelog,
            "ENTRIES",
            [
                {
                    "date": "2026-01-02",
                    "title": "<script>x</script>",
                    "summary": "a & b",
                    "changes": [("new", "<b>hi</b>")],
                }
            ],
        )
        page = client.get("/changelog").text
        assert "<script>x</script>" not in page and "&lt;script&gt;" in page
        assert "<b>hi</b>" not in page

    def test_it_works_logged_in(self, client, make_user):
        _, _, cookies = make_user(username="agamy", global_name="AGAMY")
        client.cookies.update(cookies)
        assert client.get("/changelog").status_code == 200


def test_the_changelog_never_names_the_old_host():
    blob = " ".join(e["title"] + e["summary"] + " ".join(t for _k, t in e["changes"]) for e in changelog.ENTRIES)
    assert "wispbyte" not in blob.lower()


def test_the_style_sheet_exists():
    from pathlib import Path

    assert (Path(__file__).resolve().parent.parent / "public" / "static" / "css" / "changelog.css").is_file()


@pytest.mark.parametrize("path", ["/changelog"])
def test_security_headers_are_there(client, path):
    r = client.get(path)
    assert "content-security-policy" in {k.lower() for k in r.headers}
