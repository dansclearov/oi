import asyncio
import base64
import json
import time

import pytest
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials

from oi.core import codex_auth
from oi.exceptions import CodexAuthError


@pytest.fixture
def auth_path(tmp_path, monkeypatch):
    """Point the credential store at a temp file."""
    path = tmp_path / "openai.json"
    monkeypatch.setattr(codex_auth, "_auth_path", lambda: path)
    return path


def _make_jwt(claims: dict) -> str:
    payload = (
        base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    )
    return f"header.{payload}.sig"


def _creds(access_token: str = "acc") -> OpenAICodexCredentials:
    return OpenAICodexCredentials(
        access_token=access_token, refresh_token="ref", account_id="acct"
    )


class TestTokenStore:
    def test_round_trip(self, auth_path):
        codex_auth.save_credentials(_creds())
        assert codex_auth.load_credentials() == _creds()
        assert codex_auth.is_logged_in()

    def test_load_missing_returns_none(self, auth_path):
        assert codex_auth.load_credentials() is None
        assert not codex_auth.is_logged_in()

    def test_loads_file_with_extra_keys(self, auth_path):
        auth_path.write_text(
            json.dumps(
                {
                    "access_token": "acc",
                    "refresh_token": "ref",
                    "account_id": "acct",
                    "expires_at": 1.0,
                    "email": "me@example.com",
                }
            )
        )
        assert codex_auth.load_credentials() == _creds()

    def test_incomplete_file_is_logged_out(self, auth_path):
        auth_path.write_text(json.dumps({"access_token": "acc", "account_id": None}))
        assert codex_auth.load_credentials() is None
        assert not codex_auth.is_logged_in()

    def test_file_mode_is_owner_only(self, auth_path):
        codex_auth.save_credentials(_creds())
        assert (auth_path.stat().st_mode & 0o077) == 0

    def test_logout_removes_file(self, auth_path):
        codex_auth.save_credentials(_creds())
        assert codex_auth.logout() is True
        assert not auth_path.exists()
        assert codex_auth.logout() is False

    def test_source_raises_when_logged_out(self, auth_path):
        with pytest.raises(CodexAuthError):
            asyncio.run(codex_auth._TokenStore().load())

    def test_source_persists_refreshed_tokens(self, auth_path):
        codex_auth.save_credentials(_creds("old"))
        asyncio.run(codex_auth._TokenStore().save(_creds("new")))
        assert asyncio.run(codex_auth._TokenStore().load()) == _creds("new")


class TestAccountEmail:
    def test_reads_profile_claim(self):
        token = _make_jwt({"https://api.openai.com/profile": {"email": "x@y.z"}})
        assert codex_auth.account_email(_creds(token)) == "x@y.z"

    def test_none_without_claim(self):
        assert codex_auth.account_email(_creds(_make_jwt({}))) is None

    def test_none_on_malformed_token(self):
        assert codex_auth.account_email(_creds("not-a-jwt")) is None


class TestExhaustion:
    @pytest.fixture(autouse=True)
    def reset_state(self):
        codex_auth._rate_limit = codex_auth._RateLimitState()
        codex_auth._recovery_pending = False
        yield
        codex_auth._rate_limit = codex_auth._RateLimitState()
        codex_auth._recovery_pending = False

    def test_below_cap_not_exhausted(self):
        codex_auth.record_rate_limit_headers(
            {
                "x-codex-primary-used-percent": "73",
                "x-codex-primary-reset-at": "9999999999",
            }
        )
        assert codex_auth.is_exhausted() is False

    def test_primary_cap_exhausts_with_reset(self):
        reset_at = time.time() + 1000
        codex_auth.record_rate_limit_headers(
            {
                "x-codex-primary-used-percent": "100",
                "x-codex-primary-reset-at": str(reset_at),
            }
        )
        assert codex_auth.is_exhausted() is True
        assert codex_auth.exhausted_until() == pytest.approx(reset_at)

    def test_secondary_cap_exhausts(self):
        codex_auth.record_rate_limit_headers(
            {
                "x-codex-secondary-used-percent": "100",
                "x-codex-secondary-reset-at": str(time.time() + 500),
            }
        )
        assert codex_auth.is_exhausted() is True

    def test_not_exhausted_after_reset_passes(self):
        codex_auth.record_rate_limit_headers(
            {
                "x-codex-primary-used-percent": "100",
                "x-codex-primary-reset-at": str(time.time() - 5),
            }
        )
        assert codex_auth.is_exhausted() is False

    def test_recovery_fires_once(self):
        codex_auth.record_rate_limit_headers(
            {
                "x-codex-primary-used-percent": "100",
                "x-codex-primary-reset-at": str(time.time() - 5),
            }
        )
        assert codex_auth.consume_recovery() is True
        assert codex_auth.consume_recovery() is False

    def test_no_recovery_while_still_exhausted(self):
        codex_auth.record_rate_limit_headers(
            {
                "x-codex-primary-used-percent": "100",
                "x-codex-primary-reset-at": str(time.time() + 1000),
            }
        )
        assert codex_auth.consume_recovery() is False

    def test_missing_headers_ignored(self):
        codex_auth.record_rate_limit_headers({"content-type": "text/event-stream"})
        assert codex_auth.is_exhausted() is False
        assert codex_auth.exhausted_until() == 0.0
