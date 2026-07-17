"""Cross-source clustering (phase 1: anchored-only).

Groups loose work items (Zoho tickets, PRs) under the Jira ticket they belong
to. Two layers, same floor-plus-fuzzy shape as reconcile:

- **Deterministic backbone** — authored cross-references jg already extracts (a
  PR branch naming the key, Zoho's "Associated Jira Issues" field, a session
  pane's @jg_key). High-confidence *priors*, kind=AUTHORED.
- **LLM assignment** — the loose residual (items with no authored key) is handed
  to a headless `claude -p` call that assigns each to an existing anchor by
  topic, or leaves it unanchored. kind=LLM, always rendered with reason +
  confidence, **never** labelled "linked".

Invariants enforced here in code, not prose (see docs/work-model.md):
- An LLM edge never overrides an authored one (authored wins on conflict).
- An edge only ever connects item→anchor; it structurally cannot merge two
  anchors (merge is phase 2/3).
- The LLM path is fail-soft: any error degrades to backbone-only, never raises.

Pure core (types + build_backbone + merge_llm_edges + group) has no I/O and is
fully tested. `enrich()` is the thin async adapter (subprocess + cache). Kept
deliberately TUI-free so it ports cleanly if jg ever moves to Go.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "jg"
CACHE_FILE = CACHE_DIR / "clusters.json"

# LLM groupings below this confidence are dropped to the residual rather than
# shown — "a wrong grouping is worse than none". Uses the model's own certainty.
MIN_LLM_CONFIDENCE = 0.5


class ClusterError(Exception):
    pass


class EdgeKind(StrEnum):
    AUTHORED = "authored"  # PR branch / Zoho field / pane @jg_key — strong prior
    LLM = "llm"            # content-based LLM assignment — soft, never "linked"


# How an authored edge reads, per source. Loose items carry their own kind.
_AUTHORED_REASON = {
    "pr": "PR branch names the key",
    "zoho": "Zoho 'Associated Jira Issues' field",
    "session": "live session stamped with the key",
}


@dataclass
class Item:
    """A loose item to be clustered. `linked_keys` are authored keys jg already
    holds (branch/field/pane); empty => the LLM must place it by content."""
    id: str                              # "zoho:1544149", "pr:org/repo#12"
    kind: str                            # "zoho" | "pr" | "session"
    label: str
    detail: str = ""                     # topic text the LLM judges on
    linked_keys: list[str] = field(default_factory=list)


@dataclass
class Anchor:
    key: str
    summary: str = ""


@dataclass
class Edge:
    item_id: str
    anchor_key: str
    reason: str
    confidence: float
    kind: EdgeKind


@dataclass
class ClusterItem:
    id: str
    kind: str
    label: str
    detail: str
    edge: Edge


@dataclass
class Cluster:
    anchor_key: str
    summary: str
    members: list[ClusterItem]


@dataclass
class ClusterResult:
    clusters: list[Cluster]
    residual: list[Item]          # items that anchored to nothing
    edges: list[Edge]


# ── pure core ─────────────────────────────────────────────────────────────────
def build_backbone(items: list[Item]) -> list[Edge]:
    """Deterministic edges from authored cross-references. Strong priors (0.95),
    but still falsifiable — group() only forms clusters for keys that are real
    anchors, so a link to a closed/foreign ticket simply forms no cluster."""
    edges: list[Edge] = []
    for it in items:
        reason = _AUTHORED_REASON.get(it.kind, "authored link")
        for key in it.linked_keys:
            edges.append(Edge(it.id, key, reason, 0.95, EdgeKind.AUTHORED))
    return edges


def merge_llm_edges(
    backbone: list[Edge],
    llm_rows: list[dict],
    anchor_keys: set[str],
    loose_ids: set[str],
    min_confidence: float = MIN_LLM_CONFIDENCE,
) -> list[Edge]:
    """Fold LLM assignments into the backbone under the asymmetry rules:
    authored always wins, the LLM may only place a *loose* item onto a *real*
    anchor, it can never merge anchors (edges are item→anchor only), and
    low-confidence guesses are dropped to the residual (min_confidence)."""
    edges = list(backbone)
    already_anchored = {e.item_id for e in backbone}
    for row in llm_rows:
        iid = row.get("item_id")
        ak = row.get("anchor_key")
        if not iid or not ak:
            continue
        if iid in already_anchored:        # authored wins — LLM never overrides
            continue
        if iid not in loose_ids:
            continue
        if ak not in anchor_keys:          # must be a real anchor, not invented
            continue
        try:
            conf = float(row.get("confidence"))
        except (TypeError, ValueError):
            conf = 0.5
        conf = max(0.0, min(1.0, conf))
        if conf < min_confidence:          # too weak to show — leave it unclustered
            continue
        why = (row.get("reason") or "").strip()
        # "grouped:" prefix keeps LLM edges from ever reading as a hard link.
        reason = f"grouped: {why}" if why else "grouped by topic"
        edges.append(Edge(iid, ak, reason, conf, EdgeKind.LLM))
    return edges


def group(
    edges: list[Edge], anchors: list[Anchor], items: list[Item]
) -> tuple[list[Cluster], list[Item]]:
    """Assemble clusters (anchor + its member items) and the unanchored residual.
    Only anchors with ≥1 member become clusters."""
    by_id = {it.id: it for it in items}
    anchor_by_key = {a.key: a for a in anchors}
    members: dict[str, list[ClusterItem]] = {}
    anchored_ids: set[str] = set()
    for e in edges:
        if e.anchor_key not in anchor_by_key:
            continue
        it = by_id.get(e.item_id)
        if it is None:
            continue
        members.setdefault(e.anchor_key, []).append(
            ClusterItem(it.id, it.kind, it.label, it.detail, e)
        )
        anchored_ids.add(e.item_id)
    clusters = [
        Cluster(key, anchor_by_key[key].summary, mem) for key, mem in members.items()
    ]
    clusters.sort(key=lambda c: (-len(c.members), c.anchor_key))
    residual = [it for it in items if it.id not in anchored_ids]
    return clusters, residual


# ── LLM assignment (headless claude -p) ─────────────────────────────────────────
def _build_prompt(loose: list[Item], anchors: list[Anchor]) -> str:
    anchor_lines = "\n".join(f"- {a.key}: {a.summary}" for a in anchors)
    loose_lines = "\n".join(f"- {it.id}: [{it.kind}] {it.label} — {it.detail}" for it in loose)
    return (
        "You group loose work items under the Jira ticket they most plausibly "
        "belong to, judging by shared topic/entities (product, client, module).\n\n"
        f"ANCHORS (existing Jira tickets):\n{anchor_lines}\n\n"
        f"LOOSE ITEMS to place:\n{loose_lines}\n\n"
        "For each loose item choose the ONE anchor it belongs to, or null if none "
        "genuinely fits. Be conservative: null unless there is a real topical "
        "match — a wrong grouping is worse than none.\n\n"
        "Return ONLY a JSON array, one object per loose item, no prose:\n"
        '[{"item_id":"<id>","anchor_key":"<CH-KEY or null>",'
        '"reason":"<short why>","confidence":<0.0-1.0>}]'
    )


def _cache_key(loose: list[Item], anchors: list[Anchor]) -> str:
    h = hashlib.sha256()
    h.update("|".join(sorted(it.id for it in loose)).encode())
    h.update(b"::")
    h.update("|".join(sorted(a.key for a in anchors)).encode())
    return h.hexdigest()


def _cache_read(key: str) -> list[dict] | None:
    try:
        blob = json.loads(CACHE_FILE.read_text())
        rows = blob.get(key)
        return rows if isinstance(rows, list) else None
    except (OSError, json.JSONDecodeError):
        return None


def _cache_write(key: str, rows: list[dict]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        blob = {}
        if CACHE_FILE.exists():
            try:
                blob = json.loads(CACHE_FILE.read_text())
            except json.JSONDecodeError:
                blob = {}
        blob[key] = rows
        CACHE_FILE.write_text(json.dumps(blob))
    except OSError:
        pass


async def _assign_loose(
    loose: list[Item], anchors: list[Anchor], claude_path: str, use_cache: bool
) -> list[dict]:
    key = _cache_key(loose, anchors)
    if use_cache:
        cached = _cache_read(key)
        if cached is not None:
            return cached
    from jg import llm

    text = await llm.run_claude(_build_prompt(loose, anchors), claude_path)
    rows = llm.extract_json_array(text)
    if use_cache and rows:
        _cache_write(key, rows)
    return rows


async def enrich(
    items: list[Item],
    anchors: list[Anchor],
    *,
    claude_path: str = "claude",
    use_cache: bool = True,
    min_confidence: float = MIN_LLM_CONFIDENCE,
) -> ClusterResult:
    """Backbone (sync) + LLM assignment of the loose residual (async, fail-soft).
    Never raises on the LLM path — a failure degrades to backbone-only."""
    backbone = build_backbone(items)
    anchor_keys = {a.key for a in anchors}
    anchored = {e.item_id for e in backbone}
    loose = [it for it in items if it.id not in anchored]

    llm_rows: list[dict] = []
    if loose and anchors:
        try:
            llm_rows = await _assign_loose(loose, anchors, claude_path, use_cache)
        except Exception:
            llm_rows = []  # fail-soft: floor still renders from the backbone

    edges = merge_llm_edges(backbone, llm_rows, anchor_keys, {it.id for it in loose}, min_confidence)
    clusters, residual = group(edges, anchors, items)
    return ClusterResult(clusters=clusters, residual=residual, edges=edges)
