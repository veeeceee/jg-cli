"""Slack ingestion — surface DMs, @mentions, and followed channels in the flow.

Auth: a user token (`xoxp-…`) from your own Slack app, stored in the macOS
Keychain (`slack.user_token`). jg reads as you (DMs, mentions, channels you're
in) but never posts. `jg slack auth` stores the token; add followed channels
under `[slack] channels` in config.

Higher-level `incoming()` aggregates the three signal sources concurrently and
fails soft per source, so a missing scope on one doesn't blank the rest.
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from typing import Any

import httpx
import keyring

from jg.auth import KEYRING_SERVICE
from jg.config import Config

API = "https://slack.com/api"
KEY_TOKEN = "slack.user_token"

_JIRA_KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")


class SlackError(Exception):
    """Slack API failure. `needs_relogin` → re-run `jg slack auth`."""

    def __init__(self, message: str, *, needs_relogin: bool = False):
        super().__init__(message)
        self.needs_relogin = needs_relogin


def set_token(token: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEY_TOKEN, token)


def get_token() -> str | None:
    return keyring.get_password(KEYRING_SERVICE, KEY_TOKEN)


def is_setup() -> bool:
    return bool(get_token())


@dataclass
class SlackMsg:
    channel: str           # channel id
    channel_name: str      # "#eng" | "DM: Jaimon" | "@mention"
    ts: str
    user_name: str
    text: str              # cleaned, human-readable
    kind: str              # "dm" | "mention" | "channel"
    permalink: str = ""

    @property
    def jira_keys(self) -> list[str]:
        return _JIRA_KEY_RE.findall(self.text)


# ── text cleaning (pure) ────────────────────────────────────────────────────────
def clean_text(text: str, users: dict[str, str]) -> str:
    """Slack markup → readable: <@U123> → @name, <#C1|eng> → #eng,
    <http://x|label> → label, <http://x> → x; unescape entities."""
    s = text or ""
    s = re.sub(r"<@(\w+)(?:\|[^>]+)?>", lambda m: "@" + users.get(m.group(1), "someone"), s)
    s = re.sub(r"<#\w+\|([^>]+)>", lambda m: "#" + m.group(1), s)
    s = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", lambda m: m.group(2), s)
    s = re.sub(r"<(https?://[^>]+)>", lambda m: m.group(1), s)
    s = re.sub(r"<!(\w+)>", lambda m: "@" + m.group(1), s)  # <!here>, <!channel>
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


# ── async API client ──────────────────────────────────────────────────────────
class SlackClient:
    def __init__(self, config: Config):
        self.config = config.slack
        self._client: httpx.AsyncClient | None = None
        self._users: dict[str, str] = {}   # id → display name (cache)
        self._team_url = ""

    async def __aenter__(self) -> SlackClient:
        token = get_token()
        if not token:
            raise SlackError("Slack not authorized — run `jg slack auth`", needs_relogin=True)
        self._client = httpx.AsyncClient(
            base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=30
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    async def _call(self, method: str, params: dict | None = None) -> dict:
        assert self._client is not None
        resp = await self._client.get(f"/{method}", params=params or {})
        data = resp.json() if resp.content else {}
        if not data.get("ok"):
            err = data.get("error", f"http {resp.status_code}")
            raise SlackError(f"slack {method}: {err}", needs_relogin=(err == "invalid_auth"))
        return data

    async def auth_test(self) -> str:
        data = await self._call("auth.test")
        self._team_url = data.get("url", "")
        return data.get("user_id", "")

    async def _name(self, user_id: str) -> str:
        if user_id not in self._users:
            try:
                info = await self._call("users.info", {"user": user_id})
                p = info.get("user", {}).get("profile", {})
                self._users[user_id] = (
                    p.get("display_name") or info["user"].get("real_name") or info["user"].get("name") or user_id
                )
            except SlackError:
                self._users[user_id] = user_id
        return self._users[user_id]

    def _permalink(self, channel: str, ts: str) -> str:
        if not self._team_url:
            return ""
        return f"{self._team_url}archives/{channel}/p{ts.replace('.', '')}"

    async def dms(self, limit: int = 5) -> list[SlackMsg]:
        conv = await self._call("conversations.list", {"types": "im", "limit": 50})
        out: list[SlackMsg] = []
        for ch in conv.get("channels", []):
            hist = await self._call("conversations.history", {"channel": ch["id"], "limit": limit})
            for m in hist.get("messages", []):
                if m.get("type") != "message" or m.get("subtype"):
                    continue
                name = await self._name(m.get("user", ""))
                out.append(SlackMsg(
                    channel=ch["id"], channel_name=f"DM: {name}", ts=m.get("ts", ""),
                    user_name=name, text=clean_text(m.get("text", ""), self._users),
                    kind="dm", permalink=self._permalink(ch["id"], m.get("ts", "")),
                ))
        return out

    async def mentions(self, user_id: str, count: int = 20) -> list[SlackMsg]:
        # search.messages indexes the rendered mention, so the handle finds them.
        handle = await self._name(user_id)
        data = await self._call("search.messages", {"query": f"@{handle}", "count": count})
        out: list[SlackMsg] = []
        for m in data.get("messages", {}).get("matches", []):
            ch = m.get("channel", {})
            out.append(SlackMsg(
                channel=ch.get("id", ""), channel_name=f"#{ch.get('name', '?')}", ts=m.get("ts", ""),
                user_name=m.get("username", "") or await self._name(m.get("user", "")),
                text=clean_text(m.get("text", ""), self._users),
                kind="mention", permalink=m.get("permalink", ""),
            ))
        return out

    async def channel_history(self, channel_id: str, limit: int = 5) -> list[SlackMsg]:
        hist = await self._call("conversations.history", {"channel": channel_id, "limit": limit})
        out: list[SlackMsg] = []
        for m in hist.get("messages", []):
            if m.get("type") != "message" or m.get("subtype"):
                continue
            name = await self._name(m.get("user", ""))
            out.append(SlackMsg(
                channel=channel_id, channel_name=f"#{channel_id}", ts=m.get("ts", ""),
                user_name=name, text=clean_text(m.get("text", ""), self._users),
                kind="channel", permalink=self._permalink(channel_id, m.get("ts", "")),
            ))
        return out

    async def incoming(self) -> list[SlackMsg]:
        """DMs + @mentions + followed-channel messages, concurrent + fail-soft,
        deduped by (channel, ts), newest first."""
        user_id = await self.auth_test()

        async def _safe(coro):
            try:
                return await coro
            except SlackError:
                return []

        tasks = [_safe(self.dms()), _safe(self.mentions(user_id))]
        tasks += [_safe(self.channel_history(cid)) for cid in self.config.channels]
        results = await asyncio.gather(*tasks)

        seen: set[tuple[str, str]] = set()
        merged: list[SlackMsg] = []
        for group in results:
            for msg in group:
                key = (msg.channel, msg.ts)
                if key not in seen:
                    seen.add(key)
                    merged.append(msg)
        merged.sort(key=lambda m: m.ts, reverse=True)
        return merged
