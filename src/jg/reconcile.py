"""Deterministic reconcile floor for the in-progress bucket.

Joins three sources by Jira key — declared (Jira status), actual (a live Claude
session in tmux), artifact (a PR) — and classifies each into a reconcile state.
The value is the mismatches: where declared, actual, and artifact disagree.

Pure logic, no I/O: the data-fetching + rendering layer feeds it lists. No LLM —
this is the correctness-critical floor everything else layers onto. See
docs/work-model.md → "In-progress as Claude Code sessions, and the reconcile".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_JIRA_KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")

# A session is "warm" if touched within this window. Matches the 1h prompt-cache
# TTL: past it, resuming re-processes the whole transcript uncached (~10x the
# input cost of a warm continue), which is why cold gets flagged distinctly.
WARM_SECONDS = 3600


class State(StrEnum):
    HEALTHY = "healthy"              # In-Progress + warm session — jump in
    COLD = "cold"                    # In-Progress + cold session — resume or fresh brief
    STALLED = "stalled"              # In-Progress + no session + no PR — nudge
    UNDECLARED = "undeclared"        # To-Do + live work — move ticket → In-Progress
    DONE_BUT_OPEN = "done_but_open"  # open ticket + merged PR — move ticket → Done
    RESOLVING = "resolving"          # In-Progress + open PR — belongs in Resolving
    UNTRACKED = "untracked"          # live session, no matching open ticket — file or leave
    TRACKED = "tracked"              # accounted for, nothing to flag


# States that represent a declared-vs-actual disagreement worth surfacing.
MISMATCH_STATES = frozenset(
    {State.COLD, State.STALLED, State.UNDECLARED, State.DONE_BUT_OPEN, State.RESOLVING, State.UNTRACKED}
)


def extract_key(text: str) -> str | None:
    """First Jira key in a string (pane title, PR branch), or None."""
    m = _JIRA_KEY_RE.search(text or "")
    return m.group(0) if m else None


@dataclass
class Session:
    """A live Claude/tmux pane. `title` is what jg set on spawn (the key, or
    `decompose·CH-36`, `escalate·#1543009`, or an ad-hoc label)."""
    title: str
    idle_seconds: float | None = None
    pane_id: str = ""

    @property
    def key(self) -> str | None:
        return extract_key(self.title)

    @property
    def warm(self) -> bool:
        return self.idle_seconds is not None and self.idle_seconds < WARM_SECONDS


@dataclass
class Ticket:
    key: str
    status: str            # display status ("In Progress", "Ready for Testing", …)
    status_category: str   # "To Do" | "In Progress" | "Done"


@dataclass
class PR:
    branch: str
    state: str             # "open" | "merged"
    title: str = ""
    url: str = ""

    @property
    def key(self) -> str | None:
        return extract_key(self.branch) or extract_key(self.title)


@dataclass
class ReconcileItem:
    state: State
    key: str | None = None
    jira_status: str | None = None
    session_warm: bool | None = None   # None = no session
    session_title: str | None = None
    pane_id: str = ""
    pr_state: str | None = None        # None | "open" | "merged"

    @property
    def is_mismatch(self) -> bool:
        return self.state in MISMATCH_STATES


def _classify(ticket: Ticket | None, session_warm: bool | None, pr_state: str | None) -> State:
    has_session = session_warm is not None

    # A key with a session/PR but not among my open tickets: live work off the
    # tracked graph (untracked) if there's a session; otherwise low-signal.
    if ticket is None:
        return State.UNTRACKED if has_session else State.TRACKED

    if pr_state == "merged":
        return State.DONE_BUT_OPEN

    cat = ticket.status_category
    if cat == "In Progress":
        if pr_state == "open":
            return State.RESOLVING
        if session_warm is True:
            return State.HEALTHY
        if session_warm is False:
            return State.COLD
        return State.STALLED

    if cat == "To Do" and (has_session or pr_state == "open"):
        return State.UNDECLARED

    return State.TRACKED


def reconcile(
    sessions: list[Session], tickets: list[Ticket], prs: list[PR]
) -> list[ReconcileItem]:
    """Join the three sources by Jira key and classify each into a state.

    Deterministic: key-matching + a state lookup, no inference. Sessions whose
    title carries no key surface as untracked items rather than being dropped —
    unjoined-but-visible beats confidently-mismatched."""
    sess_by_key: dict[str, list[Session]] = {}
    keyless: list[Session] = []
    for s in sessions:
        if s.key:
            sess_by_key.setdefault(s.key, []).append(s)
        else:
            keyless.append(s)

    pr_by_key: dict[str, list[PR]] = {}
    for p in prs:
        if p.key:
            pr_by_key.setdefault(p.key, []).append(p)

    tick_by_key = {t.key: t for t in tickets}

    items: list[ReconcileItem] = []
    for key in set(tick_by_key) | set(sess_by_key) | set(pr_by_key):
        ticket = tick_by_key.get(key)
        sess = sess_by_key.get(key, [])
        prs_k = pr_by_key.get(key, [])

        session_warm: bool | None = any(s.warm for s in sess) if sess else None
        if any(p.state == "merged" for p in prs_k):
            pr_state: str | None = "merged"
        elif any(p.state == "open" for p in prs_k):
            pr_state = "open"
        else:
            pr_state = None

        items.append(
            ReconcileItem(
                state=_classify(ticket, session_warm, pr_state),
                key=key,
                jira_status=ticket.status if ticket else None,
                session_warm=session_warm,
                session_title=sess[0].title if sess else None,
                pane_id=sess[0].pane_id if sess else "",
                pr_state=pr_state,
            )
        )

    for s in keyless:
        items.append(
            ReconcileItem(
                state=State.UNTRACKED,
                session_warm=s.warm,
                session_title=s.title,
                pane_id=s.pane_id,
            )
        )
    return items
