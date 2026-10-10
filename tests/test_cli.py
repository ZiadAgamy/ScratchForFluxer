"""The command line: ``check`` (the redirect address for Fluxer and a look over the settings) and ``reencrypt``
(moving saved bot tokens to a new SECRET_KEY). Neither may ever print a secret."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fluxer_mock import TOKEN, TOKEN_2
from sqlalchemy import select, update

from scratch_fluxer import cli, config, db
from scratch_fluxer.config import Settings
from scratch_fluxer.db import _call_with_session
from scratch_fluxer.models import Bot
from scratch_fluxer.security import TokenVault

ROOT = Path(__file__).resolve().parent.parent
LONG_AGO = datetime(2020, 1, 1, tzinfo=timezone.utc)

NEWEST = "the-newest-secret-key-0123456789-abcdefghijklmnopqrst"
OLDER = "an-older-secret-key-0123456789-abcdefghijklmnopqrstuvwx"
LOST = "a-key-that-nobody-gave-0123456789-abcdefghijklmnopqrstuv"


def settings_for(**values) -> Settings:
    return Settings(_env_file=None, **values)


def production(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "secret_key": "s" * 40,
        "app_base_url": "https://scratch.example",
        "fluxer_client_id": "123456",
        "fluxer_client_secret": "shh",
        "database_url": "postgresql://u:p@db.example/app",
        "runner_url": "https://runner.example",
        "runner_secret": "r" * 40,
        "trust_proxy": True,
        "contact_url": "mailto:hello@example.com",
    }
    values.update(overrides)
    return settings_for(**values)


# ================================================================================================= check
class TestRedirectAddresses:
    def test_the_live_site_and_your_own_computer(self):
        found = cli.redirect_addresses(production())
        assert [a.uri for a in found] == [
            "https://scratch.example/auth/callback",
            "http://localhost:8000/auth/callback",
        ]
        assert "live site" in found[0].when and "own computer" in found[1].when

    def test_a_local_site_lists_its_address_once(self):
        found = cli.redirect_addresses(settings_for(app_base_url="http://localhost:8000"))
        assert [a.uri for a in found] == ["http://localhost:8000/auth/callback"]

    def test_without_an_address_only_the_local_one_is_known(self, monkeypatch):
        monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL", raising=False)
        found = cli.redirect_addresses(settings_for(app_base_url=None))
        assert [a.uri for a in found] == ["http://localhost:8000/auth/callback"]

    def test_vercels_own_address_is_used_when_there_is_no_app_base_url(self, monkeypatch):
        monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "scratch-for-fluxer.vercel.app")
        found = cli.redirect_addresses(settings_for(app_base_url=None))
        assert found[0].uri == "https://scratch-for-fluxer.vercel.app/auth/callback"

    @pytest.mark.parametrize("base", ["https://scratch.example", "https://a.b.c.example", "http://localhost:8000"])
    def test_it_is_exactly_the_address_the_login_sends_to_fluxer(self, base):
        settings = settings_for(app_base_url=base)
        assert cli.redirect_addresses(settings)[0].uri == settings.oauth_redirect_uri()


class TestReport:
    def test_a_complete_setup_passes(self):
        text, code = cli.report(production())
        assert code == 0 and "Everything needed is in place." in text
        assert "https://scratch.example/auth/callback" in text
        assert "[fix]" not in text

    def test_it_tells_people_to_copy_the_address_exactly(self):
        text, _ = cli.report(production())
        assert "exactly" in text and "no slash at the end" in text

    def test_missing_pieces_are_marked_to_fix(self, monkeypatch):
        monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL", raising=False)
        text, code = cli.report(production(fluxer_client_id=None, fluxer_client_secret=None, app_base_url=None))
        assert code == 1
        for name in ("FLUXER_CLIENT_ID", "FLUXER_CLIENT_SECRET", "APP_BASE_URL"):
            assert any(line.strip().startswith("[fix]") and name in line for line in text.splitlines()), name
        assert "thing(s) to fix" in text

    def test_a_short_key_and_a_path_in_the_address_are_both_called_out(self):
        text, code = cli.report(production(secret_key="short", app_base_url="https://scratch.example/app"))
        assert code == 1
        assert "SECRET_KEY is shorter than 32" in text and "nothing after the host name" in text

    def test_plain_http_on_a_real_site_is_worth_a_note_not_a_failure(self):
        text, code = cli.report(production(app_base_url="http://scratch.example"))
        assert code == 0
        assert any(line.strip().startswith("[note]") and "https://" in line for line in text.splitlines())

    def test_a_developer_without_login_settings_is_told_login_cannot_work(self):
        bare = settings_for(
            app_env="development",
            fluxer_client_id=None,
            fluxer_client_secret=None,
            app_base_url=None,
            secret_key=None,
        )
        text, code = cli.report(bare)
        assert code == 1 and "Logging in with Fluxer can't work" in text
        assert "APP_BASE_URL is not set" in text and "data/.dev-secret" in text

    def test_bots_on_the_same_computer_as_a_real_site_are_worth_a_note(self):
        text, _ = cli.report(production(runner_url=None, runner_secret=None, runner_mode="local"))
        assert "share its user and files" in text

    def test_the_proxy_note_only_shows_when_it_may_matter(self):
        assert "TRUST_PROXY" not in cli.report(production(trust_proxy=True))[0]
        assert "TRUST_PROXY is off" in cli.report(production(trust_proxy=False))[0]
        assert "TRUST_PROXY" not in cli.report(settings_for(app_env="development"))[0]

    def test_the_community_link_is_reported(self):
        assert "Community link: https://fluxer.gg/oHy1OzcO" in cli.report(production())[0]
        assert "COMMUNITY_URL is empty" in cli.report(production(community_url=""))[0]

    def test_every_line_is_plain_ascii(self):
        """A terminal on any system must be able to show it."""
        for settings in (production(), settings_for(app_env="development"), production(secret_key="x")):
            cli.report(settings)[0].encode("ascii")

    def test_it_never_prints_a_secret(self):
        markers = {
            "SECRET_KEY": "secret-key-marker-0123456789-abcdefghijklmnopq",
            "SECRET_KEY_PREVIOUS": "previous-key-marker-0123456789-abcdefghijklmn",
            "FLUXER_CLIENT_SECRET": "client-secret-marker-zzz",
            "FLUXER_CLIENT_ID": "client-id-marker-555",
            "DATABASE_URL": "db-password-marker-qqq",
            "RUNNER_SECRET": "runner-secret-marker-0123456789-abcdefghijk",
        }
        settings = production(
            secret_key=markers["SECRET_KEY"],
            secret_key_previous=markers["SECRET_KEY_PREVIOUS"],
            fluxer_client_secret=markers["FLUXER_CLIENT_SECRET"],
            fluxer_client_id=markers["FLUXER_CLIENT_ID"],
            database_url=f"postgresql://user:{markers['DATABASE_URL']}@db.example/app",
            runner_secret=markers["RUNNER_SECRET"],
        )
        text, _ = cli.report(settings)
        for name, marker in markers.items():
            assert marker not in text, name

    def test_nothing_is_printed_for_a_short_secret_either(self):
        text, _ = cli.report(production(secret_key="marker-short", fluxer_client_secret="marker-client"))
        assert "marker-short" not in text and "marker-client" not in text

    def test_it_does_not_create_the_development_key(self, tmp_path, monkeypatch):
        """Looking at the settings must not change anything on disk (a missing SECRET_KEY would create a key file)."""
        monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
        cli.report(settings_for(app_env="development", secret_key=None))
        assert not (tmp_path / "data").exists()


class TestCheckCommand:
    def test_main_prints_the_report_and_returns_its_exit_code(self, capsys):
        assert cli.main(["check"]) in (0, 1)
        out = capsys.readouterr().out
        assert "setup check" in out and "/auth/callback" in out

    def test_the_real_command_runs_from_the_project_folder(self):
        env = {
            **os.environ,
            "APP_ENV": "development",
            "SECRET_KEY": "",
            "APP_BASE_URL": "https://scratch.example",
            "FLUXER_CLIENT_ID": "id-for-the-test",
            "FLUXER_CLIENT_SECRET": "secret-marker-qqq",
            "DATABASE_URL": "postgresql://user:password-marker-www@db.example/app",
        }
        done = subprocess.run(  # noqa: S603 - our own interpreter and module
            [sys.executable, "-m", "scratch_fluxer", "check"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert done.returncode == 0, done.stderr
        assert "https://scratch.example/auth/callback" in done.stdout
        everything = done.stdout + done.stderr
        assert "secret-marker-qqq" not in everything and "password-marker-www" not in everything

    def test_no_command_starts_the_site_and_serve_is_the_same(self, monkeypatch):
        started = []
        monkeypatch.setattr(cli, "run_serve", lambda: started.append("site") or 0)
        assert cli.main([]) == 0 and cli.main(["serve"]) == 0
        assert started == ["site", "site"]

    def test_an_unknown_command_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit) as stop:
            cli.main(["explode"])
        assert stop.value.code == 2

    def test_help_names_every_command(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["--help"])
        out = capsys.readouterr().out
        for command in ("serve", "check", "reencrypt"):
            assert command in out


# ================================================================================================= reencrypt
def put_bots(user_id: str, *ciphertexts: str, updated_at: datetime = LONG_AGO) -> list[str]:
    """One bot row per ciphertext (the tokens inside are not looked at here). Returns their ids."""

    def _insert(session):
        rows = [
            Bot(
                user_id=user_id,
                name=f"bot {n}",
                token_ciphertext=ciphertext,
                token_last4="1234",
                bot_user_id=f"{n:018d}",
                updated_at=updated_at,
            )
            for n, ciphertext in enumerate(ciphertexts, start=1)
        ]
        session.add_all(rows)
        session.flush()
        return [row.id for row in rows]

    return _call_with_session(_insert, (), {})


def stored(bot_id: str) -> Bot:
    return _call_with_session(lambda session: session.execute(select(Bot).where(Bot.id == bot_id)).scalar_one(), (), {})


def vault() -> TokenVault:
    return TokenVault(NEWEST, [OLDER])


@pytest.fixture
def newest_key(monkeypatch):
    """The site is configured with NEWEST as SECRET_KEY and OLDER as SECRET_KEY_PREVIOUS."""
    monkeypatch.setenv("SECRET_KEY", NEWEST)
    monkeypatch.setenv("SECRET_KEY_PREVIOUS", OLDER)
    config.reset_settings_cache()
    return config.get_settings()


class TestReencryptTokens:
    def test_only_tokens_under_an_older_key_are_moved(self, make_user):
        user_id, _, _ = make_user()
        older, current, lost = (
            TokenVault(OLDER).encrypt(TOKEN),
            TokenVault(NEWEST).encrypt(TOKEN_2),
            TokenVault(LOST).encrypt(TOKEN),
        )
        ids = put_bots(user_id, older, current, lost)

        tally = _call_with_session(lambda session: cli.reencrypt_tokens(session, vault()), (), {})

        assert (tally.moved, tally.current, tally.unreadable, tally.replaced, tally.total) == (1, 1, 1, 0, 3)
        assert TokenVault(NEWEST).decrypt(stored(ids[0]).token_ciphertext) == TOKEN  # the newest key alone opens it
        assert stored(ids[1]).token_ciphertext == current  # already new: not rewritten
        assert stored(ids[2]).token_ciphertext == lost  # nobody can read it: left exactly as it was

    def test_saving_a_token_again_is_not_editing_the_bot(self, make_user):
        user_id, _, _ = make_user()
        (bot_id,) = put_bots(user_id, TokenVault(OLDER).encrypt(TOKEN))
        _call_with_session(lambda session: cli.reencrypt_tokens(session, vault()), (), {})
        assert stored(bot_id).updated_at.replace(tzinfo=None) == LONG_AGO.replace(tzinfo=None)

    def test_running_it_again_changes_nothing(self, make_user):
        user_id, _, _ = make_user()
        ids = put_bots(user_id, TokenVault(OLDER).encrypt(TOKEN), TokenVault(NEWEST).encrypt(TOKEN_2))
        _call_with_session(lambda session: cli.reencrypt_tokens(session, vault()), (), {})
        before = [stored(i).token_ciphertext for i in ids]
        tally = _call_with_session(lambda session: cli.reencrypt_tokens(session, vault()), (), {})
        assert (tally.moved, tally.current) == (0, 2)
        assert [stored(i).token_ciphertext for i in ids] == before

    def test_a_dry_run_counts_but_changes_nothing(self, make_user):
        user_id, _, _ = make_user()
        older = TokenVault(OLDER).encrypt(TOKEN)
        (bot_id,) = put_bots(user_id, older)
        tally = _call_with_session(lambda session: cli.reencrypt_tokens(session, vault(), dry_run=True), (), {})
        assert tally.moved == 1
        assert stored(bot_id).token_ciphertext == older

    def test_every_bot_is_visited_whatever_the_page_size(self, make_user):
        user_id, _, _ = make_user()
        older = [TokenVault(OLDER).encrypt(f"{n}.{'A' * 30}") for n in range(1, 8)]
        ids = put_bots(user_id, *older)
        tally = _call_with_session(lambda session: cli.reencrypt_tokens(session, vault(), page=2), (), {})
        assert tally.moved == 7
        assert all(TokenVault(NEWEST).decrypt(stored(i).token_ciphertext) for i in ids)

    def test_an_empty_database_is_fine(self):
        tally = _call_with_session(lambda session: cli.reencrypt_tokens(session, vault()), (), {})
        assert tally.total == 0

    def test_a_token_replaced_by_its_owner_meanwhile_is_not_overwritten(self, make_user):
        user_id, _, _ = make_user()
        (bot_id,) = put_bots(user_id, TokenVault(OLDER).encrypt(TOKEN))
        replacement = TokenVault(NEWEST).encrypt(TOKEN_2)

        class Racing(TokenVault):
            """Between reading a token and saving it again, the owner pastes a new one."""

            hook = None

            def refreshed(self, ciphertext):
                newer = super().refreshed(ciphertext)
                if newer is not None and self.hook is not None:
                    self.hook()
                    self.hook = None
                return newer

        def work(session):
            racing = Racing(NEWEST, [OLDER])
            racing.hook = lambda: session.execute(
                update(Bot).where(Bot.id == bot_id).values(token_ciphertext=replacement)
            )
            return cli.reencrypt_tokens(session, racing)

        tally = _call_with_session(work, (), {})
        assert (tally.moved, tally.replaced) == (0, 1)
        assert stored(bot_id).token_ciphertext == replacement


class TestReencryptCommand:
    def run(self, settings, **options):
        said: list[str] = []
        code = cli.run_reencrypt(settings, say=said.append, **options)
        return code, "\n".join(said)

    def test_everything_moved_means_the_old_key_can_go(self, make_user, newest_key):
        user_id, _, _ = make_user()
        ids = put_bots(user_id, TokenVault(OLDER).encrypt(TOKEN), TokenVault(NEWEST).encrypt(TOKEN_2))
        code, said = self.run(newest_key)
        assert code == 0
        assert "Saved bot tokens: 2" in said and "moved to the newest key: 1" in said
        assert "SECRET_KEY_PREVIOUS can be removed" in said
        assert TokenVault(NEWEST).decrypt(stored(ids[0]).token_ciphertext) == TOKEN

    def test_tokens_nobody_can_read_keep_the_old_keys_needed(self, make_user, newest_key):
        user_id, _, _ = make_user()
        lost = TokenVault(LOST).encrypt(TOKEN)
        (bot_id,) = put_bots(user_id, lost)
        code, said = self.run(newest_key)
        assert code == 1 and "not readable with these keys: 1" in said
        assert "SECRET_KEY_PREVIOUS" in said and "can be removed" not in said
        assert stored(bot_id).token_ciphertext == lost

    def test_a_dry_run_says_so_and_changes_nothing(self, make_user, newest_key):
        user_id, _, _ = make_user()
        older = TokenVault(OLDER).encrypt(TOKEN)
        (bot_id,) = put_bots(user_id, older)
        code, said = self.run(newest_key, dry_run=True)
        assert code == 0 and "would be moved to the newest key: 1" in said and "Nothing was changed" in said
        assert stored(bot_id).token_ciphertext == older

    def test_it_never_prints_a_token_a_key_or_a_ciphertext(self, make_user, newest_key):
        user_id, _, _ = make_user()
        older = TokenVault(OLDER).encrypt(TOKEN)
        ids = put_bots(user_id, older, TokenVault(LOST).encrypt(TOKEN_2))
        _, said = self.run(newest_key)
        later = stored(ids[0]).token_ciphertext
        for secret in (TOKEN, TOKEN_2, NEWEST, OLDER, LOST, older, later, TOKEN.split(".")[1]):
            assert secret not in said

    def test_without_a_secret_key_in_production_it_stops_before_touching_anything(self):
        settings = settings_for(app_env="production", secret_key=None)
        code, said = self.run(settings)
        assert code == 2 and "SECRET_KEY is not set" in said

    def test_a_database_that_cannot_be_opened_is_reported_without_its_address(self, monkeypatch, tmp_path):
        missing = tmp_path / "no-such-folder" / "marker-folder" / "sff.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{missing}")
        db.reset_engine()
        config.reset_settings_cache()
        code, said = self.run(config.get_settings())
        assert code == 2 and "Could not use the database" in said
        assert "marker-folder" not in said and str(tmp_path) not in said

    def test_it_says_which_kind_of_database_it_used_and_how_many_keys(self, newest_key):
        _, said = self.run(newest_key)
        assert "Database: a SQLite file" in said and "1 older one(s)" in said
