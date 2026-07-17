"""jg flow — the my-work flow-home: incoming / in-progress / resolving.

Floor version (deterministic, no LLM): incoming = review requests + Zoho
involvement; in-progress + resolving = the reconcile output partitioned by
state. The backlog (To-Do with no activity) is deliberately excluded — incoming
is what arrived, not the standing backlog. Clustering and triage (the LLM
layers) reshape the incoming bucket later; this is the floor everything sits on.
See docs/work-model.md.
"""

from __future__ import annotations

import asyncio
import html
import re
import webbrowser
from dataclasses import dataclass, field
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, ListItem, ListView, Static

from jg import cluster as cl
from jg import reconcile as rec
from jg.config import Config
from jg.themes import ALL_THEMES


def _strip_html(s: str) -> str:
    s = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", s or "", flags=re.I | re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


# Zoho stores @mentions inline as `zsu[@user:{zuid}]zsu`. Render them human.
_MENTION_RE = re.compile(r"zsu\[@user:(\d+)\]zsu")
# Start of a quoted reply-chain / signature block appended to a Desk email.
_QUOTE_RE = re.compile(r"From:\s|Sent:\s|-{2,}\s*Original Message|On .{0,80}? wrote:", re.I)


def _humanize_mentions(s: str, my_zuids: set[str]) -> str:
    return _MENTION_RE.sub(lambda m: "@you" if m.group(1) in my_zuids else "@mention", s or "")


def _trim_quoted(s: str) -> str:
    """Drop a trailing quoted reply-chain so only the new message shows.
    Conservative: only trims when real content precedes the quote marker."""
    m = _QUOTE_RE.search(s or "")
    if m and m.start() > 40:
        return s[: m.start()].rstrip()
    return s or ""


def _addr_name(addr: str) -> str:
    """`"Heather Duplessis"<heather@x.com>` → `Heather Duplessis`."""
    m = re.match(r'\s*"?([^"<]+?)"?\s*<', addr or "")
    return (m.group(1).strip() if m else (addr or "").strip()) or "—"


