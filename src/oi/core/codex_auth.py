"""ChatGPT subscription auth via the Codex OAuth flow.

Logs in with a ChatGPT Pro/Plus/Team subscription through pydantic-ai's
`openai-codex` provider (PKCE + a local loopback callback), stores the resulting
tokens, and builds the provider that bills model usage to the user's
subscription credit rather than a pay-as-you-go API key. The provider owns token
refresh; oi owns the token file and the rate-limit telemetry.
"""

import asyncio
import base64
import json
import os
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from platformdirs import user_config_dir

from oi.exceptions import CodexAuthError

if TYPE_CHECKING:
    from pydantic_ai.providers.openai_codex import (
        OpenAICodexCredentials,
        OpenAICodexProvider,
    )

CALLBACK_TIMEOUT_SECONDS = 300
_TOKEN_KEYS = ("access_token", "refresh_token", "account_id")


def _auth_path() -> Path:
    """Path to the stored OpenAI subscription credentials."""
    auth_dir = Path(user_config_dir("oi", ensure_exists=True)) / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(auth_dir, 0o700)
    except OSError:
        pass
    return auth_dir / "openai.json"


def _read_store() -> Optional[dict[str, str]]:
    """The stored token fields, or None when not logged in."""
    path = _auth_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not all(data.get(k) for k in _TOKEN_KEYS):
        return None
    return {k: data[k] for k in _TOKEN_KEYS}


def load_credentials() -> Optional["OpenAICodexCredentials"]:
    """Load stored credentials, or None when not logged in."""
    from pydantic_ai.providers.openai_codex import OpenAICodexCredentials

    data = _read_store()
    return OpenAICodexCredentials(**data) if data is not None else None


def save_credentials(creds: "OpenAICodexCredentials") -> None:
    path = _auth_path()
    path.write_text(json.dumps({k: getattr(creds, k) for k in _TOKEN_KEYS}, indent=2))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def is_logged_in() -> bool:
    return _read_store() is not None


def account_email(creds: "OpenAICodexCredentials") -> Optional[str]:
    """The login email from the access token's profile claim (unverified)."""
    try:
        payload = creds.access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return None
    return (claims.get("https://api.openai.com/profile") or {}).get("email")


class _TokenStore:
    """`OpenAICodexCredentialSource` over oi's token file, so refreshes persist."""

    async def load(self) -> "OpenAICodexCredentials":
        creds = load_credentials()
        if creds is None:
            raise CodexAuthError(
                "Not logged in to an OpenAI subscription. Run `oi auth openai login`."
            )
        return creds

    async def save(self, credentials: "OpenAICodexCredentials") -> None:
        save_credentials(credentials)


def login() -> "OpenAICodexCredentials":
    """Run the browser OAuth flow and persist the resulting credentials."""
    import anyio
    from pydantic_ai.exceptions import ModelAPIError, UserError
    from pydantic_ai.providers.openai_codex import OpenAICodexOAuthFlow

    flow = OpenAICodexOAuthFlow()
    authorize_url = flow.authorization_url()
    print("Opening your browser to sign in with your ChatGPT subscription...")
    print(f"If it does not open, visit:\n{authorize_url}\n")
    webbrowser.open(authorize_url)

    async def receive() -> "OpenAICodexCredentials":
        with anyio.fail_after(CALLBACK_TIMEOUT_SECONDS):
            return await flow.exchange_code_from_callback()

    try:
        creds = asyncio.run(receive())
    except TimeoutError:
        raise CodexAuthError("Timed out waiting for the browser callback.")
    except OSError as e:
        raise CodexAuthError(
            f"Could not start the login callback server: {e}. "
            "Is another login in progress?"
        )
    except (UserError, ModelAPIError) as e:
        raise CodexAuthError(str(e))
    save_credentials(creds)
    return creds


def logout() -> bool:
    """Delete stored credentials. Returns True if anything was removed."""
    path = _auth_path()
    if path.exists():
        path.unlink()
        return True
    return False


# --- subscription rate-limit telemetry --------------------------------------
#
# The Codex backend returns `x-codex-{primary,secondary}-used-percent` /
# `-reset-at` headers on every response (primary ~5h window, secondary ~7d). A
# window at 100% means that slice of the subscription is spent until its
# `reset-at` epoch. Reading these off each response tells us precisely when the
# subscription is exhausted and when it frees up — no error-body guessing.


@dataclass
class _Window:
    used_percent: float
    reset_at: float  # epoch seconds; 0.0 when the header was absent


@dataclass
class _RateLimitState:
    primary: Optional[_Window] = None
    secondary: Optional[_Window] = None

    def maxed_window(self) -> Optional[_Window]:
        for window in (self.primary, self.secondary):
            if window is not None and window.used_percent >= 100.0:
                return window
        return None


_rate_limit = _RateLimitState()
_recovery_pending = False


def _parse_window(headers: Any, prefix: str) -> Optional[_Window]:
    used = headers.get(f"{prefix}-used-percent")
    if used is None:
        return None
    try:
        used_percent = float(used)
    except (TypeError, ValueError):
        return None
    try:
        reset_at = float(headers.get(f"{prefix}-reset-at") or 0.0)
    except (TypeError, ValueError):
        reset_at = 0.0
    return _Window(used_percent=used_percent, reset_at=reset_at)


def record_rate_limit_headers(headers: Any) -> None:
    """Update the subscription rate-limit snapshot from a Codex response."""
    global _recovery_pending
    primary = _parse_window(headers, "x-codex-primary")
    secondary = _parse_window(headers, "x-codex-secondary")
    if primary is None and secondary is None:
        return
    if primary is not None:
        _rate_limit.primary = primary
    if secondary is not None:
        _rate_limit.secondary = secondary
    if _rate_limit.maxed_window() is not None:
        _recovery_pending = True


def is_exhausted() -> bool:
    """True while a subscription window is at 100% and not yet past its reset."""
    window = _rate_limit.maxed_window()
    return window is not None and time.time() < window.reset_at


def exhausted_until() -> float:
    """Epoch when the exhausted window resets, or 0.0 when not exhausted."""
    window = _rate_limit.maxed_window()
    return window.reset_at if window is not None else 0.0


def consume_recovery() -> bool:
    """True exactly once after an exhausted window frees up (for a recovery notice)."""
    global _recovery_pending
    if _recovery_pending and not is_exhausted():
        _recovery_pending = False
        return True
    return False


def build_provider() -> "OpenAICodexProvider":
    """A Codex provider on the stored login that records rate-limit headers."""
    import httpx2
    from pydantic_ai.providers.openai_codex import OpenAICodexProvider

    async def record_limits(response: httpx2.Response) -> None:
        record_rate_limit_headers(response.headers)

    http_client = httpx2.AsyncClient(
        event_hooks={"response": [record_limits]},
        timeout=httpx2.Timeout(600.0, connect=30.0),
    )
    return OpenAICodexProvider(credential_source=_TokenStore(), http_client=http_client)
