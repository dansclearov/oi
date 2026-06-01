"""ChatGPT subscription auth via the Codex OAuth flow.

Logs in with a ChatGPT Pro/Plus/Team subscription using OpenAI's Codex OAuth
(PKCE + a local loopback callback), stores the resulting tokens, and hands a
fresh access token + account id to the request layer. Tokens bill model usage to
the user's subscription credit rather than a pay-as-you-go API key.
"""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urlparse, parse_qs

import httpx
from platformdirs import user_config_dir

from oi.exceptions import CodexAuthError

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{ISSUER}/oauth/authorize"
TOKEN_URL = f"{ISSUER}/oauth/token"
SCOPE = "openid profile email offline_access"
ORIGINATOR = "oi"

REDIRECT_PORT = 1455
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/auth/callback"

# Subscription model traffic goes to the Codex backend, not api.openai.com.
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"

# Refresh slightly before expiry so an in-flight request never races the clock.
EXPIRY_SKEW_SECONDS = 120
CALLBACK_TIMEOUT_SECONDS = 300


def _auth_path() -> Path:
    """Path to the stored OpenAI subscription credentials."""
    auth_dir = Path(user_config_dir("oi", ensure_exists=True)) / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(auth_dir, 0o700)
    except OSError:
        pass
    return auth_dir / "openai.json"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _make_pkce() -> tuple[str, str]:
    """Return a (verifier, challenge) PKCE pair using S256."""
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    """Best-effort decode of a JWT payload segment (no signature check)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _account_id_from_claims(claims: dict[str, Any]) -> Optional[str]:
    auth_claim = claims.get("https://api.openai.com/auth") or {}
    orgs = claims.get("organizations") or [{}]
    return (
        claims.get("chatgpt_account_id")
        or auth_claim.get("chatgpt_account_id")
        or orgs[0].get("id")
    )


# --- token store -----------------------------------------------------------


@dataclass
class Credentials:
    access_token: str
    refresh_token: str
    expires_at: float
    account_id: Optional[str]
    email: Optional[str]


def load_credentials() -> Optional[Credentials]:
    """Load stored credentials, or None when not logged in."""
    path = _auth_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return Credentials(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=float(data.get("expires_at", 0)),
            account_id=data.get("account_id"),
            email=data.get("email"),
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return None


def _save_credentials(creds: Credentials) -> None:
    path = _auth_path()
    payload = {
        "access_token": creds.access_token,
        "refresh_token": creds.refresh_token,
        "expires_at": creds.expires_at,
        "account_id": creds.account_id,
        "email": creds.email,
    }
    path.write_text(json.dumps(payload, indent=2))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _credentials_from_token_response(
    tokens: dict[str, Any], prev: Optional[Credentials] = None
) -> Credentials:
    """Build a Credentials record from an OAuth token response."""
    id_claims = _decode_jwt_claims(tokens.get("id_token", ""))
    account_id = _account_id_from_claims(id_claims) or (
        prev.account_id if prev else None
    )
    email = id_claims.get("email") or (prev.email if prev else None)
    return Credentials(
        access_token=tokens["access_token"],
        # OpenAI rotates refresh tokens; keep the old one only if none returned.
        refresh_token=tokens.get("refresh_token")
        or (prev.refresh_token if prev else ""),
        expires_at=time.time() + float(tokens.get("expires_in", 3600)),
        account_id=account_id,
        email=email,
    )


# --- OAuth flow ------------------------------------------------------------


class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict[str, Any] = {}
    expected_state: str = ""

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        parsed = urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        error = params.get("error", [None])[0]

        if error:
            type(self).result = {"error": error}
        elif not code or state != type(self).expected_state:
            type(self).result = {"error": "invalid_callback"}
        else:
            type(self).result = {"code": code}

        ok = "error" not in type(self).result
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        title = "Login successful" if ok else "Login failed"
        self.wfile.write(
            f"<html><body style='font-family:sans-serif;text-align:center;"
            f"margin-top:4rem'><h2>{title}</h2><p>You can close this tab and "
            f"return to the terminal.</p></body></html>".encode()
        )

    def log_message(self, *args: Any) -> None:
        """Silence the default per-request stderr logging."""


def _exchange_tokens(form: dict[str, str]) -> dict[str, Any]:
    response = httpx.post(TOKEN_URL, data=form, timeout=30)
    if response.status_code != 200:
        raise CodexAuthError(
            f"OpenAI token endpoint returned {response.status_code}: {response.text}"
        )
    return response.json()


def login() -> Credentials:
    """Run the browser OAuth flow and persist the resulting credentials."""
    verifier, challenge = _make_pkce()
    state = _b64url(secrets.token_bytes(32))

    authorize_url = (
        AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "scope": SCOPE,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "id_token_add_organizations": "true",
                "codex_cli_simplified_flow": "true",
                "originator": ORIGINATOR,
                "state": state,
            }
        )
    )

    _CallbackHandler.result = {}
    _CallbackHandler.expected_state = state
    try:
        server = HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)
    except OSError as e:
        raise CodexAuthError(
            f"Could not start the login callback server on port {REDIRECT_PORT}: {e}. "
            "Is another login in progress?"
        )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print("Opening your browser to sign in with your ChatGPT subscription...")
        print(f"If it does not open, visit:\n{authorize_url}\n")
        webbrowser.open(authorize_url)

        deadline = time.time() + CALLBACK_TIMEOUT_SECONDS
        while not _CallbackHandler.result and time.time() < deadline:
            time.sleep(0.2)
    finally:
        server.shutdown()
        thread.join(timeout=2)

    result = _CallbackHandler.result
    if not result:
        raise CodexAuthError("Timed out waiting for the browser callback.")
    if "error" in result:
        raise CodexAuthError(f"Authorization failed: {result['error']}")

    tokens = _exchange_tokens(
        {
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        }
    )
    creds = _credentials_from_token_response(tokens)
    _save_credentials(creds)
    return creds


def logout() -> bool:
    """Delete stored credentials. Returns True if anything was removed."""
    path = _auth_path()
    if path.exists():
        path.unlink()
        return True
    return False


def _refresh(creds: Credentials) -> Credentials:
    tokens = _exchange_tokens(
        {
            "grant_type": "refresh_token",
            "refresh_token": creds.refresh_token,
            "client_id": CLIENT_ID,
        }
    )
    refreshed = _credentials_from_token_response(tokens, prev=creds)
    _save_credentials(refreshed)
    return refreshed


def is_logged_in() -> bool:
    return load_credentials() is not None


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


def _inject_empty_instructions(content: bytes) -> Optional[bytes]:
    """Return body bytes with an empty `instructions` key added when missing.

    The Codex backend rejects requests whose `instructions` field is absent or
    null, but accepts a literal empty string. pydantic-ai omits empty/whitespace
    instructions, so re-adding an empty one preserves an empty-system-prompt
    session. Returns None when no change is needed.
    """
    try:
        body = json.loads(content.decode())
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(body, dict) or body.get("instructions"):
        return None
    body["instructions"] = ""
    return json.dumps(body).encode()


class _InstructionsTransport(httpx.AsyncHTTPTransport):
    """Guarantee the Responses body carries an `instructions` key."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        new_content = _inject_empty_instructions(request.content)
        if new_content is not None:
            request = httpx.Request(
                method=request.method,
                url=request.url,
                headers=request.headers,
                content=new_content,
            )
            request.headers["content-length"] = str(len(new_content))
        return await super().handle_async_request(request)


def build_async_client(
    access_token: str, account_id: Optional[str]
) -> httpx.AsyncClient:
    """An httpx client that injects subscription auth headers and instructions."""

    async def inject_auth(request: httpx.Request) -> None:
        request.headers["Authorization"] = f"Bearer {access_token}"
        if account_id:
            request.headers["ChatGPT-Account-Id"] = account_id

    async def record_limits(response: httpx.Response) -> None:
        record_rate_limit_headers(response.headers)

    return httpx.AsyncClient(
        transport=_InstructionsTransport(),
        event_hooks={"request": [inject_auth], "response": [record_limits]},
        timeout=httpx.Timeout(600.0, connect=30.0),
    )


def get_access_token() -> tuple[str, Optional[str]]:
    """Return a fresh (access_token, account_id), refreshing if near expiry.

    Raises CodexAuthError when not logged in or a refresh fails.
    """
    creds = load_credentials()
    if creds is None:
        raise CodexAuthError(
            "Not logged in to an OpenAI subscription. Run `oi auth openai login`."
        )
    if creds.expires_at - time.time() < EXPIRY_SKEW_SECONDS:
        creds = _refresh(creds)
    return creds.access_token, creds.account_id