def _bar(done: int, total: int, width: int = 16) -> str:
    filled = round(width * done / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def _shift_focus(screen, ids: list[str], delta: int) -> None:
    """Move focus across a fixed set of ListViews (panel navigation)."""
    panels = [screen.query_one(i, ListView) for i in ids]
    cur = next((idx for idx, p in enumerate(panels) if p.has_focus), 0)
    nxt = panels[(cur + delta) % len(panels)]
    if nxt.index is None and len(nxt.children):
        nxt.index = 0
    nxt.focus()

# reconcile states, partitioned into the two flow buckets (tracked = backlog,
# excluded from the flow-home).
_IN_PROGRESS = {rec.State.HEALTHY, rec.State.COLD, rec.State.STALLED, rec.State.UNDECLARED, rec.State.UNTRACKED}
_RESOLVING = {rec.State.RESOLVING, rec.State.DONE_BUT_OPEN}

_STATE = {
    rec.State.HEALTHY: ("●", "green", "healthy"),
    rec.State.COLD: ("◐", "yellow", "cold"),
    rec.State.STALLED: ("▲", "red", "stalled"),
    rec.State.UNDECLARED: ("◑", "magenta", "undeclared"),
    rec.State.RESOLVING: ("◔", "cyan", "resolving"),
    rec.State.DONE_BUT_OPEN: ("✓", "green", "done→open"),
    rec.State.UNTRACKED: ("◍", "orange3", "untracked"),
    rec.State.TRACKED: (" ", "dim", ""),
}


@dataclass
class Incoming:
    kind: str      # "review" | "zoho"
    label: str
    detail: str
    url: str = ""
    ref: str = ""  # zoho ticket id (for the detail modal)
    keys: list[str] = field(default_factory=list)  # authored Jira links (branch / Zoho field)

    @property
    def cid(self) -> str:
        """Stable id used to match cluster results back to this row."""
        return f"{self.kind}:{self.ref or self.label}"


@dataclass
class Card:
    key: str
    summary: str
    status: str
    state: rec.State | None = None
    has_pr: bool = False
    rec_item: rec.ReconcileItem | None = None


@dataclass
class ProjectView:
    name: str
    done: int
    total: int
    columns: list[tuple[str, list[Card]]]  # (status group, cards) in GROUP_ORDER — current sprint
    prs: list[Incoming]


async def gather_flow(config: Config) -> tuple[list[Incoming], list[rec.ReconcileItem], list[rec.ReconcileItem]]:
    """(incoming, in_progress, resolving) for my work. The three sources
    (reconcile, review-requested PRs, Zoho involvement) are independent and run
    concurrently — Zoho dominates, so serializing them made the whole load wait
    on it. Each source fails soft."""

    async def _review_prs() -> list[Incoming]:
        from jg import github

        out: list[Incoming] = []
        for pr in await asyncio.to_thread(github.review_requested_prs):
            repo = (pr.get("repository") or {}).get("nameWithOwner", "?")
            key = rec.extract_key(pr.get("headRefName", "") or "") or rec.extract_key(pr.get("title", "") or "")
            out.append(
                Incoming("review", f"{repo}#{pr.get('number')}", pr.get("title", ""), pr.get("url", ""),
                         keys=[key] if key else [])
            )
        return out

    async def _zoho() -> list[Incoming]:
        from jg import zoho

        if not config.zoho.is_setup:
            return []
        async with zoho.ZohoClient(config) as zc:
            involved = await zoho.find_involved(zc, config.zoho)
        return [
            Incoming("zoho", f"#{t.ticket_number}", t.subject, t.web_url, ref=t.id, keys=list(t.jira_keys))
            for t in involved
            if (t.status or "").lower() != "closed"
        ]

    items_r, review_r, zoho_r = await asyncio.gather(
        rec.gather(config), _review_prs(), _zoho(), return_exceptions=True
    )
    items = items_r if isinstance(items_r, list) else []
    in_progress = [i for i in items if i.state in _IN_PROGRESS]
    resolving = [i for i in items if i.state in _RESOLVING]
    incoming = (review_r if isinstance(review_r, list) else []) + (zoho_r if isinstance(zoho_r, list) else [])
    return incoming, in_progress, resolving


async def gather_flow_anchors(config: Config) -> list[cl.Anchor]:
    """My open Jira tickets, as clustering anchors for the incoming pile."""
    from jg.api import JiraClient

    try:
        async with JiraClient(config) as api:
            data = await api.search_jql(
                "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC",
                fields=["summary"],
                max_results=100,
            )
    except Exception:
        return []
    return [
        cl.Anchor(i.get("key", ""), (i.get("fields") or {}).get("summary", "") or "")
        for i in data.get("issues", [])
    ]


async def gather_project(config: Config, project: object, sprint_id: int | None = None) -> ProjectView:
    """Board for a sprint grouped by the real workflow columns (render.GROUP_ORDER
    via normalize_status), reconcile state on cards, sprint completion for health,
    and this plan's PRs. `sprint_id=None` → the open sprint; otherwise that sprint.

    The four sources (sprint tickets, open PRs, merged PRs, sessions) are fetched
    concurrently, then the *sprint* tickets are reconciled directly — so we avoid
    rec.gather's redundant my-open-tickets search and fetch PRs only once."""
    from jg import github, render, tmux
    from jg.api import JiraClient

    base = getattr(project, "jql", "") or f"project = {config.default_project}"
    scope = f"sprint = {sprint_id}" if sprint_id else "sprint in openSprints()"
    jql = f"({base}) AND {scope}"

    async def _tickets() -> dict:
        async with JiraClient(config) as api:
            return await api.search_jql(jql, fields=["summary", "status"], max_results=200)

    data_r, open_r, merged_r, panes_r = await asyncio.gather(
        _tickets(),
        asyncio.to_thread(github.my_open_prs),
        asyncio.to_thread(github.my_recent_merged_prs),
        asyncio.to_thread(tmux.list_panes),
        return_exceptions=True,
    )
    issues = (data_r.get("issues", []) if isinstance(data_r, dict) else [])
    open_prs = open_r if isinstance(open_r, list) else []
    merged_prs = merged_r if isinstance(merged_r, list) else []
    panes = panes_r if isinstance(panes_r, list) else []

    # Reconcile the sprint tickets against live sessions + PRs (pure core, no I/O).
    sessions = [
        rec.Session(title=p.title, idle_seconds=p.idle_seconds, pane_id=p.pane_id, jg_key=p.jg_key)
        for p in panes if p.jg_key
    ]
    rec_tickets = []
    for iss in issues:
        f = iss.get("fields") or {}
        st = f.get("status") or {}
        cat = (st.get("statusCategory") or {}).get("name") or ""
        rec_tickets.append(rec.Ticket(iss.get("key", ""), st.get("name", ""), cat, f.get("summary", "") or ""))
    rec_prs = [rec.PR(p.get("headRefName", "") or "", "open", p.get("title", "") or "", p.get("url", "") or "") for p in open_prs]
    rec_prs += [rec.PR(p.get("headRefName", "") or "", "merged", p.get("title", "") or "", p.get("url", "") or "") for p in merged_prs]
    item_by_key = {i.key: i for i in rec.reconcile(sessions, rec_tickets, rec_prs) if i.key}

    repos = set(getattr(project, "repos", None) or [])
    prs_raw = [p for p in open_prs if not repos or (p.get("repository") or {}).get("nameWithOwner", "") in repos]
    pr_keys = {rec.extract_key(p.get("headRefName", "") or p.get("title", "")) for p in prs_raw}
    pr_keys.discard(None)

    groups: dict[str, list[Card]] = {}
    done = 0
    total = 0
    for iss in issues:
        f = iss.get("fields") or {}
        st = f.get("status") or {}
        cat = (st.get("statusCategory") or {}).get("name") or ""
        status = st.get("name", "")
        key = iss.get("key", "")
        total += 1
        if cat == "Done":
            done += 1
        item = item_by_key.get(key)
        card = Card(key, f.get("summary", "") or "", status, item.state if item else None, key in pr_keys, item)
        groups.setdefault(render.normalize_status(status), []).append(card)

    ordered = [g for g in render.GROUP_ORDER if g in groups] + [g for g in groups if g not in render.GROUP_ORDER]
    columns = [(g, groups[g]) for g in ordered]

    prs = [
        Incoming("review", f"{(p.get('repository') or {}).get('nameWithOwner', '?')}#{p.get('number')}", p.get("title", ""), p.get("url", ""))
        for p in prs_raw
    ]
    return ProjectView(getattr(project, "name", "project"), done, total, columns, prs)


async def gather_backlog(config: Config, project: object) -> list[Card]:
    """The plan's ranked backlog — not-done tickets outside the open sprint."""
    from jg.api import JiraClient

    base = getattr(project, "jql", "") or f"project = {config.default_project}"
    jql = f"({base}) AND statusCategory != Done AND (sprint is EMPTY OR sprint not in openSprints()) ORDER BY priority DESC, created ASC"
    try:
        async with JiraClient(config) as api:
            data = await api.search_jql(jql, fields=["summary", "status", "priority"], max_results=200)
    except Exception:
        return []
    out: list[Card] = []
    for iss in data.get("issues", []):
        f = iss.get("fields") or {}
        st = f.get("status") or {}
        out.append(Card(iss.get("key", ""), f.get("summary", "") or "", st.get("name", "")))
    return out


class _Header(ListItem):
    def __init__(self, text: str):
        super().__init__(Static(Text(text, style="bold #565f89")))


class _ClusterHead(ListItem):
    """Sub-header inside INCOMING: the Jira anchor a group of items belongs to."""
    def __init__(self, anchor_key: str, summary: str):
        t = Text("  ↳ ", style="#7dcfff")
        t.append(f"{anchor_key} ", style="bold #7dcfff")
        t.append(summary[:46], style="#565f89")
        super().__init__(Static(t))


class _IncomingRow(ListItem):
    def __init__(self, item: Incoming, edge: cl.Edge | None = None):
        self.item = item
        glyph, style = ("⇄", "magenta") if item.kind == "review" else ("⛑", "orange3")
        # nested under a cluster head → indent + dim edge bar; else the normal bar
        line = Text("      " if edge else "▎ ", style="#3d3d52" if edge else style)
        line.append(f"{glyph} ", style=style)
        line.append(f"{item.label:<18}", style="bold #c0caf5")
        line.append(item.detail[: 40 if edge else 58], style="#a9b1d6")
        if edge is not None:
            soft = edge.kind == cl.EdgeKind.LLM
            line.append(f"  {'~' if soft else '='}", style="#e0af68" if soft else "#9ece6a")
            if soft:
                line.append(f"{edge.confidence:.1f}", style="#565f89")
        super().__init__(Static(line))


class _FlowRow(ListItem):
    def __init__(self, item: rec.ReconcileItem):
        self.item = item
        glyph, color, label = _STATE.get(item.state, (" ", "white", ""))
        line = Text("▎ ", style=color)
        line.append(f"{glyph} ", style=color)
        line.append(f"{item.key or '—':<9}", style="bold #c0caf5")
        line.append(f"{label:<11}", style=color)
        line.append((item.summary or item.session_title or "")[:50], style="#a9b1d6")
        super().__init__(Static(line))


class _ProjectCard(ListItem):
    def __init__(self, card: Card):
        self.card = card
        edge = _STATE[card.state][1] if (card.state is not None and card.state in _STATE) else "#3d3d52"
        line = Text("▎", style=edge)
        if card.state is not None and card.state in _STATE:
            g, color, _ = _STATE[card.state]
            line.append(f" {g}", style=color)
        line.append(f" {card.key} ", style="bold #c0caf5")
        line.append(card.summary[:32], style="#a9b1d6")
        if card.has_pr:
            line.append("  ⎇", style="magenta")
        super().__init__(Static(line))


class ZohoDetailModal(ModalScreen[None]):
    """Full Zoho support ticket — status, thread, comments. Read-only."""

    DEFAULT_CSS = """
    ZohoDetailModal { align: center middle; background: #000000 85%; }
    ZohoDetailModal #zbox { background: #1c1c1e; border: solid #ff9e64; width: 84; height: 80%; padding: 1 2; }
    """
    BINDINGS = [  # noqa: RUF012
        Binding("escape", "close", "close"),
        Binding("q", "close", "close"),
        Binding("o", "browser", "open in browser"),
    ]

    def __init__(self, config: Config, ticket_id: str, subject: str, url: str = ""):
        super().__init__()
        self.config = config
        self.ticket_id = ticket_id
        self.subject = subject
        self.url = url

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="zbox"):
            yield Static("loading…", id="zbody")

    def on_mount(self) -> None:
        self.run_worker(self._load())

    async def _load(self) -> None:
        from jg import zoho

        try:
            async with zoho.ZohoClient(self.config) as zc:
                t = await zc.get_ticket(self.ticket_id)
                # `conversations` is the unified timeline — threads AND comments
                # interleaved. Fetching comments separately double-renders them.
                convs = await zc.conversations(self.ticket_id)
                ident = await zoho.resolve_identity(zc, self.config.zoho.agent_emails)
                # Threads only carry a truncated summary in the list; fetch each
                # full body concurrently so the whole message shows.
                tids = [c["id"] for c in convs if c.get("type") != "comment" and c.get("id")]
                fulls = await asyncio.gather(*(zc.thread(self.ticket_id, tid) for tid in tids))
        except Exception as e:
            self.query_one("#zbody", Static).update(Text(f"failed to load: {type(e).__name__}", style="red"))
            return
        my_zuids = {v["zuid"] for v in ident.values() if v.get("zuid")}
        full_by_id = {f.get("id"): f for f in fulls if f}

        body = Text()
        body.append(f"#{t.get('ticketNumber', '')}  {t.get('subject') or self.subject}\n", style="bold #ffffff")
        contact = t.get("contact") or {}
        who = f"{contact.get('firstName') or ''} {contact.get('lastName') or ''}".strip() or t.get("email", "")
        body.append(f"{t.get('status', '')}  ·  {t.get('channel', '')}  ·  {who}\n\n", style="#565f89")
        desc = _trim_quoted(_humanize_mentions(_strip_html(t.get("description", "")), my_zuids))
        if desc:
            body.append(desc + "\n\n", style="#a9b1d6")

        for c in convs:
            if c.get("type") == "comment":
                cm = c.get("commenter") or {}
                name = cm.get("name") or f"{cm.get('firstName', '')} {cm.get('lastName', '')}".strip() or "agent"
                body.append(f"» {name} · comment\n", style="#bb9af7")
                txt = _humanize_mentions(_strip_html(c.get("content", "")), my_zuids)
                body.append(txt + "\n\n", style="#c0caf5")
            else:
                glyph = "→" if c.get("direction") == "out" else "←"
                body.append(f"{glyph} {_addr_name(c.get('fromEmailAddress') or c.get('from') or '')}\n", style="#7aa2f7")
                raw = full_by_id.get(c.get("id"), {}).get("content") or c.get("summary") or ""
                txt = _trim_quoted(_humanize_mentions(_strip_html(raw), my_zuids))
                body.append(txt + "\n\n", style="#a9b1d6")
        self.query_one("#zbody", Static).update(body)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_browser(self) -> None:
        if self.url:
            webbrowser.open(self.url)


