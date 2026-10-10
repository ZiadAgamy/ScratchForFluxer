"""Security primitives: token vault, signed cookies, CSRF, PKCE, redirects, bot-token hygiene, limits."""

from __future__ import annotations

import base64
import hashlib

import pytest
from starlette.requests import Request

from scratch_fluxer import security
from scratch_fluxer.config import Settings
from scratch_fluxer.security import (
    BotTokenFormatError,
    InvalidToken,
    RateLimiter,
    SessionCodec,
    TokenVault,
    client_ip,
    csrf_matches,
    is_same_origin,
    make_pkce,
    new_csrf,
    new_state,
    normalize_bot_token,
    safe_next,
    security_headers,
)

SECRET = "unit-test-secret-0123456789-abcdefghijklmnopqrstuvwxyz"
OTHER_SECRET = "a-completely-different-secret-9876543210-zyxwvutsrqp"


def make_request(
    headers: dict[str, str] | None = None, client: tuple[str, int] | None = ("203.0.113.9", 4000)
) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/x",
        "raw_path": b"/api/x",
        "query_string": b"",
        "headers": raw,
        "client": client,
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


# --------------------------------------------------------------------------- token vault
class TestTokenVault:
    TOKEN = "123456789012345678.AbCdEfGhIjKlMnOpQrStUv"

    def test_round_trip(self):
        vault = TokenVault(SECRET)
        assert vault.decrypt(vault.encrypt(self.TOKEN)) == self.TOKEN

    def test_ciphertext_hides_the_token(self):
        cipher = TokenVault(SECRET).encrypt(self.TOKEN)
        assert self.TOKEN not in cipher
        assert "AbCdEf" not in cipher
        assert base64.urlsafe_b64decode(cipher.encode()).find(b"AbCdEf") == -1

    def test_every_encryption_is_different(self):
        vault = TokenVault(SECRET)
        assert vault.encrypt(self.TOKEN) != vault.encrypt(self.TOKEN)

    def test_wrong_key_cannot_decrypt(self):
        cipher = TokenVault(SECRET).encrypt(self.TOKEN)
        with pytest.raises(InvalidToken):
            TokenVault(OTHER_SECRET).decrypt(cipher)

    def test_tampering_is_detected(self):
        vault = TokenVault(SECRET)
        cipher = vault.encrypt(self.TOKEN)
        flipped = cipher[:-4] + ("AAAA" if not cipher.endswith("AAAA") else "BBBB")
        with pytest.raises(InvalidToken):
            vault.decrypt(flipped)

    def test_key_rotation(self):
        old = TokenVault(OTHER_SECRET)
        cipher = old.encrypt(self.TOKEN)

        rotated = TokenVault(SECRET, previous=[OTHER_SECRET])
        assert rotated.decrypt(cipher) == self.TOKEN  # old data still readable

        upgraded = rotated.rotate(cipher)
        assert upgraded != cipher
        assert TokenVault(SECRET).decrypt(upgraded) == self.TOKEN  # now readable with the new key alone
        with pytest.raises(InvalidToken):
            TokenVault(OTHER_SECRET).decrypt(upgraded)  # and no longer with the old one alone

    def test_it_knows_whether_the_newest_key_made_a_token(self):
        newest = TokenVault(SECRET, previous=[OTHER_SECRET])
        assert newest.is_current(newest.encrypt(self.TOKEN)) is True
        assert newest.is_current(TokenVault(OTHER_SECRET).encrypt(self.TOKEN)) is False  # readable, but an older key
        assert (
            newest.is_current(TokenVault("a-third-secret-key-0123456789-abcdefghijklmnop").encrypt(self.TOKEN)) is False
        )

    def test_refreshed_gives_the_token_under_the_newest_key_only_when_needed(self):
        newest = TokenVault(SECRET, previous=[OTHER_SECRET])
        older = TokenVault(OTHER_SECRET).encrypt(self.TOKEN)
        again = newest.refreshed(older)
        assert again is not None and again != older
        assert TokenVault(SECRET).decrypt(again) == self.TOKEN  # the new key alone opens it
        assert newest.refreshed(again) is None  # already new: nothing to save
        assert newest.refreshed(newest.encrypt(self.TOKEN)) is None

    def test_refreshed_does_not_hide_a_token_nobody_can_read(self):
        stranger = TokenVault("a-third-secret-key-0123456789-abcdefghijklmnop").encrypt(self.TOKEN)
        with pytest.raises(InvalidToken):
            TokenVault(SECRET, previous=[OTHER_SECRET]).refreshed(stranger)

    @pytest.mark.parametrize("junk", ["", "not a token", "gAAAAA", "gAAAAA\u00e9", "\ud800", "x" * 5000])
    def test_anything_that_is_not_ciphertext_is_just_invalid(self, junk):
        vault = TokenVault(SECRET)
        with pytest.raises(InvalidToken):
            vault.decrypt(junk)
        with pytest.raises(InvalidToken):
            vault.rotate(junk)
        with pytest.raises(InvalidToken):
            vault.refreshed(junk)
        assert vault.is_current(junk) is False

    def test_a_vault_needs_a_key(self):
        with pytest.raises(ValueError):
            TokenVault("")


