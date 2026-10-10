"""What the website and the runner say to each other: the start request, and the signature on every call."""

from __future__ import annotations

import pytest

from scratch_fluxer.config import Settings
from scratch_fluxer.runner import protocol
from scratch_fluxer.runner.protocol import (
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    NotAllowed,
    Refused,
    StartRequest,
    Verifier,
    sign_headers,
    signature,
    validate_start,
)

BOT = "a1" * 16
OWNER = "b2" * 16
TOKEN = "111111111111111111.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1234"
SECRET = "r" * 40
CODE = 'print("hi")\n'


def make(**changes) -> StartRequest:
    values = {"bot_id": BOT, "owner": OWNER, "code": CODE, "token": TOKEN, "files": []}
    values.update(changes)
    return StartRequest(**values)


settings = Settings(_env_file=None)


class TestStartRequest:
    def test_it_survives_the_trip(self):
        import json

        request = make(files=[("a.json", b'{"x": 1}'), ("b.png", bytes(range(256)))])
        again = StartRequest.from_json(json.loads(request.to_json()))
        assert again.bot_id == BOT and again.owner == OWNER and again.code == CODE and again.token == TOKEN
        assert again.files == [("a.json", b'{"x": 1}'), ("b.png", bytes(range(256)))]

    def test_it_never_prints_the_token(self):
        assert TOKEN not in repr(make()) and TOKEN.split(".")[1] not in repr(make())
        assert TOKEN not in str(make())

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            [],
            "text",
            {},
            {"bot_id": BOT},
            {"bot_id": BOT, "owner": OWNER, "code": CODE, "token": TOKEN},  # no files
            {"bot_id": 1, "owner": OWNER, "code": CODE, "token": TOKEN, "files": []},
            {"bot_id": BOT, "owner": OWNER, "code": CODE, "token": TOKEN, "files": "none"},
            {"bot_id": BOT, "owner": OWNER, "code": CODE, "token": TOKEN, "files": [1]},
            {"bot_id": BOT, "owner": OWNER, "code": CODE, "token": TOKEN, "files": [{"name": "a.txt"}]},
            {"bot_id": BOT, "owner": OWNER, "code": CODE, "token": TOKEN, "files": [{"name": "a.txt", "data": 5}]},
            {"bot_id": BOT, "owner": OWNER, "code": CODE, "token": TOKEN, "files": [{"name": "a.txt", "data": "***"}]},
        ],
    )
    def test_a_request_that_is_not_one_is_refused_not_a_crash(self, raw):
        with pytest.raises(Refused):
            StartRequest.from_json(raw)


class TestValidation:
    def test_a_good_request_passes(self):
        validate_start(make(files=[("data.json", b"{}")]), settings)

    @pytest.mark.parametrize("bot_id", ["", "short", "Z" * 32, BOT.upper(), BOT + "0", "../" + BOT[3:]])
    def test_the_bot_id_must_be_32_lowercase_hex_digits(self, bot_id):
        with pytest.raises(Refused):
            validate_start(make(bot_id=bot_id), settings)

    @pytest.mark.parametrize("owner", ["", "someone", OWNER[:-1]])
    def test_the_owner_must_be_an_id_too(self, owner):
        with pytest.raises(Refused):
            validate_start(make(owner=owner), settings)

    @pytest.mark.parametrize("code", ["", "   \n", "x" * (protocol.MAX_CODE_CHARS + 1)])
    def test_there_must_be_code_and_not_too_much(self, code):
        with pytest.raises(Refused):
            validate_start(make(code=code), settings)

    @pytest.mark.parametrize("token", ["", "has space", "line\nbreak", "tab\there", "x" * 301, "nul\x00"])
    def test_a_token_that_cannot_be_one_is_refused(self, token):
        with pytest.raises(Refused):
            validate_start(make(token=token), settings)

    @pytest.mark.parametrize(
        "name", ["../x.json", "a/b.json", "x.py", "x.svg", ".json", "", "a" * 90 + ".json", "CON.txt"]
    )
    def test_file_names_are_the_plain_ones_an_upload_can_have(self, name):
        with pytest.raises(Refused):
            validate_start(make(files=[(name, b"{}")]), settings)

    def test_the_same_file_twice_is_refused(self):
        with pytest.raises(Refused):
            validate_start(make(files=[("a.json", b"{}"), ("a.json", b"{}")]), settings)

    def test_file_counts_and_sizes_have_limits(self):
        small = Settings(_env_file=None, max_assets_per_bot=2, max_asset_bytes=10, max_assets_total_bytes=15)
        validate_start(make(files=[("a.txt", b"x" * 10), ("b.txt", b"x" * 5)]), small)
        with pytest.raises(Refused):  # too many
            validate_start(make(files=[(f"{n}.txt", b"x") for n in range(3)]), small)
        with pytest.raises(Refused):  # one too big
            validate_start(make(files=[("a.txt", b"x" * 11)]), small)
        with pytest.raises(Refused):  # too big together
            validate_start(make(files=[("a.txt", b"x" * 10), ("b.txt", b"x" * 6)]), small)


