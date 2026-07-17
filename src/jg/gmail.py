"""Gmail ingestion — surface relevant email in the flow-home.

Auth: jg's own Google OAuth client (Desktop app). client_id lives in config.toml;
client_secret + refresh/access tokens live in the macOS Keychain (same service
as the other creds, `gmail.`-namespaced). jg only ever requests the read-only
scope — it can list and read messages, never send/modify/delete.

`jg gmail auth` runs the one-time loopback consent flow (opens the browser,
captures the code on localhost); tokens auto-refresh thereafter. Mirrors the
Atlassian 3LO flow in auth.py and the Zoho self-client in zoho.py.
"""

from __future__ import annotations

import base64
import http.server
import re
import secrets
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx
import keyring

from jg.auth import KEYRING_SERVICE
from jg.config import Config, GmailConfig

# Read-only: jg can never send, modify, or delete mail.
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

KEY_CLIENT_SECRET = "gmail.client_secret"
KEY_REFRESH_TOKEN = "gmail.refresh_token"
KEY_ACCESS_TOKEN = "gmail.access_token"
KEY_EXPIRES_AT = "gmail.expires_at"

_JIRA_KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")


class GmailError(Exception):
    """Gmail auth/API failure. `needs_relogin` → re-run `jg gmail auth`."""

    def __init__(self, message: str, *, needs_relogin: bool = False):
        super().__init__(message)
        self.needs_relogin = needs_relogin


# ── keyring creds ─────────────────────────────────────────────────────────────
def get_client_secret() -> str | None:
    return keyring.get_password(KEYRING_SERVICE, KEY_CLIENT_SECRET)


def set_client_secret(secret: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEY_CLIENT_SECRET, secret)


def _get(key: str) -> str | None:
    return keyring.get_password(KEYRING_SERVICE, key)


def _set(key: str, value: str) -> None:
    keyring.set_password(KEYRING_SERVICE, key, value)


# ── OAuth loopback flow (installed-app / Desktop client) ────────────────────────
class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    captured: dict[str, str] = {}  # noqa: RUF012
    expected_state: str = ""

    def do_GET(self) -> None:
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
            _CallbackHandler.captured = {"error": error}
        elif state != _CallbackHandler.expected_state:
            _CallbackHandler.captured = {"error": "state_mismatch"}
        elif not code:
            _CallbackHandler.captured = {"error": "no_code"}
        else:
            _CallbackHandler.captured = {"code": code}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body style='font-family:-apple-system;padding:40px'>"
            b"<h2>You can close this tab.</h2><p>jg: Gmail authorization complete.</p></body></html>"
        )

    def log_message(self, *_: Any) -> None:
        return


def _find_free_port(preferred: int = 9877) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def login(config: GmailConfig) -> None:
    """One-time consent: opens the browser, captures the code on localhost, and
    exchanges it for a refresh + access token. `access_type=offline` +
    `prompt=consent` guarantees a refresh token."""
    secret = get_client_secret()
    if not config.client_id or not secret:
        raise GmailError("Gmail client_id/secret missing — run `jg gmail auth` setup first")

    port = _find_free_port()
    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(24)
    _CallbackHandler.expected_state = state
    _CallbackHandler.captured = {}

    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    params = {
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    import webbrowser

    webbrowser.open(f"{AUTH_URL}?{urllib.parse.urlencode(params)}")

    deadline = time.time() + 300
    while not _CallbackHandler.captured and time.time() < deadline:
        time.sleep(0.1)
    server.shutdown()

    captured = _CallbackHandler.captured
    if not captured or "error" in captured:
        raise GmailError(f"OAuth callback failed: {captured.get('error', 'timeout')}")

    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": config.client_id,
            "client_secret": secret,
            "code": captured["code"],
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    payload = resp.json() if resp.content else {}
    if resp.status_code != 200 or "refresh_token" not in payload:
        raise GmailError(f"token exchange failed: {resp.status_code} {resp.text[:200]}")
    _set(KEY_REFRESH_TOKEN, payload["refresh_token"])
    _set(KEY_ACCESS_TOKEN, payload["access_token"])
    _set(KEY_EXPIRES_AT, str(time.time() + payload.get("expires_in", 3600)))


def _refresh_access(config: GmailConfig) -> str:
    secret = get_client_secret()
    refresh = _get(KEY_REFRESH_TOKEN)
    if not config.client_id or not secret or not refresh:
        raise GmailError("Gmail not authorized — run `jg gmail auth`", needs_relogin=True)
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": config.client_id,
            "client_secret": secret,
            "refresh_token": refresh,
        },
        timeout=30,
    )
    payload = resp.json() if resp.content else {}
    if resp.status_code != 200 or "access_token" not in payload:
        raise GmailError(f"token refresh failed: {resp.status_code} {resp.text[:200]}", needs_relogin=True)
    _set(KEY_ACCESS_TOKEN, payload["access_token"])
    _set(KEY_EXPIRES_AT, str(time.time() + payload.get("expires_in", 3600)))
    return payload["access_token"]


def ensure_token(config: GmailConfig) -> str:
    access = _get(KEY_ACCESS_TOKEN)
    expires = _get(KEY_EXPIRES_AT)
    if not access or not expires:
        return _refresh_access(config)
    if time.time() >= float(expires) - 60:
        return _refresh_access(config)
    return access


