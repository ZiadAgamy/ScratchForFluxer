"""Configuration: what is required in production, URL handling, runner mode, dev secret."""

from __future__ import annotations

import pytest

from scratch_fluxer import config
from scratch_fluxer.config import Settings, normalize_database_url

LONG_SECRET = "s" * 40


def prod(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "secret_key": LONG_SECRET,
        "app_base_url": "https://scratch.example",
        "fluxer_client_id": "123",
        "fluxer_client_secret": "shh",
        "database_url": "postgresql://u:p@db.example/app",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class TestProblems:
    def test_development_needs_nothing(self):
        assert Settings(_env_file=None, app_env="development", secret_key=None, fluxer_client_id=None).problems() == []

    def test_complete_production_config_is_fine(self):
        assert prod().fatal_problems() == []

    @pytest.mark.parametrize(
        "overrides,key",
        [
            ({"secret_key": None}, "SECRET_KEY"),
            ({"secret_key": "too-short"}, "SECRET_KEY"),
            ({"fluxer_client_id": None}, "FLUXER_CLIENT_ID"),
            ({"fluxer_client_secret": None}, "FLUXER_CLIENT_SECRET"),
            ({"app_base_url": None}, "APP_BASE_URL"),
        ],
    )
    def test_each_missing_piece_is_reported(self, overrides, key, monkeypatch):
        monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL", raising=False)
        assert key in {p.key for p in prod(**overrides).fatal_problems()}

    def test_sqlite_on_vercel_is_not_allowed(self, monkeypatch):
        monkeypatch.setattr(config, "ON_VERCEL", True)
        keys = {p.key for p in prod(database_url=None).fatal_problems()}
        assert "DATABASE_URL" in keys

    def test_vercel_production_url_counts_as_base_url(self, monkeypatch):
        monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "my-app.vercel.app")
        settings = prod(app_base_url=None)
        assert settings.public_base_url == "https://my-app.vercel.app"
        assert "APP_BASE_URL" not in {p.key for p in settings.fatal_problems()}

    def test_remote_runner_needs_url_and_secret(self):
        problems = prod(runner_mode="remote").problems()
        assert any(p.key == "RUNNER_URL" and not p.fatal for p in problems)


class TestUrls:
    def test_trailing_slash_is_removed(self):
        assert Settings(_env_file=None, app_base_url="https://x.example/").public_base_url == "https://x.example"

    def test_blank_values_become_none(self):
        s = Settings(_env_file=None, app_base_url="  ", secret_key="", fluxer_client_id=" ")
        assert s.app_base_url is None and s.secret_key is None and s.fluxer_client_id is None

    def test_redirect_uri(self):
        assert (
            Settings(_env_file=None, app_base_url="https://x.example").oauth_redirect_uri()
            == "https://x.example/auth/callback"
        )

    def test_redirect_uri_from_request_in_development(self, monkeypatch):
        monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL", raising=False)
        s = Settings(_env_file=None, app_base_url=None)
        assert s.oauth_redirect_uri("http://localhost:8000/") == "http://localhost:8000/auth/callback"

    def test_redirect_uri_without_any_base_is_an_error(self, monkeypatch):
        monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL", raising=False)
        with pytest.raises(RuntimeError):
            Settings(_env_file=None, app_base_url=None).oauth_redirect_uri()

    def test_cookie_secure_follows_https(self, monkeypatch):
        monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL", raising=False)
        assert Settings(_env_file=None, app_base_url="https://x.example").cookie_secure
        assert not Settings(_env_file=None, app_base_url="http://localhost:8000").cookie_secure
        assert prod().cookie_secure

    @pytest.mark.parametrize(
        "given,expected",
        [
            ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
            ("postgresql://u:p@h/db?sslmode=require", "postgresql+psycopg://u:p@h/db?sslmode=require"),
            ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
            ("sqlite:///data/x.db", "sqlite:///data/x.db"),
        ],
    )
    def test_database_urls(self, given, expected):
        assert normalize_database_url(given) == expected

    def test_default_database_is_sqlite(self):
        assert Settings(_env_file=None, database_url=None, postgres_url=None).effective_database_url.startswith(
            "sqlite:///"
        )

    def test_vercel_postgres_url_is_picked_up(self):
        s = Settings(_env_file=None, database_url=None, postgres_url="postgres://u:p@h/db")
        assert s.effective_database_url == "postgresql+psycopg://u:p@h/db"