def _open_incoming(app: App, inc: Incoming, config: Config) -> None:
    """Open a PR or Zoho ticket as a modal (browser fallback)."""
    if inc.kind == "review" and inc.url:
        repo, _, num = inc.label.rpartition("#")
        if num.isdigit():
            from jg.tui import PRDetailModal

            app.push_screen(PRDetailModal(repo=repo, number=int(num), url=inc.url, config=config))
            return
    if inc.kind == "zoho" and inc.ref:
        app.push_screen(ZohoDetailModal(config, inc.ref, inc.detail, inc.url))
        return
    if inc.url:
        webbrowser.open(inc.url)


def _col_id(group: str) -> str:
    """Stable DOM id for a status-group column."""
    return "c-" + re.sub(r"[^a-z0-9]+", "-", group.lower()).strip("-")


def _row_identity(row: object) -> tuple[str, str] | None:
    """A stable token for a flow row, so selection survives a list re-render."""
    if isinstance(row, _FlowRow):
        return ("flow", row.item.key or row.item.session_title or "")
    if isinstance(row, _IncomingRow):
        return ("inc", row.item.cid)
    return None


class ProjectScreen(Screen):
    """The per-plan dashboard lens: health + current-sprint board (real workflow
    columns, reconcile on cards) + PRs. `b` toggles to the ranked backlog list.
    `w` opens the project workspace; `esc` returns to the flow."""

    DEFAULT_CSS = """
    ProjectScreen { background: $background; }
    #proj-health { height: 1; padding: 0 1; }
    #proj-board { height: 1fr; padding: 0 1; }
    .pcol { width: 1fr; min-width: 22; }
    .pcol ListView { height: 1fr; background: $surface; }
    .pcol ListView > ListItem { background: $surface; padding: 0 1; }
    #proj-backlog { height: 1fr; background: $surface; }
    #proj-backlog > ListItem { background: $surface; padding: 0 1; }
    #proj-prs-h { color: $text-muted; padding: 0 1; height: 1; }
    #proj-prs { height: auto; max-height: 6; background: $surface; }
    #proj-prs > ListItem { background: $surface; padding: 0; }
    """
    BINDINGS = [  # noqa: RUF012
        Binding("l", "focus_right", "→ column", show=True),
        Binding("h", "focus_left", "← column", show=False),
        Binding("right", "focus_right", "→ column", show=False),
        Binding("left", "focus_left", "← column", show=False),
        Binding("b", "toggle", "sprint ⇄ backlog", show=True),
        Binding("s", "select", "pick sprint/backlog", show=True),
        Binding("g", "jump", "jump to session", show=True),
        Binding("escape", "app.pop_screen", "back"),
        Binding("w", "workspace", "workspace"),
        Binding("r", "reload", "refresh"),
        Binding("q", "quit", "quit"),
    ]

    def _panels(self) -> list[ListView]:
        """Focusable lists in DOM order — board columns (or backlog) then PRs."""
        board = self.query_one("#proj-board", Horizontal)
        lists = list(board.query(ListView))
        lists.extend(self.query("#proj-prs").results(ListView))
        return lists

    def _shift(self, delta: int) -> None:
        panels = self._panels()
        if not panels:
            return
        cur = self.app.focused
        idx = panels.index(cur) if cur in panels else 0
        panels[(idx + delta) % len(panels)].focus()

    def action_focus_left(self) -> None:
        self._shift(-1)

    def action_focus_right(self) -> None:
        self._shift(1)

    def __init__(self, project: object, config: Config):
        super().__init__()
        self.project = project
        self.config = config
        self.view = "sprint"                 # "sprint" | "backlog"
        self.sprint_id: int | None = None    # None → the open sprint
        self.sprint_name = "open sprint"
        self._sprints: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Static("", id="proj-health")
        yield Horizontal(id="proj-board")
        yield Static("PRs", id="proj-prs-h")
        yield ListView(id="proj-prs")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load())

    async def action_reload(self) -> None:
        await self._load()

    async def action_toggle(self) -> None:
        self.view = "backlog" if self.view == "sprint" else "sprint"
        await self._load()

    async def action_select(self) -> None:
        """Pick any available sprint (or the backlog) via a modal selector."""
        board_id = getattr(self.project, "board_id", "")
        if not board_id:
            self.notify("no board_id for this project — add it to config", severity="warning")
            return
        from jg.api import JiraClient

        try:
            async with JiraClient(self.config) as api:
                sprints = await api.get_sprints(board_id, state="active,future,closed")
        except Exception as e:
            self.notify(f"sprints unavailable: {type(e).__name__}", severity="error")
            return
        # active + future in API order, then the most-recent closed (bounded — a
        # board can have dozens of closed sprints).
        active_future = [s for s in sprints if (s.get("state") or "").lower() != "closed"]
        closed = sorted(
            (s for s in sprints if (s.get("state") or "").lower() == "closed"),
            key=lambda s: s.get("id", 0),
            reverse=True,
        )[:8]
        self._sprints = active_future + closed
        from jg.tui import SprintPickerModal

        self.app.push_screen(
            SprintPickerModal("", self._sprints, title="View sprint or backlog", hint="enter to view · esc to cancel"),
            self._on_pick,
        )

    def _on_pick(self, result: object) -> None:
        if not result:
            return
        kind, sid = result  # ("sprint", id) | ("backlog", None)
        if kind == "backlog":
            self.view = "backlog"
        else:
            self.view = "sprint"
            self.sprint_id = sid
            self.sprint_name = next(
                (s.get("name", "sprint") for s in self._sprints if int(s.get("id", -1)) == sid), "sprint"
            )
        self.run_worker(self._load())

    async def _load(self) -> None:
        from jg.tui import GROUP_GRADIENT, GradientPanel

        board = self.query_one("#proj-board", Horizontal)
        await board.remove_children()

        if self.view == "backlog":
            try:
                cards = await gather_backlog(self.config, self.project)
            except Exception as e:
                self.notify(f"load failed: {type(e).__name__}", severity="error")
                return
            self.query_one("#proj-health", Static).update(
                Text.assemble(
                    (f"{getattr(self.project, 'name', 'project')}  ", "bold #ffffff"),
                    ("backlog  ", "bold #ff9e64"),
                    (f"{len(cards)} items", "#565f89"),
                )
            )
            lv = ListView(id="proj-backlog")
            await board.mount(GradientPanel(lv, panel_title="BACKLOG", classes="pcol"))
            for c in cards:
                await lv.append(_ProjectCard(c))
            lv.focus()
            return

        try:
            v = await gather_project(self.config, self.project, self.sprint_id)
        except Exception as e:
            self.notify(f"load failed: {type(e).__name__}", severity="error")
            return
        pct = round(100 * v.done / v.total) if v.total else 0
        self.query_one("#proj-health", Static).update(
            Text.assemble(
                (f"{v.name}  ", "bold #ffffff"),
                (f"{self.sprint_name}  ", "bold #7aa2f7"),
                (f"{_bar(v.done, v.total)} ", "#9ece6a"),
                (f"{pct}%  ", "bold #c0caf5"),
                (f"{v.done}/{v.total} done", "#565f89"),
            )
        )
        first_filled: ListView | None = None
        for group, cards in v.columns:
            lv = ListView(id=_col_id(group))
            stops = list(GROUP_GRADIENT.get(group, [])) or None
            await board.mount(GradientPanel(lv, panel_title=f"{group} · {len(cards)}", stops=stops, classes="pcol"))
            for c in cards:
                await lv.append(_ProjectCard(c))
            if cards and first_filled is None:
                first_filled = lv
        prs = self.query_one("#proj-prs", ListView)
        await prs.clear()
        for p in v.prs:
            await prs.append(_IncomingRow(p))
        if first_filled is not None:
            first_filled.focus()

    def action_workspace(self) -> None:
        from jg.tui import ProjectDetailModal

        self.app.push_screen(ProjectDetailModal(self.project, self.config))

    @on(ListView.Selected)
    def _sel(self, ev: ListView.Selected) -> None:
        item = ev.item
        if isinstance(item, _ProjectCard) and item.card.key:
            from jg.tui import TicketDetailModal

            self.app.push_screen(TicketDetailModal(item.card.key, self.config), self._after)
        elif isinstance(item, _IncomingRow):
            _open_incoming(self.app, item.item, self.config)

    def _after(self, changed: object) -> None:
        # only pay the board reload if the ticket modal actually mutated something
        if changed:
            self.run_worker(self._load())

    def action_jump(self) -> None:
        f = self.app.focused
        r = None
        if isinstance(f, ListView):
            it = f.highlighted_child
            if isinstance(it, _ProjectCard):
                r = it.card.rec_item
        if r is not None and r.pane_id:
            from jg import tmux

            tmux.select_pane(r.pane_id)
            self.app.notify(f"jumped to {r.key} · pane {r.pane_id}", severity="information")
        else:
            self.app.notify("no live session for the selected card", severity="warning")