# ── message model + parsing (pure) ──────────────────────────────────────────────
@dataclass
class Message:
    id: str
    thread_id: str
    sender: str          # raw From header
    subject: str
    snippet: str
    date: str = ""
    to: str = ""         # raw To header (triage: addressed-directly-to-me)
    cc: str = ""         # raw Cc header
    label_ids: list[str] = field(default_factory=list)
    list_unsubscribe: str = ""   # presence ⇒ bulk/newsletter (triage floor signal)
    precedence: str = ""         # "bulk"/"list"/"junk" ⇒ bulk (triage floor signal)

    @property
    def web_url(self) -> str:
        return f"https://mail.google.com/mail/u/0/#inbox/{self.id}"

    @property
    def is_bulk(self) -> bool:
        return bool(self.list_unsubscribe) or self.precedence.lower() in {"bulk", "list", "junk"}

    @property
    def jira_keys(self) -> list[str]:
        return _JIRA_KEY_RE.findall(f"{self.subject} {self.snippet}")


def _header(headers: list[dict], name: str) -> str:
    lname = name.lower()
    for h in headers:
        if (h.get("name") or "").lower() == lname:
            return h.get("value") or ""
    return ""


def parse_message(raw: dict) -> Message:
    """Build a Message from a Gmail `users.messages.get` (metadata format) result."""
    payload = raw.get("payload") or {}
    headers = payload.get("headers") or []
    return Message(
        id=raw.get("id", ""),
        thread_id=raw.get("threadId", ""),
        sender=_header(headers, "From"),
        subject=_header(headers, "Subject"),
        snippet=raw.get("snippet", "") or "",
        date=_header(headers, "Date"),
        to=_header(headers, "To"),
        cc=_header(headers, "Cc"),
        label_ids=list(raw.get("labelIds") or []),
        list_unsubscribe=_header(headers, "List-Unsubscribe"),
        precedence=_header(headers, "Precedence"),
    )


def sender_name(raw_from: str) -> str:
    """`"Heather Duplessis" <heather@x.com>` → `Heather Duplessis` (else the addr)."""
    m = re.match(r'\s*"?([^"<]+?)"?\s*<', raw_from or "")
    return (m.group(1).strip() if m else (raw_from or "").strip()) or "—"


def _decode_b64url(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


def extract_body(payload: dict) -> str:
    """Best-effort readable body from a `format=full` MIME tree: the first
    text/plain part, else the first text/html (caller strips it). Raw text."""
    def walk(p: dict, want: str) -> str:
        if p.get("mimeType") == want:
            data = (p.get("body") or {}).get("data")
            if data:
                return _decode_b64url(data)
        for sub in p.get("parts") or []:
            found = walk(sub, want)
            if found:
                return found
        return ""

    return walk(payload, "text/plain") or walk(payload, "text/html")


# ── async API client ──────────────────────────────────────────────────────────
class GmailClient:
    _META_HEADERS = ("From", "To", "Cc", "Subject", "Date", "List-Unsubscribe", "Precedence")

    def __init__(self, config: Config):
        self.config = config.gmail
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> GmailClient:
        token = ensure_token(self.config)
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        assert self._client is not None
        resp = await self._client.get(path, params=params or {})
        if resp.status_code == 401:
            raise GmailError("Gmail session expired — run `jg gmail auth`", needs_relogin=True)
        if resp.status_code != 200 or not resp.content:
            raise GmailError(f"Gmail API {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def profile_email(self) -> str:
        """The authenticated account's address — auto-used as a triage my_address."""
        try:
            data = await self._get("/profile")
            return (data.get("emailAddress") or "").lower()
        except GmailError:
            return ""

    async def list_message_ids(self, query: str, max_results: int) -> list[str]:
        data = await self._get("/messages", {"q": query, "maxResults": max_results})
        return [m.get("id", "") for m in data.get("messages", []) if m.get("id")]

    async def get_message(self, msg_id: str) -> Message:
        raw = await self._get(
            f"/messages/{msg_id}",
            {"format": "metadata", "metadataHeaders": list(self._META_HEADERS)},
        )
        return parse_message(raw)

    async def full_message(self, msg_id: str) -> tuple[Message, str]:
        """A single message with its decoded body (format=full)."""
        raw = await self._get(f"/messages/{msg_id}", {"format": "full"})
        return parse_message(raw), extract_body(raw.get("payload") or {})

    async def thread_messages(self, thread_id: str) -> list[tuple[Message, str]]:
        """Every message in a thread with its decoded body — one call."""
        data = await self._get(f"/threads/{thread_id}", {"format": "full"})
        return [
            (parse_message(m), extract_body(m.get("payload") or {}))
            for m in data.get("messages", [])
        ]

    async def recent(self, query: str | None = None, max_results: int | None = None) -> list[Message]:
        """The messages jg's query scopes into the incoming pile, fetched
        concurrently. Fails soft per message."""
        import asyncio

        q = query if query is not None else self.config.query
        n = max_results if max_results is not None else self.config.max_results
        ids = await self.list_message_ids(q, n)
        results = await asyncio.gather(*(self.get_message(i) for i in ids), return_exceptions=True)
        return [m for m in results if isinstance(m, Message)]