class TestRunnerMode:
    def test_auto_is_local_off_vercel(self, monkeypatch):
        monkeypatch.setattr(config, "ON_VERCEL", False)
        assert Settings(_env_file=None, runner_mode="auto").runner_effective_mode == "local"

    def test_auto_is_off_on_vercel_without_runner(self, monkeypatch):
        monkeypatch.setattr(config, "ON_VERCEL", True)
        assert Settings(_env_file=None, runner_mode="auto").runner_effective_mode == "off"

    def test_auto_uses_remote_runner_when_configured(self, monkeypatch):
        monkeypatch.setattr(config, "ON_VERCEL", True)
        s = Settings(
            _env_file=None, runner_mode="auto", runner_url="https://runner.example/", runner_secret=LONG_SECRET
        )
        assert s.runner_effective_mode == "remote"
        assert s.runner_url == "https://runner.example"
        assert s.runner_setup_problem() is None

    def test_remote_without_secret_falls_back_to_off(self):
        s = Settings(_env_file=None, runner_mode="remote", runner_url="https://r.example")
        assert s.runner_effective_mode == "off"
        assert "RUNNER_SECRET" in (s.runner_setup_problem() or "")

    def test_a_short_secret_is_not_good_enough(self):
        s = Settings(_env_file=None, runner_mode="remote", runner_url="https://r.example", runner_secret="short")
        assert s.runner_effective_mode == "off"
        assert "32" in (s.runner_setup_problem() or "")

    def test_bot_tokens_never_travel_over_plain_http(self):
        s = Settings(
            _env_file=None, runner_mode="remote", runner_url="http://runner.example", runner_secret=LONG_SECRET
        )
        assert s.runner_effective_mode == "off"
        assert "https" in (s.runner_setup_problem() or "")

    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
    def test_plain_http_is_fine_on_the_same_machine(self, host):
        s = Settings(_env_file=None, runner_mode="remote", runner_url=f"http://{host}:8080", runner_secret=LONG_SECRET)
        assert s.runner_effective_mode == "remote"

    @pytest.mark.parametrize("url", ["ftp://runner.example", "runner.example", "https://"])
    def test_an_address_that_is_not_a_web_address_is_refused(self, url):
        s = Settings(_env_file=None, runner_mode="remote", runner_url=url, runner_secret=LONG_SECRET)
        assert s.runner_effective_mode == "off"
        assert s.runner_setup_problem()

    def test_the_built_in_runner_is_never_used_on_vercel(self, monkeypatch):
        monkeypatch.setattr(config, "ON_VERCEL", True)
        s = prod(runner_mode="local")
        assert s.runner_effective_mode == "off"
        assert any(p.key == "RUNNER_MODE" and not p.fatal for p in s.problems())

    def test_a_blank_user_id_base_means_none(self):
        assert Settings(_env_file=None, runner_uid_base="  ").runner_uid_base is None
        assert Settings(_env_file=None, runner_uid_base="20000").runner_uid_base == 20000

    @pytest.mark.parametrize("number", ["0", "1", "999", "-5", "4000000001"])
    def test_a_user_id_base_that_belongs_to_the_system_is_refused(self, number):
        with pytest.raises(ValueError, match="RUNNER_UID_BASE"):
            Settings(_env_file=None, runner_uid_base=number)

    @pytest.mark.parametrize("number", ["1000", "41000", "4000000000"])
    def test_a_user_id_base_from_the_allowed_range_is_taken(self, number):
        assert Settings(_env_file=None, runner_uid_base=number).runner_uid_base == int(number)


class TestSecrets:
    def test_production_refuses_to_invent_a_secret(self):
        with pytest.raises(RuntimeError):
            _ = prod(secret_key=None).effective_secret

    def test_development_secret_is_generated_once_and_reused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        config.reset_settings_cache()
        s = Settings(_env_file=None, app_env="development", secret_key=None)
        first = s.effective_secret
        assert len(first) >= config.MIN_SECRET_LENGTH
        assert (tmp_path / ".dev-secret").read_text().strip() == first
        config._dev_secret_cache = None  # simulate a restart
        assert Settings(_env_file=None, app_env="development", secret_key=None).effective_secret == first

    def test_previous_secrets_are_split(self):
        assert Settings(_env_file=None, secret_key_previous=" a ,b,, c ").previous_secrets == ["a", "b", "c"]


