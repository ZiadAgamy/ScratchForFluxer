"""A pretend Fluxer for tests: the shapes of the real responses, and tokens that are valid in the mock."""

from __future__ import annotations

APP_ME = "https://api.fluxer.app/v1/applications/@me"
OWNER_FLUXER_ID = "900000000000001"  # the id make_user() gives the logged-in person by default

TOKEN = "111111111111111111.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1234"
TOKEN_2 = "222222222222222222.BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB5678"
TOKEN_1_ROTATED = "111111111111111111.CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC9999"


def application(app_id="111111111111111111", name="Ping Bot", owner_id=OWNER_FLUXER_ID, bot_username="pingbot"):
    """``GET /applications/@me`` as documented in Fluxer's OpenAPI schema (``ApplicationsMeResponse``)."""
    return {
        "id": app_id,
        "name": name,
        "icon": None,
        "description": None,
        "bot_public": True,
        "bot_require_code_grant": False,
        "verify_key": "x",
        "owner": {"id": owner_id, "username": "agamy", "discriminator": "0", "avatar": None, "flags": 0},
        "bot": {
            "id": app_id,
            "username": bot_username,
            "discriminator": "0",
            "avatar": "abc123",
            "bio": None,
            "flags": 0,
        },
    }
