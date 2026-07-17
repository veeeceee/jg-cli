"""Incoming triage — separate actionable signal from noise (phase 1: the floor).

The deterministic floor handles the bulk of the noise; the LLM (next iteration)
will judge only the ambiguous middle. Governed by asymmetric error cost: a false
positive (a newsletter slips through) is mildly annoying; a false negative (a
colleague's real ask buried) is catastrophic — so the rule is **when unsure,
surface**. The friction goes on suppression.

Pure and fully tested — no I/O. Applied to email only; PRs and Zoho tickets are
pre-filtered signal and bypass triage entirely. See docs/work-model.md →
"Incoming triage".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "jg"
CACHE_FILE = CACHE_DIR / "triage.json"  # {message_id: "actionable"|"suppressed"}


class Verdict(StrEnum):
    ACTIONABLE = "actionable"  # surface
    SUPPRESSED = "suppressed"  # collapse into the expandable "N filtered" line
    UNSURE = "unsure"          # surface for now; the LLM judges these later


@dataclass
class TriageResult:
    verdict: Verdict
    reason: str

    @property
    def surfaced(self) -> bool:
        # "when unsure, surface" — only a hard SUPPRESSED verdict hides an item.
        return self.verdict is not Verdict.SUPPRESSED


# Code-forge notifications are bulk-headered but often real signal (a reply, a
# review comment) — never hard-suppressed; handed to the LLM to judge by content.
_FORGE = ("github.com", "gitlab.com", "bitbucket.org")
# Automated local-parts that (absent a direct-to-me signal) mean machine mail.
_AUTOMATED = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "bounce", "notifications")


def _addr(raw_from: str) -> str:
    """The bare address out of a From header, lowercased."""
    m = re.search(r"<([^>]+)>", raw_from or "")
    return (m.group(1) if m else (raw_from or "")).strip().lower()


def classify(
    *,
    sender: str,
    to_cc: str,
    is_bulk: bool,
    my_addresses: list[str],
    noise_senders: list[str],
    signal_senders: list[str],
) -> TriageResult:
    """Classify one message from its headers + user rules. Order matters: the
    strong-signal overrides come first so a colleague's bulk-headered mail is
    never suppressed."""
    frm = (sender or "").lower()
    addr = _addr(sender)
    haystack = (to_cc or "").lower()

    # 1. A sender you've marked signal → actionable (explicit override, even bulk).
    if any(s.lower() in frm for s in signal_senders if s):
        return TriageResult(Verdict.ACTIONABLE, "signal sender")
    # 2. A sender you've marked noise → suppress.
    if any(n.lower() in frm for n in noise_senders if n):
        return TriageResult(Verdict.SUPPRESSED, "noise sender")
    # 3. Code-forge notification → ambiguous (reply vs star); let the LLM read it.
    if any(f in addr for f in _FORGE):
        return TriageResult(Verdict.UNSURE, "code-forge notification")
    # 4. Bulk / automated → suppress (newsletters, alerts, marketing). This MUST
    #    come before the addressed-to-me check: bulk mail is sent To: me too, so
    #    "my address in To/Cc" is not a signal on its own.
    if is_bulk or any(a in addr for a in _AUTOMATED):
        return TriageResult(Verdict.SUPPRESSED, "bulk / automated")
    # 5. A non-bulk message addressed to me → actionable (a real direct message).
    if my_addresses and any(a.lower() in haystack for a in my_addresses if a):
        return TriageResult(Verdict.ACTIONABLE, "addressed to you")
    # 6. Human-looking message, no clear marker → the ambiguous middle; surface.
    return TriageResult(Verdict.UNSURE, "no clear marker")


def classify_slack(
    *,
    kind: str,
    channel_name: str,
    noise_channels: list[str],
    signal_channels: list[str],
) -> TriageResult:
    """Slack floor: a DM is direct-to-me signal; a configured signal/noise channel
    decides; anything else (a channel @mention) is the ambiguous middle for the
    LLM to judge work-vs-social. Channel match is substring on the name (# optional)."""
    name = (channel_name or "").lstrip("#").lower()
    if kind == "dm":
        return TriageResult(Verdict.ACTIONABLE, "direct message")
    if any(c.lstrip("#").lower() in name for c in signal_channels if c):
        return TriageResult(Verdict.ACTIONABLE, "signal channel")
    if any(c.lstrip("#").lower() in name for c in noise_channels if c):
        return TriageResult(Verdict.SUPPRESSED, "noise channel")
    return TriageResult(Verdict.UNSURE, "channel mention")


# ── LLM judge of the ambiguous middle (async, conservative, cached) ─────────────
@dataclass
class JudgeItem:
    id: str        # message id — the stable cache key
    sender: str
    subject: str


def _build_judge_prompt(items: list[JudgeItem]) -> str:
    lines = "\n".join(f"- {it.id}: from {it.sender} — {it.subject}" for it in items)
    return (
        "You triage ambiguous messages (email or chat) into actionable vs suppressed.\n"
        "- actionable = a real message needing my attention: a human reply, a "
        "direct question/request, a work discussion I'm part of, a review/mention of me.\n"
        "- suppressed = noise with no work ask: bot/CI notifications, star/watch/"
        "digest emails, marketing, status pings, and pure social chit-chat/banter.\n\n"
        "BIAS STRONGLY toward actionable — a missed real message is far worse than "
        "a newsletter or a bit of banter slipping through. Only suppress when clearly noise.\n\n"
        f"MESSAGES:\n{lines}\n\n"
        "Return ONLY a JSON array, one object per message, no prose:\n"
        '[{"id":"<id>","verdict":"actionable|suppressed","reason":"<short>"}]'
    )


def _cache_read() -> dict[str, str]:
    try:
        blob = json.loads(CACHE_FILE.read_text())
        return blob if isinstance(blob, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _cache_write(verdicts: dict[str, str]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(verdicts))
    except OSError:
        pass


async def judge(
    items: list[JudgeItem], *, claude_path: str = "claude", use_cache: bool = True
) -> dict[str, str]:
    """Classify the unsure middle via a conservative `claude -p` call. Verdicts
    are cached per message id so they don't flicker between refreshes. Anything
    the LLM doesn't resolve defaults to actionable (surface) — never suppress on
    doubt. Fail-soft: on any error every item stays actionable."""
    from jg import llm

    cache = _cache_read() if use_cache else {}
    verdicts: dict[str, str] = {it.id: cache[it.id] for it in items if it.id in cache}
    todo = [it for it in items if it.id not in verdicts]
    if todo:
        try:
            text = await llm.run_claude(_build_judge_prompt(todo), claude_path)
            for row in llm.extract_json_array(text):
                iid, v = row.get("id"), row.get("verdict")
                if iid and v in ("actionable", "suppressed"):
                    verdicts[iid] = v
        except Exception:
            pass  # fail-soft: unresolved items fall through to actionable below
        if use_cache and verdicts:
            merged = {**cache, **verdicts}
            _cache_write(merged)
    # conservative default — surface anything the LLM didn't resolve
    return {it.id: verdicts.get(it.id, str(Verdict.ACTIONABLE)) for it in items}