class _RailItem(ListItem):
    def __init__(self, action: str, label: str, project: object = None, active: bool = False):
        self.action = action
        self.project = project
        t = Text("▸ " if active else "  ", style="#ff79c6")
        t.append(label, style="bold #ff79c6" if active else "#a9b1d6")
        super().__init__(Static(t))


class FlowApp(App):
    CSS = """
    Screen { background: $background; }
    #title { padding: 0 1; color: $text-muted; height: 1; }
    #body { height: 1fr; padding: 0 1; }
    #scope-panel { width: 22; }
    #flow-panel { width: 1fr; }
    #scope, #flow { height: 1fr; background: $surface; }
    #scope > ListItem, #flow > ListItem { background: $surface; padding: 0 1; }
    #scope:focus > ListItem.--highlight,
    #flow:focus > ListItem.--highlight { background: $primary 25%; }
    """
    BINDINGS = [  # noqa: RUF012
        Binding("l", "focus_right", "→ list", show=True),
        Binding("h", "focus_left", "← scope", show=True),
        Binding("right", "focus_right", "→ list", show=False),
        Binding("left", "focus_left", "← scope", show=False),
        Binding("g", "jump", "jump to session", show=True),
        Binding("q", "quit", "quit"),
        Binding("r", "refresh", "refresh"),
    ]
    _PANELS: ClassVar[list[str]] = ["#scope", "#flow"]

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self._incoming: list[Incoming] = []
        self._ip: list[rec.ReconcileItem] = []
        self._res: list[rec.ReconcileItem] = []

    def action_focus_left(self) -> None:
        _shift_focus(self, self._PANELS, -1)

    def action_focus_right(self) -> None:
        _shift_focus(self, self._PANELS, 1)

    def compose(self) -> ComposeResult:
        from jg.tui import GradientPanel

        yield Static("jg · my work — incoming / in-progress / resolving   (h/l: panels · enter: jump/open · r: refresh · q: quit)", id="title")
        with Horizontal(id="body"):
            yield GradientPanel(ListView(id="scope"), panel_title="LENS", id="scope-panel")
            yield GradientPanel(ListView(id="flow"), panel_title="MY WORK", id="flow-panel")
        yield Footer()

    def on_mount(self) -> None:
        for t in ALL_THEMES:
            try:
                self.register_theme(t)
            except Exception:
                pass
        try:
            self.theme = self.config.ui.theme
        except Exception:
            pass
        self.run_worker(self._init())

    async def _init(self) -> None:
        rail = self.query_one("#scope", ListView)
        await rail.append(_RailItem("mywork", "my work", active=True))
        for p in self.config.projects:
            await rail.append(_RailItem("plan", p.name, project=p))
        await rail.append(_RailItem("roadmap", "roadmap"))
        await self._refresh()
        self.query_one("#flow", ListView).focus()

    async def action_refresh(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        try:
            incoming, in_progress, resolving = await gather_flow(self.config)
        except Exception as e:
            self.notify(f"refresh failed: {type(e).__name__}", severity="error")
            return
        self._incoming, self._ip, self._res = incoming, in_progress, resolving
        await self._render_flow()                    # deterministic floor — instant
        self.run_worker(self._cluster_overlay())     # LLM grouping — arrives later

    async def _render_flow(
        self, clusters: list[cl.Cluster] | None = None, residual: list[cl.Item] | None = None
    ) -> None:
        """Render the flow list. Floor: incoming flat. Overlay: incoming grouped
        under cluster anchors, residual flat. In-progress/resolving unchanged.

        Preserves the highlighted row across a re-render (by identity, not index)
        so the async cluster overlay landing doesn't yank the cursor to the top."""
        lv = self.query_one("#flow", ListView)
        prev = _row_identity(lv.highlighted_child)
        await lv.clear()
        await lv.append(_Header(f"INCOMING ({len(self._incoming)})"))
        if clusters is None:
            for i in self._incoming:
                await lv.append(_IncomingRow(i))
        else:
            by_cid = {i.cid: i for i in self._incoming}
            for c in clusters:
                await lv.append(_ClusterHead(c.anchor_key, c.summary))
                for m in c.members:
                    inc = by_cid.get(m.id)
                    if inc is not None:
                        await lv.append(_IncomingRow(inc, edge=m.edge))
            for it in residual or []:
                inc = by_cid.get(it.id)
                if inc is not None:
                    await lv.append(_IncomingRow(inc))
        await lv.append(_Header(f"IN PROGRESS ({len(self._ip)})"))
        for i in self._ip:
            await lv.append(_FlowRow(i))
        await lv.append(_Header(f"RESOLVING ({len(self._res)})"))
        for i in self._res:
            await lv.append(_FlowRow(i))
        if not len(lv.children):
            return
        restored = 0
        if prev is not None:
            for i, child in enumerate(lv.children):
                if _row_identity(child) == prev:
                    restored = i
                    break
        lv.index = restored

    async def _cluster_overlay(self) -> None:
        """Async LLM grouping of the incoming pile. Fail-soft: on any problem or
        empty result the deterministic floor stands untouched."""
        incoming = self._incoming
        if not incoming:
            return
        items = [
            cl.Item(
                id=i.cid,
                kind="pr" if i.kind == "review" else "zoho",
                label=i.label,
                detail=i.detail,
                linked_keys=i.keys,
            )
            for i in incoming
        ]
        anchors = await gather_flow_anchors(self.config)
        if not anchors:
            return
        try:
            result = await cl.enrich(items, anchors, claude_path=self.config.ai.claude_path)
        except Exception:
            return
        # Only re-render if the incoming pile hasn't changed under us, and only
        # if the LLM actually grouped something.
        if result.clusters and self._incoming is incoming:
            await self._render_flow(result.clusters, result.residual)

    @on(ListView.Selected)
    def _selected(self, ev: ListView.Selected) -> None:
        if ev.list_view.id == "scope":
            self._rail(ev.item)
            return
        item = ev.item
        if isinstance(item, _FlowRow) and item.item.key:
            from jg.tui import TicketDetailModal

            self.push_screen(TicketDetailModal(item.item.key, self.config), self._after_detail)
        elif isinstance(item, _IncomingRow):
            _open_incoming(self, item.item, self.config)

    def _after_detail(self, changed: object) -> None:
        # re-reconcile only if the ticket modal mutated something
        if changed:
            self.run_worker(self._refresh())

    def _focused_recitem(self) -> rec.ReconcileItem | None:
        f = self.focused
        if isinstance(f, ListView):
            it = f.highlighted_child
            if isinstance(it, _FlowRow):
                return it.item
        return None

    def action_jump(self) -> None:
        r = self._focused_recitem()
        if r is not None and r.pane_id:
            from jg import tmux

            tmux.select_pane(r.pane_id)
            self.notify(f"jumped to {r.key or 'session'} · pane {r.pane_id}", severity="information")
        else:
            self.notify("no live session for the selected item", severity="warning")

    def _rail(self, item: ListItem) -> None:
        if not isinstance(item, _RailItem):
            return
        if item.action == "plan" and item.project is not None:
            self.push_screen(ProjectScreen(item.project, self.config))
        elif item.action == "roadmap":
            from jg.tui import RoadmapModal

            self.push_screen(RoadmapModal(self.config))
        else:
            self.query_one("#flow", ListView).focus()

def run_flow(config: Config) -> None:
    FlowApp(config).run()
