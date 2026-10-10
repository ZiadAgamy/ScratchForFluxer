"""Public pages, error pages, static files, headers and the configuration guard."""

from __future__ import annotations

import logging
import re

import pytest
from fastapi.testclient import TestClient

from scratch_fluxer import config, deps


def csrf_meta(html: str) -> str | None:
    match = re.search(r'<meta name="csrf-token" content="([^"]*)"', html)
    return match.group(1) if match else None


class TestLanding:
    def test_anonymous_visitor_is_invited_to_log_in(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Log in with Fluxer" in r.text
        assert 'href="/auth/login"' in r.text
        assert csrf_meta(r.text) is None  # no token for anonymous visitors
        assert 'href="/bots"' not in r.text  # nothing that needs a login is offered

    def test_logged_in_user_sees_their_name_and_a_logout_form(self, client, make_user):
        _, csrf, cookies = make_user(username="agamy", global_name="AGAMY")
        client.cookies.update(cookies)
        r = client.get("/")
        assert r.status_code == 200
        assert "My bots" in r.text
        assert "AGAMY" in r.text and "@agamy" in r.text
        assert csrf_meta(r.text) == csrf
        assert 'action="/auth/logout"' in r.text
        assert f'name="csrf_token" value="{csrf}"' in r.text

    def test_user_names_are_escaped(self, client, make_user):
        _, _, cookies = make_user(username="x", global_name="<img src=x onerror=alert(1)>")
        client.cookies.update(cookies)
        r = client.get("/")
        assert "<img src=x onerror" not in r.text
        assert "&lt;img src=x onerror=alert(1)&gt;" in r.text

    def test_page_does_not_expose_secrets(self, client, settings):
        body = client.get("/").text + client.get("/login").text
        assert settings.fluxer_client_secret not in body
        assert settings.secret_key not in body


class TestLoginPage:
    def test_shows_login_button_with_safe_default_next(self, client):
        r = client.get("/login")
        assert r.status_code == 200
        assert 'href="/auth/login?next=/bots"' in r.text

    def test_keeps_a_local_next(self, client):
        r = client.get("/login", params={"next": "/bots/new"})
        assert 'href="/auth/login?next=/bots/new"' in r.text

    @pytest.mark.parametrize("evil", ["//evil.example", "https://evil.example", "/\\evil.example", "/auth/login"])
    def test_never_forwards_an_unsafe_next(self, client, evil):
        r = client.get("/login", params={"next": evil})
        assert "evil.example" not in r.text
        assert 'href="/auth/login?next=/bots"' in r.text

    @pytest.mark.parametrize("words", ["<script>alert(1)</script>", "Your account is locked. Call +1 555 0100"])
    def test_never_shows_words_taken_from_the_address(self, client, words):
        """``/login?error=...`` used to print its value, which let a made-up link put any sentence on this page."""
        r = client.get("/login", params={"error": words, "message": words})
        assert r.status_code == 200
        assert words not in r.text and "&lt;script&gt;" not in r.text
        assert "alert-error" not in r.text

    def test_logged_in_users_are_sent_onward(self, client, make_user):
        _, _, cookies = make_user()
        client.cookies.update(cookies)
        r = client.get("/login", params={"next": "/bots/new"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/bots/new"
        r = client.get("/login", params={"next": "https://evil.example"}, follow_redirects=False)
        assert r.headers["location"] == "/bots"

    def test_tells_the_developer_when_login_is_not_configured(self, monkeypatch):
        monkeypatch.setenv("FLUXER_CLIENT_ID", "")
        config.reset_settings_cache()
        from scratch_fluxer.main import create_app

        with TestClient(create_app()) as c:
            r = c.get("/login")
        assert r.status_code == 200
        assert "isn't set up on this server yet" in r.text
        assert "FLUXER_CLIENT_ID" in r.text


class TestErrors:
    def test_unknown_page_is_a_friendly_404(self, client):
        r = client.get("/definitely-not-here")
        assert r.status_code == 404
        assert "text/html" in r.headers["content-type"]
        assert "Page not found" in r.text and "Back home" in r.text

    def test_unknown_api_path_is_json(self, client):
        r = client.get("/api/definitely-not-here")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")
        assert r.json() == {"detail": "Not Found"}

    def test_wrong_method_is_not_a_stack_trace(self, client):
        r = client.post("/healthz")
        assert r.status_code == 405
        assert "Traceback" not in r.text

    def test_crashes_become_a_clean_500_that_keeps_security_headers(self, app):
        async def boom():
            raise RuntimeError("secret internal detail: hunter2")

        app.add_api_route("/boom", boom)
        app.add_api_route("/api/boom", boom)
        with TestClient(app, raise_server_exceptions=False) as c:
            page = c.get("/boom")
            api = c.get("/api/boom")
        assert page.status_code == 500 and "Something went wrong" in page.text
        assert api.status_code == 500 and api.json() == {"detail": "Something went wrong on our side."}
        for r in (page, api):
            assert "hunter2" not in r.text
            assert r.headers["x-frame-options"] == "DENY"
            assert "content-security-policy" in r.headers
            assert r.headers["cache-control"] == "no-store"

    def test_error_pages_still_know_who_is_logged_in(self, client, make_user):
        _, _, cookies = make_user(global_name="Pat")
        client.cookies.update(cookies)
        r = client.get("/definitely-not-here")
        assert r.status_code == 404 and "Pat" in r.text

    def test_a_broken_database_does_not_break_error_pages(self, client, make_user, monkeypatch):
        _, _, cookies = make_user()
        client.cookies.update(cookies)

        async def broken(*_a, **_k):
            raise RuntimeError("database is down")

        monkeypatch.setattr(deps, "db_run", broken)
        r = client.get("/definitely-not-here")
        assert r.status_code == 404 and "Log in with Fluxer" in r.text


class TestHeaders:
    @pytest.mark.parametrize("path", ["/", "/login", "/healthz", "/nope", "/api/nope", "/auth/login"])
    def test_security_headers_everywhere(self, client, path):
        r = client.get(path, follow_redirects=False)
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"

    @pytest.mark.parametrize("path", ["/", "/login", "/healthz", "/api/nope"])
    def test_dynamic_pages_are_never_cached(self, client, path):
        assert client.get(path).headers["cache-control"] == "no-store"

    def test_static_files_are_not_marked_no_store(self, client):
        assert client.get("/static/css/app.css").headers.get("cache-control") != "no-store"

    def test_no_inline_scripts_so_the_csp_can_stay_strict(self, client):
        for path in ("/", "/login", "/nope"):
            html = client.get(path).text
            assert not re.search(r"<script(?![^>]*\bsrc=)", html), path
            assert not re.search(r"\son[a-z]+\s*=", html), path  # no onclick="" style handlers


class TestStatic:
    def test_assets_are_served(self, client):
        for path, kind in [
            ("/static/css/app.css", "text/css"),
            ("/static/css/fonts.css", "text/css"),
            ("/static/js/app.js", "javascript"),
            ("/static/img/logo.png", "image/png"),
            ("/static/vendor/phosphor/fill/style.css", "text/css"),
            ("/static/vendor/phosphor/fill/Phosphor-Fill.woff2", "font/woff2"),
            ("/static/vendor/blockly/blockly.min.js", "javascript"),
            ("/static/fonts/ibm-plex-sans-latin-400-normal.woff2", "font/woff2"),
            ("/favicon.ico", "image/x-icon"),
        ]:
            r = client.get(path)
            assert r.status_code == 200, path
            assert kind in r.headers["content-type"], path

    @pytest.mark.parametrize(
        "path",
        [
            "/static/../scratch_fluxer/config.py",
            "/static/%2e%2e/scratch_fluxer/config.py",
            "/static/..%2fscratch_fluxer%2fconfig.py",
            "/static/css/../../../scratch_fluxer/config.py",
            "/static//etc/passwd",
        ],
    )
    def test_cannot_read_files_outside_static(self, client, path):
        r = client.get(path)
        assert r.status_code == 404
        assert "FLUXER_CLIENT_SECRET" not in r.text and "root:" not in r.text

    def test_every_asset_referenced_by_the_base_page_exists(self, client):
        html = client.get("/").text
        refs = re.findall(r'(?:href|src)="(/static/[^"?]+)', html)
        assert refs
        for ref in refs:
            assert client.get(ref).status_code == 200, ref


class TestConfigGuard:
    def broken_app(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("SECRET_KEY", "")
        monkeypatch.setenv("FLUXER_CLIENT_ID", "")
        monkeypatch.setenv("FLUXER_CLIENT_SECRET", "")
        monkeypatch.setenv("APP_BASE_URL", "")
        config.reset_settings_cache()
        from scratch_fluxer.main import create_app

        return create_app()

    def test_misconfigured_production_shows_a_setup_page_not_a_crash(self, monkeypatch):
        with TestClient(self.broken_app(monkeypatch)) as c:
            for path in ("/", "/login", "/auth/login", "/bots"):
                r = c.get(path, follow_redirects=False)
                assert r.status_code == 503, path
                assert "Almost ready" in r.text
                for key in ("SECRET_KEY", "FLUXER_CLIENT_ID", "FLUXER_CLIENT_SECRET", "APP_BASE_URL"):
                    assert key in r.text
                assert r.headers["x-frame-options"] == "DENY"

    def test_health_check_still_answers(self, monkeypatch):
        with TestClient(self.broken_app(monkeypatch)) as c:
            assert c.get("/healthz").json()["ok"] is True

    def test_docs_are_hidden_in_production(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        config.reset_settings_cache()
        from scratch_fluxer.main import create_app

        with TestClient(create_app()) as c:
            assert c.get("/docs").status_code == 404
            assert c.get("/openapi.json").status_code == 404

    def test_hsts_is_sent_in_production(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("APP_BASE_URL", "https://scratch.example")
        config.reset_settings_cache()
        from scratch_fluxer.main import create_app

        with TestClient(create_app(), base_url="https://scratch.example") as c:
            assert "max-age=" in c.get("/healthz").headers["strict-transport-security"]


class TestStartUpNotes:
    """What the site says in its log when it starts: things to look at, and the address to give Fluxer."""

    def start(self, monkeypatch, caplog, **env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        config.reset_settings_cache()
        from scratch_fluxer.main import create_app

        with caplog.at_level(logging.INFO, logger="scratch_fluxer"), TestClient(create_app()):
            pass
        return caplog.text

    def test_it_says_which_redirect_address_fluxer_must_list(self, monkeypatch, caplog):
        log = self.start(monkeypatch, caplog, APP_BASE_URL="https://scratch.example")
        assert "redirect address in your Fluxer application: https://scratch.example/auth/callback" in log

    def test_it_stays_quiet_about_it_without_login_settings_or_an_address(self, monkeypatch, caplog):
        assert "redirect address" not in self.start(monkeypatch, caplog, FLUXER_CLIENT_ID="")
        caplog.clear()
        monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL", raising=False)
        assert "redirect address" not in self.start(monkeypatch, caplog, APP_BASE_URL="")

    def test_a_production_warning_reaches_the_log_without_stopping_the_site(self, monkeypatch, caplog):
        log = self.start(monkeypatch, caplog, APP_ENV="production", APP_BASE_URL="http://scratch.example")
        assert "APP_BASE_URL: APP_BASE_URL should start with https://" in log

    def test_a_short_key_on_a_developers_computer_is_called_out(self, monkeypatch, caplog):
        log = self.start(monkeypatch, caplog, APP_ENV="development", SECRET_KEY="marker-short-key")
        assert "SECRET_KEY: SECRET_KEY is shorter than 32 characters" in log
        assert "marker-short-key" not in log

    def test_nothing_secret_is_ever_in_the_start_up_notes(self, monkeypatch, caplog):
        log = self.start(
            monkeypatch,
            caplog,
            APP_BASE_URL="https://scratch.example",
            FLUXER_CLIENT_SECRET="client-secret-do-not-leak",
        )
        assert "client-secret-do-not-leak" not in log
        assert "test-secret-key-0123456789" not in log
