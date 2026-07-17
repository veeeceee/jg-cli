"""Emergent durable threads (clustering phase 2) — group the unanchored residual.

Runs only on items that anchored to nothing in phase-1 clustering. A thread is a
durable stored object (stable id, LLM-authored descriptor, member list) that
persists across refreshes. Each refresh does an INCREMENTAL join: only NEW
residual items are classified (join an existing thread by descriptor, or spawn
one with ≥2 members); settled membership is never re-derived — that stickiness is
what stops the flicker.

Skeleton scope: join + spawn only. Merge/split of established threads, descriptor
updates, promotion at the escalate gate, staleness cleanup, and a manual re-audit
are deferred (see docs/work-model.md). Correction is manual for now.

Pure core (types + apply_join) is I/O-free and tested; `emergent()` is the async
adapter (subprocess + persistence). TUI-free, like cluster.py — ports cleanly.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "jg"
THREADS_FILE = CACHE_DIR / "threads.json"

# Threads not touched within this window are dropped (staleness cleanup) — bounds
# threads.json growth without needing membership liveness tracking.
THREAD_TTL_DAYS = 21


@dataclass
class Thread:
    id: str
    descriptor: str                       # LLM-authored label + durable self
    members: list[str] = field(default_factory=list)  # item cids
    created: str = ""
    updated: str = ""


@dataclass
class EItem:
    """A residual item offered to emergent clustering."""
    id: str          # cid
    kind: str
    label: str
    detail: str = ""


def thread_id(members: list[str]) -> str:
    """Stable id from the founding members (derived once, then stored)."""
    h = hashlib.sha256("|".join(sorted(members)).encode()).hexdigest()[:10]
    return f"th-{h}"


def load_threads() -> list[Thread]:
    try:
        raw = json.loads(THREADS_FILE.read_text())
        return [Thread(**t) for t in raw if isinstance(t, dict)]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def save_threads(threads: list[Thread]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        THREADS_FILE.write_text(json.dumps([asdict(t) for t in threads]))
    except OSError:
        pass


def apply_join(
    stored: list[Thread], new_ids: set[str], result: dict, now: str
) -> list[Thread]:
    """Fold an LLM join result into the stored threads (pure). `new_ids` bounds
    what can be added — a member must be an actual new residual item, so the LLM
    can't invent ids or re-touch settled members. New threads need ≥2 members."""
    by_id = {t.id: t for t in stored}
    for a in result.get("assign") or []:
        tid, iid = a.get("thread_id"), a.get("item_id")
        t = by_id.get(tid)
        if t is not None and iid in new_ids and iid not in t.members:
            t.members.append(iid)
            t.updated = now
    for nt in result.get("new_threads") or []:
        members = [m for m in (nt.get("members") or []) if m in new_ids]
        if len(members) < 2:  # a single loose item is not a thread
            continue
        tid = thread_id(members)
        if tid not in by_id:
            th = Thread(tid, (nt.get("descriptor") or "thread").strip(), members, now, now)
            stored.append(th)
            by_id[tid] = th
    return stored


def prune_stale(threads: list[Thread], now_iso: str, ttl_days: int = THREAD_TTL_DAYS) -> list[Thread]:
    """Drop threads whose `updated` is older than ttl_days. Tolerant of bad/blank
    timestamps (kept) so a parse hiccup never silently deletes a thread."""
    try:
        now = dt.datetime.fromisoformat(now_iso)
    except ValueError:
        return threads
    kept: list[Thread] = []
    for t in threads:
        try:
            updated = dt.datetime.fromisoformat(t.updated) if t.updated else now
        except ValueError:
            updated = now
        if (now - updated).days <= ttl_days:
            kept.append(t)
    return kept


def _build_prompt(new_items: list[EItem], stored: list[Thread]) -> str:
    existing = "\n".join(f"- {t.id}: {t.descriptor}" for t in stored) or "(none yet)"
    items = "\n".join(f"- {it.id}: [{it.kind}] {it.label} — {it.detail}" for it in new_items)
    return (
        "You maintain emergent work-threads across scattered items (Slack, email, "
        "Zoho, PRs) that share an underlying topic/effort but have no Jira ticket.\n\n"
        f"EXISTING THREADS:\n{existing}\n\n"
        f"NEW LOOSE ITEMS:\n{items}\n\n"
        "For each new item, either assign it to an existing thread (if it clearly "
        "belongs), or group it with other NEW items that share a real work-thread "
        "into a new thread, or leave it ungrouped. Be conservative — only group "
        "items that genuinely belong together; a wrong grouping is worse than none. "
        "A new thread needs at least 2 members.\n\n"
        "Return ONLY a JSON object, no prose:\n"
        '{"assign":[{"item_id":"<id>","thread_id":"<existing id>"}],'
        '"new_threads":[{"descriptor":"<short label>","members":["<id>","<id>"]}]}'
    )


async def emergent(
    residual: list[EItem], *, now: str, claude_path: str = "claude"
) -> list[Thread]:
    """Incremental join of the residual into durable threads. Only NEW items are
    sent to the LLM; settled membership stays. Fail-soft: on any error the stored
    threads are returned unchanged (no re-partition)."""
    from jg import llm

    stored = prune_stale(load_threads(), now)   # drop rotten threads first
    assigned = {m for t in stored for m in t.members}
    new_items = [it for it in residual if it.id not in assigned]
    if not new_items:
        save_threads(stored)                    # persist the prune even with no new items
        return stored
    try:
        text = await llm.run_claude(_build_prompt(new_items, stored), claude_path)
        result = llm.extract_json_object(text)
    except Exception:
        return stored  # fail-soft: never re-partition on error
    stored = apply_join(stored, {it.id for it in new_items}, result, now)
    save_threads(stored)
    return stored