# --------------------------------------------------------------------------- signed cookies
class TestSessionCodec:
    def test_round_trip(self):
        codec = SessionCodec(SECRET)
        value = codec.dump_session({"u": "abc", "e": 3})
        assert codec.load_session(value, 60) == {"u": "abc", "e": 3}

    def test_rejects_other_secret(self):
        value = SessionCodec(SECRET).dump_session({"u": "abc"})
        assert SessionCodec(OTHER_SECRET).load_session(value, 60) is None

    def test_rejects_tampering(self):
        codec = SessionCodec(SECRET)
        value = codec.dump_session({"u": "abc"})
        head, _, tail = value.partition(".")
        assert codec.load_session("x" + head + "." + tail, 60) is None
        assert codec.load_session(value[:-2] + "zz", 60) is None

    def test_expiry(self):
        codec = SessionCodec(SECRET)
        value = codec.dump_session({"u": "abc"})
        assert codec.load_session(value, -1) is None

    def test_session_and_oauth_cookies_are_not_interchangeable(self):
        codec = SessionCodec(SECRET)
        assert codec.load_oauth(codec.dump_session({"u": "abc"}), 60) is None
        assert codec.load_session(codec.dump_oauth({"s": "state"}), 60) is None

    @pytest.mark.parametrize("junk", [None, "", "nope", "a.b.c", "x" * 5000, "\x00\x01"])
    def test_garbage_means_no_session(self, junk):
        assert SessionCodec(SECRET).load_session(junk, 60) is None


# --------------------------------------------------------------------------- csrf / oauth helpers
def test_csrf_matches():
    token = new_csrf()
    assert csrf_matches(token, token)
    assert not csrf_matches(token, token + "x")
    assert not csrf_matches(token, None)
    assert not csrf_matches(None, token)
    assert not csrf_matches("", "")
    assert new_csrf() != new_csrf()


def test_state_is_random():
    assert len({new_state() for _ in range(50)}) == 50


def test_pkce_s256():
    verifier, challenge = make_pkce()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in challenge
    assert make_pkce()[0] != verifier


# --------------------------------------------------------------------------- open-redirect guard
class TestSafeNext:
    @pytest.mark.parametrize(
        "value",
        ["/bots", "/bots/new", "/bots/abc123/edit?tab=assets", "/", "  /bots  "],
    )
    def test_allows_local_paths(self, value):
        assert safe_next(value) == value.strip()

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "   ",
            "bots",
            "http://evil.example",
            "https://evil.example/bots",
            "//evil.example",
            "///evil.example",
            "/\\evil.example",
            "\\\\evil.example",
            "javascript:alert(1)",
            "/\t/evil.example",  # browsers strip tabs/newlines and would read this as //evil.example
            "/\n/evil.example",
            "/bots\r\nSet-Cookie: pwn=1",
            "/auth/login",
            "/auth/callback?code=1",
            "/api/me",
            "/" + "a" * 400,
            123,
            ["/bots"],
        ],
    )
    def test_rejects_everything_else(self, value):
        assert safe_next(value) == "/bots"

    def test_custom_default(self):
        assert safe_next("//evil.example", default="/") == "/"


