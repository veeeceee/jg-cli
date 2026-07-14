"""jg as a second client of the dialectical-coding framework.

Reads (and lightly updates) ~/.ai/progress.json — the same file that governs how
Claude scaffolds in a session. jg uses the per-pattern `level` to decide how much
of an orchestration gate to supply vs. demand: level 0 hands you the options,
level 3 makes you propose them. So the gate levels up as you master a pattern,
instead of going rote.

Reads are the core contract. `record_use` is best-effort and only bumps
uses/lastUsed — promotion (correct++/level++) stays with the reflection flow,
which is the only place a decision's correctness is actually known.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

PROGRESS_PATH = Path.home() / ".ai" / "progress.json"


def _load() -> dict | None:
    try:
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_level(pattern: str) -> int:
    """Scaffolding level (0-3) for a pattern; 0 if unknown or unreadable."""
    data = _load()
    if not data:
        return 0
    try:
        return int(data.get("patterns", {}).get(pattern, {}).get("level", 0))
    except (TypeError, ValueError):
        return 0


def record_use(pattern: str) -> None:
    """Best-effort: bump uses + lastUsed for a pattern. Never creates the file
    from scratch (jg shouldn't author the framework's canonical file), and never
    touches correct/level — that's the reflection flow's job."""
    data = _load()
    if data is None:
        return
    patterns = data.setdefault("patterns", {})
    entry = patterns.setdefault(pattern, {"level": 0, "uses": 0, "correct": 0, "lastUsed": ""})
    try:
        entry["uses"] = int(entry.get("uses", 0)) + 1
    except (TypeError, ValueError):
        entry["uses"] = 1
    entry["lastUsed"] = dt.date.today().isoformat()
    try:
        PROGRESS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass
