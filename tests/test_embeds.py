"""The options of an embed block: what is kept, what is refused, what Python they become."""

from __future__ import annotations

import ast
import json

import pytest
from blocks import block, command, workspace

from scratch_fluxer import codecheck, embeds
from scratch_fluxer import workspace as ws_lib
from scratch_fluxer.compiler import compile_workspace


def card(kind="sff_reply_embed", options=None, **fields):
    if options is not None:
        fields["OPTIONS"] = options if isinstance(options, str) else json.dumps(options)
    return workspace(
        command("card", block(kind, id="e1", fields=fields or None, inputs={"TITLE": "Rules", "TEXT": "Be kind."}))
    )


def compiled(state, assets=None):
    cleaned = ws_lib.clean_workspace(state).workspace
    return compile_workspace(cleaned, assets=assets)


def lines(result):
    return result.code.splitlines()


class TestWhatIsKept:
    def test_nothing_set_is_an_empty_object(self):
        assert embeds.clean("") == "{}"
        assert embeds.clean(None) == "{}"
        assert embeds.clean("not json") == "{}"
        assert embeds.clean("[1, 2]") == "{}"

    def test_the_default_colour_is_not_stored(self):
        assert embeds.clean('{"color": "blue"}') == "{}"

    def test_a_colour_is_a_name_or_six_hex_digits(self):
        assert embeds.parse('{"color": "red"}') == {"color": "red"}
        assert embeds.parse('{"color": "#AABBCC"}') == {"color": "#aabbcc"}
        for bad in ("#abc", "ff0000", "javascript:1", "#gggggg", "pink; DROP", 5, None):
            assert embeds.parse(json.dumps({"color": bad})) == {}

    @pytest.mark.parametrize(
        "address",
        ["javascript:alert(1)", "data:text/html,x", "ftp://example.com/a", "https://exa mple.com", "https://x.com/'a", "//x.com", "https://x.com/\"", "https://x.com/a\\b", "file:///etc/passwd"],
    )
    def test_only_plain_web_addresses_are_kept(self, address):
        assert embeds.parse(json.dumps({"url": address, "thumbnail": address, "author": "A", "author_icon": address})) == {
            "author": "A"
        }

    def test_a_web_address_that_is_too_long_is_dropped(self):
        assert embeds.parse(json.dumps({"url": "https://x.com/" + "a" * 600})) == {}

    def test_text_is_cleaned_and_cut_short(self):
        kept = embeds.parse(json.dumps({"author": "  Me‮\n\tthere  " + "x" * 300, "footer": "ok"}))
        assert "‮" not in kept["author"] and "\n" not in kept["author"] and len(kept["author"]) <= 100
        assert kept["footer"] == "ok"

    def test_an_icon_needs_its_name_or_text(self):
        assert embeds.parse('{"author_icon": "https://x.com/a.png", "footer_icon": "https://x.com/f.png"}') == {}

    def test_unknown_keys_and_wrong_types_are_dropped(self):
        assert embeds.parse('{"hack": "x", "time": "yes", "author": 5, "image": 5}') == {}
        assert embeds.parse('{"time": true}') == {"time": True}

    def test_uploaded_pictures_only_when_the_block_can_send_files(self):
        raw = '{"image": "file:banner.png", "thumbnail": "file:notes.txt"}'
        assert embeds.parse(raw) == {"image": "file:banner.png"}
        assert embeds.parse(raw, files=False) == {}
        assert embeds.parse('{"image": "file:../secret.png"}') == {}

    def test_it_is_in_its_plain_form_after_cleaning_once(self):
        raw = json.dumps({"url": "https://x.com", "color": "#FF0000", "time": True, "footer": "a", "author": "b"})
        once = embeds.clean(raw)
        assert embeds.clean(once) == once
        assert json.loads(once)["color"] == "#ff0000"

    def test_an_enormous_value_is_not_even_read(self):
        assert embeds.parse('{"footer": "' + "a" * 20000 + '"}') == {}


