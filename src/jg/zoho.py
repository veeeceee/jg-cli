"""Zoho Desk integration — surface support tickets where I'm involved.

Auth: a dedicated jg self-client (OAuth). client_id/org live in config.toml;
client_secret + refresh/access tokens live in the macOS Keychain (same service
as the Atlassian creds, `zoho.`-namespaced). `jg zoho auth` runs the one-time
self-client grant exchange; tokens auto-refresh thereafter.

Discovery (per the derived algorithm): resolve my emails to agent records →
full-email `_all` candidate search (union/dedup) → per-ticket classification via
conversations (to/cc) + comments (@mentions) → drop tokenizer false-positives
(candidates with no real involvement). No global "@mentions of me" search
exists, so mention/thread detection is per-candidate, not desk-wide.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import keyring

from jg.auth import KEYRING_SERVICE
from jg.config import Config, ZohoConfig

KEY_CLIENT_SECRET = "zoho.client_secret"
KEY_REFRESH_TOKEN = "zoho.refresh_token"
KEY_ACCESS_TOKEN = "zoho.access_token"
KEY_EXPIRES_AT = "zoho.expires_at"

SCOPES = "Desk.basic.READ,Desk.tickets.READ,Desk.search.READ,Desk.contacts.READ"


class ZohoError(Exception):
    """Zoho auth/API failure. `needs_relogin` → re-run `jg zoho auth`."""

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


# ── OAuth (self-client) ───────────────────────────────────────────────────────
def exchange_grant(config: ZohoConfig, grant_code: str) -> None:
    """One-time: trade a self-client grant code for refresh + access tokens."""
    secret = get_client_secret()
    if not config.client_id or not secret:
        raise ZohoError("Zoho client_id/secret missing — run `jg zoho auth` setup first")
    resp = httpx.post(
        f"{config.accounts_url}/oauth/v2/token",
        data={
            "grant_type": "authorization_code",
            "client_id": config.client_id,
            "client_secret": secret,
            "code": grant_code,
        },
        timeout=30,
    )
    payload = resp.json() if resp.content else {}
    if resp.status_code != 200 or "refresh_token" not in payload:
        raise ZohoError(f"grant exchange failed: {resp.status_code} {resp.text}")
    _set(KEY_REFRESH_TOKEN, payload["refresh_token"])
    _set(KEY_ACCESS_TOKEN, payload["access_token"])
    _set(KEY_EXPIRES_AT, str(time.time() + payload.get("expires_in", 3600)))


def _refresh_access(config: ZohoConfig) -> str:
    secret = get_client_secret()
    refresh = _get(KEY_REFRESH_TOKEN)
    if not config.client_id or not secret or not refresh:
        raise ZohoError("Zoho not authorized — run `jg zoho auth`", needs_relogin=True)
    resp = httpx.post(
        f"{config.accounts_url}/oauth/v2/token",
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
        raise ZohoError(f"token refresh failed: {resp.status_code} {resp.text}", needs_relogin=True)
    _set(KEY_ACCESS_TOKEN, payload["access_token"])
    _set(KEY_EXPIRES_AT, str(time.time() + payload.get("expires_in", 3600)))
    return payload["access_token"]


def ensure_token(config: ZohoConfig) -> str:
    """Return a valid access token, refreshing if within 60s of expiry."""
    access = _get(KEY_ACCESS_TOKEN)
    expires = _get(KEY_EXPIRES_AT)
    if not access or not expires:
        return _refresh_access(config)
    if time.time() >= float(expires) - 60:
        return _refresh_access(config)
    return access


# ── async API client ──────────────────────────────────────────────────────────
class ZohoClient:
    def __init__(self, config: Config):
        self.config = config.zoho
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ZohoClient:
        token = ensure_token(self.config)
        self._client = httpx.AsyncClient(
            base_url=self.config.api_base,
            headers={"Authorization": f"Zoho-oauthtoken {token}", "orgId": self.config.org_id},
            timeout=30,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    async def _data(self, path: str, params: dict | None = None) -> list[dict]:
        assert self._client is not None
        resp = await self._client.get(path, params=params or {})
        if resp.status_code == 401:
            raise ZohoError("Zoho session expired — run `jg zoho auth`", needs_relogin=True)
        if resp.status_code == 204 or not resp.content:
            return []
        if resp.status_code != 200:
            raise ZohoError(f"Zoho API {resp.status_code}: {resp.text[:200]}")
        return resp.json().get("data", [])

    async def agents(self) -> list[dict]:
        return await self._data("/agents", {"limit": 200})

    async def search_tickets(self, term: str, limit: int = 100) -> list[dict]:
        return await self._data(
            "/tickets/search", {"_all": term, "limit": limit, "sortBy": "-modifiedTime"}
        )

    async def conversations(self, ticket_id: str) -> list[dict]:
        return await self._data(f"/tickets/{ticket_id}/conversations")

    async def comments(self, ticket_id: str) -> list[dict]:
        return await self._data(f"/tickets/{ticket_id}/comments", {"include": "mentions"})


# ── discovery ─────────────────────────────────────────────────────────────────
@dataclass
class InvolvedTicket:
    id: str
    ticket_number: str
    subject: str
    status: str
    assignee: str
    web_url: str
    involvement: list[str] = field(default_factory=list)  # ASSIGNED / THREAD / MENTIONED / BODY
    modified: str = ""


async def resolve_identity(client: ZohoClient, emails: list[str]) -> dict[str, dict]:
    """email(lowercased) -> {agentId, zuid} for emails that are Desk agents."""
    wanted = {e.lower() for e in emails}
    out: dict[str, dict] = {}
    for a in await client.agents():
        em = (a.get("emailId") or "").lower()
        if em in wanted:
            out[em] = {"agentId": str(a.get("id") or ""), "zuid": str(a.get("zuid") or "")}
    return out


def _thread_addresses(conv: dict) -> str:
    """All address text in a thread (to/cc/from), lowercased, for substring checks."""
    parts = [conv.get("to", ""), conv.get("cc", ""), conv.get("fromEmailAddress", "")]
    return " ".join(p for p in parts if p).lower()


async def find_involved(client: ZohoClient, config: ZohoConfig) -> list[InvolvedTicket]:
    emails = [e for e in config.agent_emails if e]
    if not emails:
        return []
    email_lc = [e.lower() for e in emails]
    identity = await resolve_identity(client, emails)
    my_agent_ids = {v["agentId"] for v in identity.values() if v["agentId"]}
    my_zuids = {v["zuid"] for v in identity.values() if v["zuid"]}

    # Candidate search: full email under _all (never the bare token — that pulls
    # in unrelated people who share a first name). Union + dedup by ticket id.
    candidates: dict[str, dict] = {}
    for email in emails:
        for t in await client.search_tickets(email):
            candidates[str(t.get("id"))] = t

    involved: list[InvolvedTicket] = []
    for tid, t in candidates.items():
        types: list[str] = []
        if str(t.get("assigneeId") or "") in my_agent_ids:
            types.append("ASSIGNED")

        convs = await client.conversations(tid)
        if any(any(e in _thread_addresses(c) for e in email_lc) for c in convs):
            types.append("THREAD")

        comments = await client.comments(tid)
        if my_zuids and any(
            any(f"@user:{z}" in (cm.get("content") or "") for z in my_zuids) for cm in comments
        ):
            types.append("MENTIONED")

        # Body/subject/thread contains my exact email (not just the fuzzy token).
        blob = f"{t.get('subject', '')} {t.get('description') or ''}".lower()
        blob += " " + " ".join(_thread_addresses(c) for c in convs)
        if not types and any(e in blob for e in email_lc):
            types.append("BODY")

        # Drop tokenizer false-positives: matched `_all` fuzzily but no real
        # involvement (not assigned/threaded/mentioned, exact email absent).
        if not types:
            continue

        assignee = (t.get("assignee") or {})
        involved.append(
            InvolvedTicket(
                id=tid,
                ticket_number=str(t.get("ticketNumber", "")),
                subject=t.get("subject", ""),
                status=t.get("status", ""),
                assignee=f"{assignee.get('firstName', '') or ''} {assignee.get('lastName', '') or ''}".strip()
                or "—",
                web_url=t.get("webUrl", ""),
                involvement=types,
                modified=t.get("modifiedTime", ""),
            )
        )
    involved.sort(key=lambda i: i.modified, reverse=True)
    return involved