class TestSignatures:
    def headers(self, method="PUT", target="/v1/runs/" + BOT, body=b"{}", secret=SECRET, now=None):
        return sign_headers(secret, method, target, body, now)

    def check(self, verifier, headers, method="PUT", target="/v1/runs/" + BOT, body=b"{}", now=None):
        verifier.check(method, target, body, headers, now)

    def test_a_signed_call_is_accepted(self):
        self.check(Verifier(SECRET), self.headers())

    def test_a_call_with_nothing_to_sign_is_accepted_too(self):
        self.check(Verifier(SECRET), self.headers("GET", "/v1/runs/x?after=3", b""), "GET", "/v1/runs/x?after=3", b"")

    @pytest.mark.parametrize(
        "change",
        [
            {"method": "DELETE"},
            {"target": "/v1/runs/" + "c" * 32},
            {"target": "/v1/runs/" + BOT + "?replace=0"},
            {"body": b'{"x":1}'},
        ],
    )
    def test_changing_anything_that_was_signed_breaks_it(self, change):
        headers = self.headers()
        with pytest.raises(NotAllowed):
            self.check(Verifier(SECRET), headers, **change)

    def test_another_secret_does_not_work(self):
        with pytest.raises(NotAllowed):
            self.check(Verifier(SECRET), self.headers(secret="q" * 40))

    @pytest.mark.parametrize("missing", [TIMESTAMP_HEADER, NONCE_HEADER, SIGNATURE_HEADER])
    def test_every_header_is_needed(self, missing):
        headers = self.headers()
        del headers[missing]
        with pytest.raises(NotAllowed):
            self.check(Verifier(SECRET), headers)

    @pytest.mark.parametrize(
        ("name", "value"),
        [(TIMESTAMP_HEADER, "yesterday"), (TIMESTAMP_HEADER, "-5"), (NONCE_HEADER, "x"), (SIGNATURE_HEADER, "abc")],
    )
    def test_headers_that_are_not_what_they_should_be_are_refused(self, name, value):
        headers = self.headers()
        headers[name] = value
        with pytest.raises(NotAllowed):
            self.check(Verifier(SECRET), headers)

    def test_an_old_call_is_refused_and_so_is_one_from_the_future(self):
        now = 1_800_000_000
        verifier = Verifier(SECRET)
        self.check(verifier, self.headers(now=now - 59), now=now)
        with pytest.raises(NotAllowed):
            self.check(verifier, self.headers(now=now - 61), now=now)
        with pytest.raises(NotAllowed):
            self.check(verifier, self.headers(now=now + 61), now=now)

    def test_a_copy_of_a_call_is_refused(self):
        verifier = Verifier(SECRET)
        headers = self.headers()
        self.check(verifier, headers)
        with pytest.raises(NotAllowed):
            self.check(verifier, headers)

    def test_two_identical_calls_made_one_after_the_other_both_work(self):
        verifier = Verifier(SECRET)
        for _ in range(3):
            self.check(verifier, self.headers("GET", "/v1/runs/x?after=1", b""), "GET", "/v1/runs/x?after=1", b"")

    def test_remembered_calls_are_forgotten_in_time(self):
        verifier = Verifier(SECRET)
        now = 1_800_000_000
        for _ in range(5001):
            self.check(verifier, self.headers(now=now), now=now)
        assert len(verifier._seen) == 5001  # noqa: SLF001 - none of them is old enough to forget yet
        later = now + 1000
        self.check(verifier, self.headers(now=later), now=later)
        assert len(verifier._seen) == 1  # noqa: SLF001 - the old ones are gone, so the list can't grow without end

    def test_the_signature_covers_the_body_by_its_hash(self):
        a = signature(SECRET, "PUT", "/x", b"one", "1", "n" * 16)
        b = signature(SECRET, "PUT", "/x", b"two", "1", "n" * 16)
        assert a != b and len(a) == 64 and all(c in "0123456789abcdef" for c in a)

    def test_each_call_gets_its_own_nonce(self):
        assert self.headers()[NONCE_HEADER] != self.headers()[NONCE_HEADER]


def test_every_error_has_its_own_kind_and_a_status():
    kinds = {cls.kind for cls in protocol.ERRORS.values()}
    assert kinds == set(protocol.ERRORS)
    assert protocol.Refused.status == 422 and protocol.TooMany.status == 409
    assert protocol.Busy.status == 503 and protocol.Unavailable.status == 503 and protocol.NotAllowed.status == 401