class TestInTheBlocks:
    def test_an_old_block_with_a_colour_menu_keeps_its_colour(self):
        cleaned = ws_lib.clean_workspace(card(COLOR="purple")).workspace
        text = json.dumps(cleaned)
        assert '"OPTIONS": "{\\"color\\":\\"purple\\"}"' in text
        assert "COLOR" not in text
        assert "color=0xA98BFF" in compiled(card(COLOR="purple")).code

    def test_a_plain_card_is_still_one_line(self):
        result = compiled(card())
        assert 'embed = fluxer.Embed(title="Rules", description="Be kind.", color=0x176DFF)' in result.code
        assert not result.diagnostics

    def test_every_option_becomes_the_call_that_sets_it(self):
        options = {
            "color": "#ff8800",
            "url": "https://example.com/rules",
            "image": "https://example.com/banner.png",
            "thumbnail": "https://example.com/small.png",
            "author": "The team",
            "author_icon": "https://example.com/me.png",
            "footer": "Read twice",
            "footer_icon": "https://example.com/f.png",
            "time": True,
        }
        result = compiled(card(options=options))
        assert not result.diagnostics, result.diagnostics
        code = result.code
        for expected in (
            "color=0xFF8800,",
            'url="https://example.com/rules",',
            "timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),",
            'embed.set_image(url="https://example.com/banner.png")',
            'embed.set_thumbnail(url="https://example.com/small.png")',
            'embed.set_author(name="The team", icon_url="https://example.com/me.png")',
            'embed.set_footer(text="Read twice", icon_url="https://example.com/f.png")',
            "await ctx.reply(embed=embed)",
        ):
            assert expected in code
        assert "import datetime" in code
        ast.parse(code)
        codecheck.check(code)  # raises when anything in it is not allowed

    def test_an_uploaded_picture_is_sent_with_the_message_under_a_plain_name(self):
        result = compiled(card(options={"image": "file:My Banner (1).png", "thumbnail": "file:icon.jpg"}),
                          assets={"My Banner (1).png": "image", "icon.jpg": "image"})
        assert not result.diagnostics, result.diagnostics
        code = result.code
        assert 'embed.set_image(url="attachment://image.png")' in code
        assert 'embed.set_thumbnail(url="attachment://thumbnail.jpg")' in code
        assert 'files=[fluxer.File(ASSETS / "My Banner (1).png", filename="image.png"), ' in code
        assert 'fluxer.File(ASSETS / "icon.jpg", filename="thumbnail.jpg")' in code
        assert 'ASSETS = Path(__file__).parent / "assets"' in code

    def test_one_uploaded_picture_is_sent_as_file(self):
        result = compiled(card(options={"image": "file:a.png"}), assets={"a.png": "image"})
        assert 'await ctx.reply(embed=embed, file=fluxer.File(ASSETS / "a.png", filename="image.png"))' in result.code

    def test_a_picture_that_is_not_uploaded_is_an_error_on_the_block(self):
        result = compiled(card(options={"image": "file:gone.png"}), assets={})
        assert any("gone.png" in d.message for d in result.diagnostics if d.level == "error")

    def test_a_webhook_cannot_send_a_file(self):
        result = compiled(card("sff_webhook_embed", options={"image": "file:a.png", "thumbnail": "https://x.com/t.png"}), assets={"a.png": "image"})
        assert "attachment://" not in result.code and "fluxer.File" not in result.code
        assert 'embed.set_thumbnail(url="https://x.com/t.png")' in result.code
        assert "await send_webhook(" in result.code

    def test_a_forged_value_never_reaches_the_python(self):
        evil = {"url": 'https://x.com/");import os#', "author": '"); os.system("x', "footer": "\\\"\\n"}
        result = compiled(card(options=evil))
        assert "os.system" not in result.code or 'name="\\"); os.system(\\"x"' in result.code
        ast.parse(result.code)

    def test_the_files_a_card_uses_count_as_used(self):
        cleaned = ws_lib.clean_workspace(card(options={"image": "file:a.png", "thumbnail": "file:a.png"})).workspace
        assert ws_lib.files_used(cleaned)["a.png"] == 1

    def test_the_server_cleans_whatever_the_browser_sends(self):
        cleaned = ws_lib.clean_workspace(card(options='{"color": "#ABCDEF", "bogus": 1}')).workspace
        assert '{\\"color\\":\\"#abcdef\\"}' in json.dumps(cleaned)
