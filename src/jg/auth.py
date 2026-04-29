"""Atlassian OAuth 2.0 (3LO) flow + token storage in macOS Keychain.

Setup is a two-step user flow:
1. `ch auth setup` — walks user through registering an OAuth app at
   https://developer.atlassian.com/console/myapps/, captures client_id and
   client_secret. Client ID goes to config.toml; client_secret to Keychain.
2. `ch auth login` — opens browser, captures the callback on localhost,
   exchanges code for tokens, stores in Keychain.

Tokens auto-refresh via `ensure_token()` when an access_token is within 60s
of expiry. Refresh failures bubble up so callers can prompt re-login.
"""

from __future__ import annotations

import http.server
import json
import secrets
import socket
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from typing import Any

import httpx
import keyring

from jg.config import Config

KEYRING_SERVICE = "ch-cli"
KEY_CLIENT_SECRET = "atlassian.client_secret"
KEY_ACCESS_TOKEN = "atlassian.access_token"
KEY_REFRESH_TOKEN = "atlassian.refresh_token"
KEY_EXPIRES_AT = "atlassian.expires_at"

AUTH_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"


class AuthError(Exception):
    """Auth-related failure. Recoverable via re-running `ch auth login` unless
    `transient` is True, in which case retrying may work."""

    def __init__(self, message: str, *, transient: bool = False, needs_relogin: bool = True):
        super().__init__(message)
        self.transient = transient
        self.needs_relogin = needs_relogin


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds

    @property
    def is_expiring_soon(self) -> bool:
        return time.time() >= self.expires_at - 60


def get_client_secret() -> str | None:
    return keyring.get_password(KEYRING_SERVICE, KEY_CLIENT_SECRET)


def set_client_secret(secret: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEY_CLIENT_SECRET, secret)


def get_tokens() -> TokenSet | None:
    access = keyring.get_password(KEYRING_SERVICE, KEY_ACCESS_TOKEN)
    refresh = keyring.get_password(KEYRING_SERVICE, KEY_REFRESH_TOKEN)
    expires = keyring.get_password(KEYRING_SERVICE, KEY_EXPIRES_AT)
    if not access or not refresh or not expires:
        return None
    return TokenSet(access_token=access, refresh_token=refresh, expires_at=float(expires))


def set_tokens(tokens: TokenSet) -> None:
    keyring.set_password(KEYRING_SERVICE, KEY_ACCESS_TOKEN, tokens.access_token)
    keyring.set_password(KEYRING_SERVICE, KEY_REFRESH_TOKEN, tokens.refresh_token)
    keyring.set_password(KEYRING_SERVICE, KEY_EXPIRES_AT, str(tokens.expires_at))


def clear_tokens() -> None:
    for k in (KEY_ACCESS_TOKEN, KEY_REFRESH_TOKEN, KEY_EXPIRES_AT):
        try:
            keyring.delete_password(KEYRING_SERVICE, k)
        except keyring.errors.PasswordDeleteError:
            pass


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    captured: dict[str, str] = {}
    expected_state: str = ""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        state = params.get("state", [""])[0]
        code = params.get("code", [""])[0]
        error = params.get("error", [""])[0]
        if error:
            _CallbackHandler.captured = {"error": error, "error_description": params.get("error_description", [""])[0]}
        elif state != _CallbackHandler.expected_state:
            _CallbackHandler.captured = {"error": "state_mismatch"}
        elif not code:
            _CallbackHandler.captured = {"error": "no_code"}
        else:
            _CallbackHandler.captured = {"code": code}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = b"<html><body style='font-family: -apple-system; padding: 40px;'><h2>You can close this tab.</h2><p>ch CLI: authorization complete. Returning to terminal.</p></body></html>"
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:  # silence
        return


def _find_free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def login(config: Config) -> TokenSet:
    """Run the OAuth 3LO flow. Opens browser, blocks on callback."""
    if not config.client_id:
        raise AuthError("OAuth not set up. Run: ch auth setup")
    client_secret = get_client_secret()
    if not client_secret:
        raise AuthError("client_secret missing in keychain. Run: ch auth setup")

    parsed = urllib.parse.urlparse(config.redirect_uri)
    port = _find_free_port(parsed.port or 9876)
    redirect_uri = f"http://{parsed.hostname or 'localhost'}:{port}/callback"

    state = secrets.token_urlsafe(24)
    _CallbackHandler.expected_state = state
    _CallbackHandler.captured = {}

    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    auth_params = {
        "audience": "api.atlassian.com",
        "client_id": config.client_id,
        "scope": " ".join(config.scopes),
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(auth_params)}"
    webbrowser.open(url)

    deadline = time.time() + 300
    while not _CallbackHandler.captured and time.time() < deadline:
        time.sleep(0.1)
    server.shutdown()

    captured = _CallbackHandler.captured
    if not captured or "error" in captured:
        raise AuthError(f"OAuth callback failed: {captured.get('error', 'timeout')} {captured.get('error_description', '')}".strip())

    code = captured["code"]
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": config.client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
    if resp.status_code != 200:
        raise AuthError(f"Token exchange failed: {resp.status_code} {resp.text}")
    payload = resp.json()
    tokens = TokenSet(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        expires_at=time.time() + payload.get("expires_in", 3600),
    )
    set_tokens(tokens)
    return tokens


def refresh(config: Config, tokens: TokenSet) -> TokenSet:
    if not config.client_id:
        raise AuthError("OAuth not set up. Run: ch auth setup")
    client_secret = get_client_secret()
    if not client_secret:
        raise AuthError("client_secret missing in keychain. Run: ch auth setup")
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                TOKEN_URL,
                json={
                    "grant_type": "refresh_token",
                    "client_id": config.client_id,
                    "client_secret": client_secret,
                    "refresh_token": tokens.refresh_token,
                },
            )
    except (httpx.RequestError, httpx.TimeoutException) as e:
        # Network blip — caller can retry without re-login.
        raise AuthError(f"Network error refreshing token: {e}", transient=True, needs_relogin=False) from e

    if resp.status_code == 200:
        payload = resp.json()
        new_tokens = TokenSet(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token", tokens.refresh_token),
            expires_at=time.time() + payload.get("expires_in", 3600),
        )
        set_tokens(new_tokens)
        return new_tokens

    # Atlassian returns invalid_grant when the refresh_token itself is dead
    # (revoked, expired, or the user logged out elsewhere).
    body = resp.text
    if resp.status_code in (400, 401) and "invalid_grant" in body:
        clear_tokens()
        raise AuthError(
            "Atlassian session expired. Run: ch auth login",
            transient=False,
            needs_relogin=True,
        )
    if resp.status_code >= 500:
        raise AuthError(
            f"Atlassian token endpoint {resp.status_code} (likely transient). Retry.",
            transient=True,
            needs_relogin=False,
        )
    raise AuthError(f"Token refresh failed: {resp.status_code} {body}")


def ensure_token(config: Config) -> str:
    """Return a valid access token, refreshing if needed. Raises AuthError otherwise."""
    tokens = get_tokens()
    if not tokens:
        raise AuthError("Not logged in. Run: ch auth login")
    if tokens.is_expiring_soon:
        tokens = refresh(config, tokens)
    return tokens.access_token


def list_resources(access_token: str) -> list[dict[str, Any]]:
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            RESOURCES_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise AuthError(f"Failed to list accessible resources: {resp.status_code} {resp.text}")
    return resp.json()
