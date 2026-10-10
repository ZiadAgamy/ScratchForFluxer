"""The rules for file names and file contents (the API tests go through these too; this checks them directly)."""

from __future__ import annotations

import random
import string

import pytest

from scratch_fluxer import assets


class TestNames:
    @pytest.mark.parametrize(
        "name",
        ["cat.png", "My Cat (2).JPG", "a-b_c.json", "notes.txt", "x.md", "table.csv", "名前.json", "ünï.webp", "1.gif"],
    )
    def test_plain_names_pass(self, name):
        assert assets.clean_name(name) == name and assets.is_safe_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "",
            " ",
            ".png",
            "png",
            "a.exe",
            "a.svg",
            "a.html",
            "a.png ",
            "a/b.png",
            "a\\b.png",
            "../a.png",
            "a..png",
            "a\x00.png",
            "a\n.png",
            "con.txt",
            "COM3.png",
            "lpt9.json",
            "-a.png",
            ".a.png",
            "a" * 77 + ".png",
            None,
            5,
            b"a.png",
            ["a.png"],
        ],
    )
    def test_other_names_are_refused_or_tidied(self, name):
        cleaned = assets.clean_name(name)
        assert cleaned is None or cleaned != name or assets.is_safe_name(cleaned)
        assert not assets.is_safe_name(name)

    def test_surrounding_space_and_odd_unicode_are_tidied(self):
        assert assets.clean_name("  cat.png  ") == "cat.png"
        assert assets.clean_name("café.txt") == "café.txt"

    def test_the_limit_is_80_characters(self):
        assert assets.clean_name("a" * 76 + ".png") is not None
        assert assets.clean_name("a" * 77 + ".png") is None

    def test_whatever_comes_out_is_safe_to_join_to_a_folder(self):
        """Random text: anything accepted is a single, plain file name with an allowed extension."""
        rng = random.Random(7)
        alphabet = string.ascii_letters + string.digits + " ._-()/\\:;*?\"<>|'`$%&+=~^,!@#{}[]\t\n\x00" + "éüñ名‮😀"
        extensions = ["", *assets.TYPES]
        accepted = 0
        for _ in range(20_000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24))) + rng.choice(extensions)
            name = assets.clean_name(text)
            if name is None:
                continue
            accepted += 1
            assert assets.is_safe_name(name) and assets.clean_name(name) == name  # tidying twice changes nothing
            assert 1 <= len(name) <= assets.MAX_NAME and name[0].isalnum() and ".." not in name
            assert not set(name) & set("/\\:*?\"<>|;'`$%&+=~^,!@#{}[]\t\n\r\x00‮")
            assert assets.extension(name) in assets.TYPES
        assert accepted > 500  # the test really did see accepted names


class TestContents:
    def test_each_extension_knows_its_kind_and_type(self):
        for ext, (kind, mime) in assets.TYPES.items():
            assert kind in assets.KINDS and mime
            assert assets.kind_of_name(f"a{ext}") == kind and assets.kind_of_name(f"A{ext.upper()}") == kind
        assert assets.kind_of_name("a.exe") is None and assets.kind_of_name("noext") is None

    def test_the_kinds_are_the_ones_the_blocks_use(self):
        from scratch_fluxer import catalogue

        assert tuple(assets.KINDS) == tuple(catalogue.ASSET_KINDS)

    def test_the_label_names_every_extension(self):
        for ext in assets.TYPES:
            assert ext[1:].upper() in assets.ACCEPTED_LABEL.upper() or ext == ".jpeg"  # .jpeg is shown as JPG

    def test_random_bytes_never_pass_as_an_image_or_json(self):
        rng = random.Random(11)
        for _ in range(3000):
            data = bytes(rng.randrange(256) for _ in range(rng.randint(0, 40)))
            for ext in (".png", ".jpg", ".gif", ".webp", ".json"):
                upload, problem = assets.inspect(f"a{ext}", data)
                if upload is not None:
                    assert upload.kind == assets.TYPES[ext][0] and upload.data and problem == ""
                else:
                    assert problem and problem.endswith(".")

    def test_svg_is_not_an_image_here(self):
        assert assets.clean_name("logo.svg") is None
        assert assets.kind_of_name("logo.svg") is None

    def test_a_png_that_is_a_script_is_refused(self):
        upload, problem = assets.inspect("a.png", b"<script>alert(1)</script>")
        assert upload is None and "PNG" in problem

    def test_text_keeps_its_bytes_except_for_a_byte_order_mark(self):
        upload, _ = assets.inspect("a.txt", "héllo\r\nwörld".encode())
        assert upload is not None and upload.data == "héllo\r\nwörld".encode() and upload.kind == "text"
        upload, _ = assets.inspect("a.txt", b"\xef\xbb\xbfhi")
        assert upload is not None and upload.data == b"hi"

    @pytest.mark.parametrize("data", [b"\xff", b"\xc3", b"a\x00b", b"\x00"])
    def test_text_must_be_utf8_without_nul(self, data):
        assert assets.inspect("a.txt", data)[0] is None

    def test_empty_files_are_refused(self):
        for ext in assets.TYPES:
            assert assets.inspect(f"a{ext}", b"")[0] is None
