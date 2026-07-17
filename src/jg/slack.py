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
def format_ts(ts: str) -> str:
    """Slack epoch ts ('1721145600.0012') → 'Jul 17 15:00' (local)."""
    try:
        import datetime as dt

        return dt.datetime.fromtimestamp(float(ts.split(".")[0])).strftime("%b %d %H:%M")
    except (ValueError, OSError, IndexError):
        return ""


_MENTION_ID = re.compile(r"<@(\w+)(?:\|[^>]+)?>")


def clean_text(text: str, users: dict[str, str]) -> str:
    """Slack markup → readable: <@U123> → @name (inline handle if present, else
    the resolved cache, else @someone), <#C1|eng> → #eng, <http://x|label> →
    label, <http://x> → x; unescape entities."""
    def _user(m: re.Match) -> str:
        uid, label = m.group(1), m.group(2)
        if label:  # Slack sometimes inlines the handle: <@U123|vibhu>
            return "@" + label
        return "@" + users.get(uid, "someone")

    s = text or ""
    s = re.sub(r"<@(\w+)(?:\|([^>]+))?>", _user, s)
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
        self.warnings: list[str] = []      # per-source failures (e.g. missing_scope)

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

    async def _clean(self, text: str) -> str:
        """Resolve the user-ids mentioned *inside* the text (not just the author)
        into the name cache, then render. So <@U123> shows who was tagged."""
        for uid in {m.group(1) for m in _MENTION_ID.finditer(text or "")}:
            await self._name(uid)  # cache-fills; no-op if already known
        return clean_text(text, self._users)

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
                    user_name=name, text=await self._clean(m.get("text", "")),
                    kind="dm", permalink=self._permalink(ch["id"], m.get("ts", "")),
                ))
        return out

    async def _channel_label(self, ch: dict) -> tuple[str, str]:
        """(display name, kind) for a search-result channel. A DM (is_im) has its
        `name` set to the peer's *user id*, so resolve it to 'DM: <name>' and mark
        it a dm (→ actionable), not a channel mention showing a raw id."""
        if ch.get("is_im"):
            return f"DM: {await self._name(ch.get('user', ''))}", "dm"
        return f"#{ch.get('name') or ch.get('id', '?')}", "mention"

    async def mentions(self, user_id: str, count: int = 20) -> list[SlackMsg]:
        # search.messages indexes the rendered mention, so the handle finds them.
        handle = await self._name(user_id)
        data = await self._call("search.messages", {"query": f"@{handle}", "count": count})
        out: list[SlackMsg] = []
        for m in data.get("messages", {}).get("matches", []):
            ch = m.get("channel", {})
            cname, kind = await self._channel_label(ch)
            out.append(SlackMsg(
                channel=ch.get("id", ""), channel_name=cname, ts=m.get("ts", ""),
                user_name=m.get("username", "") or await self._name(m.get("user", "")),
                text=await self._clean(m.get("text", "")),
                kind=kind, permalink=m.get("permalink", ""),
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
                user_name=name, text=await self._clean(m.get("text", "")),
                kind="channel", permalink=self._permalink(channel_id, m.get("ts", "")),
            ))
        return out

    async def thread_replies(self, channel: str, ts: str) -> list[SlackMsg]:
        """The whole thread a message belongs to (passing any message ts in the
        thread returns all of it). A standalone message returns just itself."""
        data = await self._call("conversations.replies", {"channel": channel, "ts": ts})
        out: list[SlackMsg] = []
        for m in data.get("messages", []):
            if m.get("type") != "message":
                continue
            name = await self._name(m.get("user", ""))
            out.append(SlackMsg(
                channel=channel, channel_name="", ts=m.get("ts", ""),
                user_name=name, text=await self._clean(m.get("text", "")),
                kind="reply", permalink=self._permalink(channel, m.get("ts", "")),
            ))
        return out

    async def incoming(self) -> list[SlackMsg]:
        """DMs + @mentions + followed-channel messages, concurrent + fail-soft,
        deduped by (channel, ts), newest first."""
        user_id = await self.auth_test()

        async def _safe(label: str, coro):
            try:
                return await coro
            except SlackError as e:
                self.warnings.append(f"{label} unavailable: {e}")
                return []

        tasks = [_safe("DMs", self.dms()), _safe("mentions", self.mentions(user_id))]
        tasks += [_safe(f"#{cid}", self.channel_history(cid)) for cid in self.config.channels]
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