class TestWebLinks:
    @pytest.mark.parametrize(
        "value",
        ["https://fluxer.gg/oHy1OzcO", "HTTPS://FLUXER.GG/x", "https://example.com/a?b=c#d", "https://[::1]:8000/x"],
    )
    def test_https_links_are_kept(self, value):
        assert config.web_link(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "http://example.com",
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "data:text/html,x",
            "//example.com",
            "example.com",
            "mailto:me@example.com",
            "https://",
            "https:///path",
            "https://exa mple.com",
            "https://example.com/\nx",
            "https://example.com/\tx",
            'https://example.com/"x',
            "https://example.com/<x>",
            "https://example.com\\@evil.example",
            "https://[::1",  # not a valid address
        ],
    )
    def test_anything_else_is_dropped(self, value):
        assert config.web_link(value) is None

    def test_contact_links_may_also_be_http_or_mail(self):
        assert config.web_link("http://localhost:8000/c", allow_http_and_mail=True) == "http://localhost:8000/c"
        assert config.web_link("mailto:me@example.com", allow_http_and_mail=True) == "mailto:me@example.com"
        assert config.web_link("mailto:", allow_http_and_mail=True) is None
        assert config.web_link("javascript:alert(1)", allow_http_and_mail=True) is None
        assert config.web_link("mailto:a b@example.com", allow_http_and_mail=True) is None


class TestCommunityUrl:
    def test_it_defaults_to_the_projects_server(self):
        assert Settings(_env_file=None).community_url == "https://fluxer.gg/oHy1OzcO"
        assert config.DEFAULT_COMMUNITY_URL == "https://fluxer.gg/oHy1OzcO"

    def test_empty_hides_it(self):
        assert Settings(_env_file=None, community_url="").community_url is None
        assert Settings(_env_file=None, community_url="   ").community_url is None

    def test_only_https_is_kept(self):
        assert (
            Settings(_env_file=None, community_url="https://fluxer.gg/other").community_url == "https://fluxer.gg/other"
        )
        assert Settings(_env_file=None, community_url="http://fluxer.gg/other").community_url is None
        assert Settings(_env_file=None, community_url="javascript:alert(1)").community_url is None

    def test_it_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("COMMUNITY_URL", "https://fluxer.gg/abc")
        config.reset_settings_cache()
        assert config.get_settings().community_url == "https://fluxer.gg/abc"
        monkeypatch.setenv("COMMUNITY_URL", "")
        config.reset_settings_cache()
        assert config.get_settings().community_url is None


class TestSiteAddress:
    @pytest.mark.parametrize("base", ["https://scratch.example", "http://localhost:8000", "http://127.0.0.1:8000"])
    def test_good_addresses_have_no_problem(self, base):
        assert prod(app_base_url=base).base_url_problem() is None

    def test_no_address_is_not_a_shape_problem(self, monkeypatch):
        monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL", raising=False)
        assert prod(app_base_url=None).base_url_problem() is None  # a missing address is reported on its own

    @pytest.mark.parametrize(
        "base",
        [
            "https://scratch.example/app",  # the callback would not be where the site expects it
            "https://scratch.example?x=1",
            "https://scratch.example#top",
            "ftp://scratch.example",
            "scratch.example",
            "https://",
            "https://[::1",
        ],
    )
    def test_a_wrong_shape_is_fatal_in_production(self, base):
        settings = prod(app_base_url=base)
        problem = settings.base_url_problem()
        assert problem is not None and problem.key == "APP_BASE_URL" and problem.fatal
        assert problem in settings.fatal_problems()

    def test_plain_http_on_a_real_site_is_a_warning_not_a_stop(self):
        settings = prod(app_base_url="http://scratch.example")
        problem = settings.base_url_problem()
        assert problem is not None and not problem.fatal and "https://" in problem.message
        assert settings.fatal_problems() == [] and problem in settings.problems()

    def test_plain_http_is_fine_in_development(self):
        settings = Settings(_env_file=None, app_env="development", app_base_url="http://scratch.example")
        assert settings.problems() == [] and settings.warnings() == []

    def test_the_callback_address_is_the_site_plus_one_path(self):
        assert prod().oauth_redirect_uri() == "https://scratch.example/auth/callback"
        assert config.CALLBACK_PATH == "/auth/callback"


class TestWarnings:
    def dev(self, **values) -> Settings:
        return Settings(_env_file=None, app_env="development", **values)

    def test_nothing_to_say_about_a_plain_local_setup(self):
        assert self.dev().warnings() == []
        assert self.dev(secret_key="x" * 40, app_base_url="http://localhost:8000").warnings() == []

    def test_a_short_key_is_called_out(self):
        (warning,) = self.dev(secret_key="marker-value").warnings()
        assert warning.key == "SECRET_KEY" and not warning.fatal and "marker-value" not in warning.message

    def test_a_public_https_address_without_production_is_called_out(self):
        (warning,) = self.dev(app_base_url="https://scratch.example").warnings()
        assert warning.key == "APP_ENV" and "APP_ENV=production" in warning.message

    def test_production_and_tests_have_their_own_rules(self):
        assert prod().warnings() == []
        assert Settings(_env_file=None, app_env="test", secret_key="short").warnings() == []