# --------------------------------------------------------------------------- same-origin check
class TestSameOrigin:
    BASE = "https://scratch.example"

    def test_no_headers_is_allowed(self):
        assert is_same_origin(make_request(), self.BASE)

    @pytest.mark.parametrize(
        "site,ok", [("same-origin", True), ("none", True), ("same-site", False), ("cross-site", False)]
    )
    def test_fetch_metadata(self, site, ok):
        assert is_same_origin(make_request({"Sec-Fetch-Site": site}), self.BASE) is ok

    def test_origin_header(self):
        assert is_same_origin(make_request({"Origin": "https://scratch.example"}), self.BASE)
        assert is_same_origin(make_request({"Origin": "https://scratch.example/"}), self.BASE)
        assert not is_same_origin(make_request({"Origin": "https://evil.example"}), self.BASE)
        assert not is_same_origin(make_request({"Origin": "http://scratch.example"}), self.BASE)
        assert not is_same_origin(make_request({"Origin": "https://scratch.example.evil.example"}), self.BASE)

    def test_falls_back_to_host_without_base_url(self):
        assert is_same_origin(make_request({"Origin": "http://localhost:8000", "Host": "localhost:8000"}), None)
        assert not is_same_origin(make_request({"Origin": "http://evil.example", "Host": "localhost:8000"}), None)


# --------------------------------------------------------------------------- bot tokens
GOOD_TOKEN = "123456789012345678.AbCdEfGhIjKlMnOpQrStUvWx"


class TestNormalizeBotToken:
    def test_accepts_a_token(self):
        assert normalize_bot_token(GOOD_TOKEN) == GOOD_TOKEN

    @pytest.mark.parametrize(
        "pasted",
        [
            f"  {GOOD_TOKEN}  ",
            f'"{GOOD_TOKEN}"',
            f"'{GOOD_TOKEN}'",
            f"`{GOOD_TOKEN}`",
            f"Bot {GOOD_TOKEN}",
            f"bot {GOOD_TOKEN}",
            f"\n{GOOD_TOKEN}\n",
        ],
    )
    def test_cleans_common_paste_artifacts(self, pasted):
        assert normalize_bot_token(pasted) == GOOD_TOKEN

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            "",
            "   ",
            "123456789012345678",  # no secret part
            "AbCdEfGhIjKlMnOpQrStUvWx",  # a client secret, not a bot token
            "abc.AbCdEfGhIjKlMnOpQrStUvWx",  # application id must be numeric
            "123456789012345678.short",
            "123.AbCdEfGh IjKlMnOp",  # whitespace inside
            "123.AbCdEfGh\nIjKlMnOp",
            "١٢٣٤٥٦٧٨٩.AbCdEfGhIjKlMnOpQrStUv",  # Arabic-Indic digits
            "123456789012345678." + "A" * 400,
            "<script>alert(1)</script>",
            "123456789012345678.AbCdEfGh<script>",
        ],
    )
    def test_rejects_things_that_are_not_tokens(self, bad):
        with pytest.raises(BotTokenFormatError) as info:
            normalize_bot_token(bad)
        message = str(info.value)
        assert message  # always a human-readable message
        if bad and len(bad.strip()) >= 8:
            assert bad.strip() not in message  # never echoes what was pasted (it may be a secret)


