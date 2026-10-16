"""Codenames: ``$authorID``, ``$varCoins`` ... written inside a text box instead of a block and a join."""

from __future__ import annotations

import ast
import json

from blocks import block, command, on_join, reply, show, text, workspace

from scratch_fluxer import catalogue, codecheck
from scratch_fluxer import workspace as ws_lib
from scratch_fluxer.compiler import compile_workspace


def build(state):
    return compile_workspace(ws_lib.clean_workspace(state).workspace)


def reply_ws(value, variables=()):
    return workspace(command("t", reply(value)), variables=variables)


def body(result):
    return [line.strip() for line in result.code.splitlines() if "reply(" in line][0]


def warnings(result):
    return [d.message for d in result.diagnostics if d.level == "warning"]


class TestTheList:
    def test_every_codename_is_a_block_without_sockets(self):
        for name, type_, _group, _what in catalogue.CODENAME_LIST:
            spec = catalogue.BLOCKS[type_]
            assert spec["shape"] in ("value", "boolean"), name
            assert spec["returns"] in ("text", "number"), name

    def test_names_are_plain_words(self):
        for name, *_ in catalogue.CODENAME_LIST:
            assert name.isalnum() and name[0].isalpha()

    def test_the_browser_gets_the_list(self):
        names = [c["name"] for c in catalogue.browser_payload()["codenames"]["blocks"]]
        assert "authorID" in names and "messageID" in names and "communityID" in names


class TestBlocks:
    def test_a_codename_is_the_block(self):
        r = build(reply_ws("Hi $authorName, you are $authorID"))
        assert r.ok, r.diagnostics
        assert body(r) == 'await ctx.reply("Hi " + ctx.author.display_name + ", you are " + str(ctx.author.id))'
        ast.parse(r.code)

    def test_it_matches_the_blocks_own_code(self):
        by_block = build(workspace(command("t", reply(block("sff_msg_id")))))
        by_name = build(reply_ws("$messageID"))
        assert by_block.ok and by_name.ok
        assert body(by_name) == body(by_block)

    def test_case_does_not_matter(self):
        assert build(reply_ws("$AUTHORID")).code == build(reply_ws("$authorID")).code

    def test_braces_let_text_follow_directly(self):
        r = build(reply_ws("${authorID}abc"))
        assert r.ok and "abc" in r.code
        assert not warnings(r)

    def test_community_id_works_in_a_member_event_too(self):
        r = build(workspace(on_join(show("Welcome to $communityID"))))
        assert not [d for d in r.diagnostics if d.level == "error"], r.diagnostics

    def test_numbers_become_text(self):
        r = build(reply_ws("Ping: $botPing ms"))
        assert r.ok
        ast.parse(r.code)

    def test_where_the_block_cannot_be_used_it_says_so(self):
        r = build(workspace(on_join(show("$messageID"))))
        assert not r.ok
        assert any("message" in d.message for d in r.diagnostics)

    def test_the_text_stays_text_not_code(self):
        r = build(reply_ws('"); import os; os.system("x") $authorID {x} {{y}}'))
        assert r.ok
        ast.parse(r.code)


class TestVariables:
    def vars(self):
        return (("Coins", ""), ("Shop List", "List"))

    def test_a_variable(self):
        r = build(reply_ws("Coins Balance: $varCoins", self.vars()))
        assert r.ok, r.diagnostics
        assert 'f"Coins Balance: {coins}"' in r.code

    def test_a_variable_with_spaces_in_braces(self):
        r = build(reply_ws("${var Coins} / ${var:Coins}", self.vars()))
        assert r.ok and r.code.count("{coins}") == 2

    def test_a_list_is_its_items(self):
        r = build(reply_ws("Shop: $listShopList", self.vars()))
        assert r.ok, r.diagnostics
        assert '", ".join(str(item) for item in shop_list)' in r.code

    def test_a_count(self):
        r = build(reply_ws("Items: $countShopList", self.vars()))
        assert r.ok and "len(shop_list)" in r.code

    def test_the_wrong_kind_is_explained(self):
        r = build(reply_ws("$varShopList", self.vars()))
        assert any("$list" in w for w in warnings(r))

    def test_an_unknown_variable_stays_as_written(self):
        r = build(reply_ws("$varNope", self.vars()))
        assert "$varNope" in r.code
        assert any("varNope" in w for w in warnings(r))

    def test_the_variable_is_set_up_once(self):
        r = build(reply_ws("$varCoins", self.vars()))
        assert "coins = 0" in r.code or "coins =" in r.code


class TestEscapes:
    def test_double_dollar_is_a_dollar(self):
        r = build(reply_ws("Costs $$5 and $$authorID"))
        assert r.ok and not warnings(r)
        assert "Costs $5 and $authorID" in r.code

    def test_prices_are_left_alone(self):
        r = build(reply_ws("It costs $5."))
        assert r.ok and not warnings(r)
        assert "$5" in r.code

    def test_an_unknown_codename_warns_once_and_stays(self):
        r = build(reply_ws("$nothing $nothing"))
        assert r.ok
        assert len(warnings(r)) == 1
        assert "$nothing $nothing" in r.code

    def test_text_without_a_dollar_is_unchanged(self):
        assert build(reply_ws("Hello")).code == build(workspace(command("t", reply(text("Hello"))))).code


class TestEmbeds:
    def card(self, options, **inputs):
        return workspace(
            command(
                "c",
                block(
                    "sff_reply_embed",
                    id="e1",
                    fields={"OPTIONS": json.dumps(options)},
                    inputs={"TITLE": "Hi $authorName", "TEXT": "ID: $authorID", **inputs},
                ),
            )
        )

    def test_title_and_text_work(self):
        r = build(self.card({}))
        assert r.ok and "ctx.author" in r.code

    def test_author_and_footer_work(self):
        r = build(self.card({"author": "By $authorName", "footer": "Message $messageID"}))
        assert r.ok, r.diagnostics
        assert 'set_author(name=f"By {ctx.author.display_name}")' in r.code
        assert 'set_footer(text="Message " + str(ctx.id))' in r.code
        ast.parse(r.code)

    def test_plain_author_is_still_a_plain_string(self):
        r = build(self.card({"author": "Staff", "footer": "Rules"}))
        assert 'set_author(name="Staff")' in r.code and 'set_footer(text="Rules")' in r.code


class TestTheRunnerAcceptsIt:
    def test_the_python_passes_the_allow_list(self):
        variables = (("Coins", ""), ("Shop", "List"))
        every = " ".join(f"${name}" for name, *_ in catalogue.CODENAME_LIST)
        for value in (every, "$varCoins $listShop $countShop ${var Coins}", "$$ $5 $nope"):
            r = build(reply_ws(value, variables))
            codecheck.check(r.code)  # raises when anything in it is not allowed
