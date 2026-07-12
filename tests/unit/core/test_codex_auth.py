import base64
import json
import time

import pytest

from oi.core import codex_auth
from oi.core.codex_auth import Credentials
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


class TestInjectEmptyInstructions:
    def test_adds_key_when_absent(self):
        out = codex_auth._inject_empty_instructions(b'{"model": "m"}')
        assert out is not None
        assert json.loads(out)["instructions"] == ""

    def test_noop_when_present(self):
        assert (
            codex_auth._inject_empty_instructions(b'{"instructions": "be terse"}')
            is None
        )

    def test_noop_on_non_object(self):
        assert codex_auth._inject_empty_instructions(b"[1, 2, 3]") is None

    def test_noop_on_non_json(self):
        assert codex_auth._inject_empty_instructions(b"not json") is None


class TestAccountIdExtraction:
    def test_direct_claim(self):
        claims = {"chatgpt_account_id": "acct_1"}
        assert codex_auth._account_id_from_claims(claims) == "acct_1"

    def test_namespaced_claim(self):
        claims = {"https://api.openai.com/auth": {"chatgpt_account_id": "acct_2"}}
        assert codex_auth._account_id_from_claims(claims) == "acct_2"

    def test_org_fallback(self):
        claims = {"organizations": [{"id": "org_3"}]}
        assert codex_auth._account_id_from_claims(claims) == "org_3"

    def test_none_when_absent(self):
        assert codex_auth._account_id_from_claims({}) is None


class TestTokenStore:
    def test_round_trip(self, auth_path):
        creds = Credentials("acc", "ref", 123.0, "acct", "me@example.com")
        codex_auth._save_credentials(creds)
        loaded = codex_auth.load_credentials()
        assert loaded == creds

    def test_load_missing_returns_none(self, auth_path):
        assert codex_auth.load_credentials() is None

    def test_file_mode_is_owner_only(self, auth_path):
        codex_auth._save_credentials(Credentials("a", "r", 1.0, None, None))
        assert (auth_path.stat().st_mode & 0o077) == 0

    def test_logout_removes_file(self, auth_path):
        codex_auth._save_credentials(Credentials("a", "r", 1.0, None, None))
        assert codex_auth.logout() is True
        assert not auth_path.exists()
        assert codex_auth.logout() is False


class TestGetAccessToken:
    def test_raises_when_not_logged_in(self, auth_path):
        with pytest.raises(CodexAuthError):
            codex_auth.get_access_token()

    def test_returns_stored_when_fresh(self, auth_path):
        codex_auth._save_credentials(
            Credentials("fresh-token", "ref", time.time() + 3600, "acct", None)
        )
        token, account_id = codex_auth.get_access_token()
        assert token == "fresh-token"
        assert account_id == "acct"

    def test_refreshes_when_near_expiry(self, auth_path, monkeypatch):
        codex_auth._save_credentials(
            Credentials("old-token", "old-ref", time.time() + 5, "acct", None)
        )
        captured = {}

        def fake_exchange(form):
            captured.update(form)
            return {
                "access_token": "new-token",
                "refresh_token": "new-ref",
                "expires_in": 3600,
                "id_token": _make_jwt({"chatgpt_account_id": "acct", "email": "x@y.z"}),
            }

        monkeypatch.setattr(codex_auth, "_exchange_tokens", fake_exchange)
        token, account_id = codex_auth.get_access_token()

        assert token == "new-token"
        assert captured["grant_type"] == "refresh_token"
        assert captured["refresh_token"] == "old-ref"
        # The refreshed token is persisted.
        persisted = codex_auth.load_credentials()
        assert persisted is not None
        assert persisted.access_token == "new-token"


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