# --------------------------------------------------------------------------- rate limiting
class TestRateLimiter:
    def test_blocks_after_limit_and_recovers(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(security.time, "monotonic", lambda: now[0])
        rl = RateLimiter()
        assert all(rl.allow("k", 3, 10) for _ in range(3))
        assert not rl.allow("k", 3, 10)
        now[0] += 10.5
        assert rl.allow("k", 3, 10)

    def test_keys_are_independent(self):
        rl = RateLimiter()
        assert rl.allow("a", 1, 60)
        assert not rl.allow("a", 1, 60)
        assert rl.allow("b", 1, 60)

    def test_reset(self):
        rl = RateLimiter()
        rl.allow("a", 1, 60)
        rl.reset()
        assert rl.allow("a", 1, 60)

    def test_a_flood_of_different_addresses_cannot_fill_the_memory(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(security.time, "monotonic", lambda: now[0])
        rl = RateLimiter()
        for number in range(RateLimiter.KEEP_AT_MOST * 2):
            now[0] += 0.001  # all inside one minute: every key is still needed, so the oldest are forgotten
            rl.allow(f"ip-{number}", 5, 60)
        assert len(rl._hits) <= RateLimiter.KEEP_AT_MOST + 1000
        assert not rl._hits.keys() & {"ip-0", "ip-1"}  # the first to come are the first to go
        assert f"ip-{RateLimiter.KEEP_AT_MOST * 2 - 1}" in rl._hits

    def test_clearing_runs_at_most_once_a_second(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(security.time, "monotonic", lambda: now[0])
        rl = RateLimiter()
        scans = []
        original = rl._clear
        monkeypatch.setattr(rl, "_clear", lambda at: (scans.append(at), original(at))[1])
        for number in range(RateLimiter.CLEAR_ABOVE + 3000):
            now[0] += 0.0001  # 1.3 seconds in all
            rl.allow(f"ip-{number}", 5, 60)
        assert 1 <= len(scans) <= 2

    def test_keys_with_a_long_window_survive_a_clear_made_for_short_ones(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(security.time, "monotonic", lambda: now[0])
        rl = RateLimiter()
        assert rl.allow("slow", 1, 600)  # one hit allowed every ten minutes
        now[0] += 120
        for number in range(RateLimiter.CLEAR_ABOVE + 10):
            now[0] += 0.01
            rl.allow(f"busy-{number}", 5, 10)
        now[0] += 1
        rl.allow("trigger", 5, 10)
        assert not rl.allow("slow", 1, 600)  # still remembered


class TestClientIp:
    def test_uses_socket_address_by_default(self):
        request = make_request({"X-Forwarded-For": "6.6.6.6"})
        assert client_ip(request, Settings(trust_proxy=False)) == "203.0.113.9"  # header is ignored: it can be forged

    def test_behind_a_proxy_of_your_own_the_entry_the_proxy_added_is_used(self):
        # The proxy adds the address it saw to the end; whatever the visitor sent stays in front of it.
        request = make_request({"X-Forwarded-For": "6.6.6.6, 198.51.100.7"})
        assert client_ip(request, Settings(trust_proxy=True)) == "198.51.100.7"

    def test_a_visitor_cannot_choose_their_address_by_writing_headers(self):
        for header in ("X-Forwarded-For", "X-Real-IP", "X-Vercel-Forwarded-For"):
            request = make_request({header: "6.6.6.6", "X-Forwarded-For": "6.6.6.6, 198.51.100.7"})
            assert client_ip(request, Settings(trust_proxy=True)) == "198.51.100.7"
        assert client_ip(make_request({"X-Real-IP": "6.6.6.6"}), Settings(trust_proxy=True)) == "203.0.113.9"

    def test_with_two_proxies_in_a_row_the_second_entry_from_the_end_is_used(self):
        request = make_request({"X-Forwarded-For": "6.6.6.6, 198.51.100.7, 10.0.0.1"})
        assert client_ip(request, Settings(trust_proxy=True, trusted_proxy_hops=2)) == "198.51.100.7"

    def test_a_request_that_did_not_come_through_the_proxies_falls_back_to_the_connection(self):
        request = make_request({"X-Forwarded-For": "198.51.100.7"})
        assert client_ip(request, Settings(trust_proxy=True, trusted_proxy_hops=2)) == "203.0.113.9"
        assert client_ip(make_request(), Settings(trust_proxy=True)) == "203.0.113.9"

    @pytest.mark.parametrize("hops", [0, -1, 11])
    def test_the_number_of_proxies_must_be_sensible(self, hops):
        with pytest.raises(ValueError, match="trusted_proxy_hops"):
            Settings(trusted_proxy_hops=hops)

    def test_on_vercel_what_vercel_wrote_is_used(self, monkeypatch):
        monkeypatch.setattr(security, "ON_VERCEL", True)
        request = make_request({"X-Vercel-Forwarded-For": "198.51.100.7", "X-Forwarded-For": "6.6.6.6"})
        assert client_ip(request, Settings()) == "198.51.100.7"

    def test_no_client(self):
        assert client_ip(make_request(client=None), Settings()) == "unknown"


# --------------------------------------------------------------------------- headers
class TestHeaders:
    def test_csp_is_strict(self):
        csp = security_headers(Settings())["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
        assert "'unsafe-eval'" not in csp
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp
        assert "base-uri 'none'" in csp
        assert "form-action 'self'" in csp
        assert "connect-src 'self'" in csp

    def test_basic_hardening_headers(self):
        headers = security_headers(Settings())
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in headers["Permissions-Policy"]

    def test_hsts_only_over_https(self):
        assert "Strict-Transport-Security" not in security_headers(Settings(app_base_url="http://localhost:8000"))
        assert (
            "max-age="
            in security_headers(Settings(app_base_url="https://scratch.example"))["Strict-Transport-Security"]
        )
