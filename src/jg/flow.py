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
import webbrowser
from dataclasses import dataclass
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, ListItem, ListView, Static

from jg import reconcile as rec
from jg.config import Config


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
    todo: list[Card]
    inprog: list[Card]
    resolving: list[Card]
    prs: list[Incoming]


async def gather_flow(config: Config) -> tuple[list[Incoming], list[rec.ReconcileItem], list[rec.ReconcileItem]]:
    """(incoming, in_progress, resolving) for my work. Each source fails soft."""
    items = await rec.gather(config)
    in_progress = [i for i in items if i.state in _IN_PROGRESS]
    resolving = [i for i in items if i.state in _RESOLVING]

    incoming: list[Incoming] = []
    try:
        from jg import github

        for pr in await asyncio.to_thread(github.review_requested_prs):
            repo = (pr.get("repository") or {}).get("nameWithOwner", "?")
            incoming.append(Incoming("review", f"{repo}#{pr.get('number')}", pr.get("title", ""), pr.get("url", "")))
    except Exception:
        pass
    try:
        from jg import zoho

        if config.zoho.is_setup:
            async with zoho.ZohoClient(config) as zc:
                for t in await zoho.find_involved(zc, config.zoho):
                    if (t.status or "").lower() != "closed":
                        incoming.append(Incoming("zoho", f"#{t.ticket_number}", t.subject, t.web_url))
    except Exception:
        pass

    return incoming, in_progress, resolving


async def gather_project(config: Config, project: object) -> ProjectView:
    """The per-plan dashboard data: board (To-Do / In-Progress / Resolving) with
    reconcile state on cards, done-count for health, and this plan's PRs."""
    from jg.api import JiraClient

    jql = getattr(project, "jql", "") or f"project = {config.default_project}"
    tickets: list[dict] = []
    try:
        async with JiraClient(config) as api:
            data = await api.search_jql(jql, fields=["summary", "status"], max_results=200)
        tickets = data.get("issues", [])
    except Exception:
        pass

    rec_items = await rec.gather(config)
    item_by_key = {i.key: i for i in rec_items if i.key}

    prs_raw: list[dict] = []
    try:
        from jg import github

        repos = set(getattr(project, "repos", None) or [])
        for pr in await asyncio.to_thread(github.my_open_prs):
            repo = (pr.get("repository") or {}).get("nameWithOwner", "")
            if not repos or repo in repos:
                prs_raw.append(pr)
    except Exception:
        pass
    pr_keys = {rec.extract_key(p.get("headRefName", "") or p.get("title", "")) for p in prs_raw}
    pr_keys.discard(None)

    todo: list[Card] = []
    inprog: list[Card] = []
    resolving: list[Card] = []
    done = 0
    total = 0
    for iss in tickets:
        f = iss.get("fields") or {}
        st = f.get("status") or {}
        cat = (st.get("statusCategory") or {}).get("name") or ""
        status = st.get("name", "")
        key = iss.get("key", "")
        total += 1
        if cat == "Done":
            done += 1
            continue
        item = item_by_key.get(key)
        card = Card(key, f.get("summary", "") or "", status, item.state if item else None, key in pr_keys, item)
        if cat == "To Do":
            todo.append(card)
        elif rec.is_resolving_status(status):
            resolving.append(card)
        else:
            inprog.append(card)

    prs = [
        Incoming("review", f"{(p.get('repository') or {}).get('nameWithOwner', '?')}#{p.get('number')}", p.get("title", ""), p.get("url", ""))
        for p in prs_raw
    ]
    return ProjectView(getattr(project, "name", "project"), done, total, todo, inprog, resolving, prs)


class _Header(ListItem):
    def __init__(self, text: str):
        super().__init__(Static(Text(text, style="bold #565f89")))


class _IncomingRow(ListItem):
    def __init__(self, item: Incoming):
        self.item = item
        glyph, style = ("⇄", "magenta") if item.kind == "review" else ("⛑", "orange3")
        line = Text("  ")
        line.append(f"{glyph} ", style=style)
        line.append(f"{item.label:<18}", style="bold #c0caf5")
        line.append(item.detail[:58], style="#a9b1d6")
        super().__init__(Static(line))


class _FlowRow(ListItem):
    def __init__(self, item: rec.ReconcileItem):
        self.item = item
        glyph, color, label = _STATE.get(item.state, (" ", "white", ""))
        line = Text("  ")
        line.append(f"{glyph} ", style=color)
        line.append(f"{item.key or '—':<9}", style="bold #c0caf5")
        line.append(f"{label:<11}", style=color)
        line.append((item.summary or item.session_title or "")[:50], style="#a9b1d6")
        super().__init__(Static(line))


class _ProjectCard(ListItem):
    def __init__(self, card: Card):
        self.card = card
        line = Text("")
        if card.state is not None and card.state in _STATE:
            g, color, _ = _STATE[card.state]
            line.append(f"{g} ", style=color)
        line.append(f"{card.key} ", style="bold #c0caf5")
        line.append(card.summary[:32], style="#a9b1d6")
        if card.has_pr:
            line.append("  ⎇", style="magenta")
        super().__init__(Static(line))


class FlowDetailModal(ModalScreen):
    """Reconcile detail + actions for a task. Dismisses True when a write happened
    (transition / start session) so the caller refreshes."""

    DEFAULT_CSS = """
    FlowDetailModal { align: center middle; background: #000000 85%; }
    FlowDetailModal #box { background: #1c1c1e; border: solid #565f89; width: 76; height: auto; padding: 1 2; }
    FlowDetailModal #det { height: auto; }
    FlowDetailModal #acts { color: #565f89; margin-top: 1; }
    """
    BINDINGS = [  # noqa: RUF012
        Binding("escape", "cancel", "close"),
        Binding("j", "jump", "jump"),
        Binding("s", "start", "start"),
        Binding("m", "move", "transition"),
        Binding("o", "open", "open ticket"),
    ]

    def __init__(self, config: Config, item: rec.ReconcileItem):
        super().__init__()
        self.config = config
        self.item = item

    def _target(self) -> str | None:
        if self.item.state == rec.State.UNDECLARED:
            return "In Progress"
        if self.item.state == rec.State.DONE_BUT_OPEN:
            return "Done"
        return None

    def compose(self) -> ComposeResult:
        i = self.item
        glyph, color, label = _STATE.get(i.state, (" ", "white", str(i.state)))
        det = Text()
        det.append(f"{i.key or '—'}  ", style="bold #ffffff")
        det.append(f"{i.summary}\n\n", style="#c0caf5")
        det.append("declared  ", style="#565f89")
        det.append(f"{i.jira_status or '—'}\n", style="#a9b1d6")
        det.append("actual    ", style="#565f89")
        det.append(f"{'warm session' if i.session_warm else 'cold session' if i.session_warm is False else 'no live session'}\n", style="#a9b1d6")
        det.append("artifact  ", style="#565f89")
        det.append(f"{('PR ' + i.pr_state) if i.pr_state else 'no PR'}\n", style="#a9b1d6")
        det.append("state     ", style="#565f89")
        det.append(f"{glyph} {label}", style=color)

        acts = Text()
        if i.pane_id:
            acts.append("[j] jump to session   ", style="#9ece6a")
        else:
            acts.append("[s] start session   ", style="#9ece6a")
        tgt = self._target()
        if tgt:
            acts.append(f"[m] → {tgt}   ", style="#bb9af7")
        acts.append("[o] open ticket   [esc] close", style="#565f89")

        with Vertical(id="box"):
            yield Static(det, id="det")
            yield Static(acts, id="acts")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_jump(self) -> None:
        if self.item.pane_id:
            from jg import tmux

            tmux.select_pane(self.item.pane_id)
            self.dismiss(None)
        else:
            self.app.notify("no live session for this ticket", severity="warning")

    def action_start(self) -> None:
        from jg import tmux

        if not tmux.in_tmux():
            self.app.notify("run inside tmux to start a session", severity="warning")
            return
        if not self.item.key:
            return
        cmd = f"{self.config.ai.claude_path} {self.config.ai.default_command} {self.item.key}"
        try:
            tmux.spawn(cmd, title=self.item.key, config=self.config.tmux)
        except RuntimeError as e:
            self.app.notify(str(e), severity="error")
            return
        self.app.notify(f"started session · {self.item.key}", severity="information")
        self.dismiss(True)

    async def action_move(self) -> None:
        tgt = self._target()
        if not tgt or not self.item.key:
            self.app.notify("no quick transition for this state", severity="warning")
            return
        from jg.api import JiraClient

        try:
            async with JiraClient(self.config) as api:
                trs = await api.get_transitions(self.item.key)
                match = next((t for t in trs if tgt.lower() in (t.get("name") or "").lower()), None)
                if match is None:
                    self.app.notify(f"no '{tgt}' transition available", severity="warning")
                    return
                await api.transition_issue(self.item.key, match["id"])
        except Exception as e:
            self.app.notify(f"transition failed: {type(e).__name__}", severity="error")
            return
        self.app.notify(f"{self.item.key} → {tgt}", severity="information")
        self.dismiss(True)

    def action_open(self) -> None:
        if self.item.key and self.config.default_cloud_url:
            webbrowser.open(f"{self.config.default_cloud_url.rstrip('/')}/browse/{self.item.key}")
        self.dismiss(None)


class ProjectScreen(Screen):
    """The per-plan dashboard lens: health + board (with reconcile on cards) + PRs.
    `w` opens the project workspace (docs/research); `esc` returns to the flow."""

    DEFAULT_CSS = """
    ProjectScreen { background: #16161e; }
    #proj-health { height: 1; padding: 0 1; }
    #proj-board { height: 1fr; }
    .pcol { width: 1fr; border-right: solid #2a2e42; }
    .pcol .ch { color: #565f89; padding: 0 1; height: 1; }
    .pcol ListView { background: #16161e; }
    .pcol ListView > ListItem { background: #16161e; padding: 0 1; }
    #proj-prs-h { color: #565f89; padding: 0 1; height: 1; }
    #proj-prs { height: auto; max-height: 6; background: #16161e; }
    #proj-prs > ListItem { background: #16161e; padding: 0; }
    """
    BINDINGS = [  # noqa: RUF012
        Binding("l", "focus_right", "→ column", show=True),
        Binding("h", "focus_left", "← column", show=False),
        Binding("right", "focus_right", "→ column", show=False),
        Binding("left", "focus_left", "← column", show=False),
        Binding("escape", "app.pop_screen", "back"),
        Binding("w", "workspace", "workspace"),
        Binding("r", "reload", "refresh"),
        Binding("q", "quit", "quit"),
    ]

    _COLS = (("todo", "TO DO"), ("inprog", "IN PROGRESS"), ("resolving", "RESOLVING"))
    _PANELS: ClassVar[list[str]] = ["#c-todo", "#c-inprog", "#c-resolving", "#proj-prs"]

    def action_focus_left(self) -> None:
        _shift_focus(self, self._PANELS, -1)

    def action_focus_right(self) -> None:
        _shift_focus(self, self._PANELS, 1)

    def __init__(self, project: object, config: Config):
        super().__init__()
        self.project = project
        self.config = config

    def compose(self) -> ComposeResult:
        yield Static("", id="proj-health")
        with Horizontal(id="proj-board"):
            for col, name in self._COLS:
                with Vertical(classes="pcol"):
                    yield Static(name, id=f"h-{col}", classes="ch")
                    yield ListView(id=f"c-{col}")
        yield Static("PRs", id="proj-prs-h")
        yield ListView(id="proj-prs")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load())

    async def action_reload(self) -> None:
        await self._load()

    async def _load(self) -> None:
        try:
            v = await gather_project(self.config, self.project)
        except Exception as e:
            self.notify(f"load failed: {type(e).__name__}", severity="error")
            return
        pct = round(100 * v.done / v.total) if v.total else 0
        self.query_one("#proj-health", Static).update(
            Text.assemble(
                (f"{v.name}  ", "bold #ffffff"),
                (f"{_bar(v.done, v.total)} ", "#9ece6a"),
                (f"{pct}%  ", "bold #c0caf5"),
                (f"{v.done}/{v.total} done", "#565f89"),
            )
        )
        first_filled: str | None = None
        for (col, name), cards in zip(self._COLS, (v.todo, v.inprog, v.resolving), strict=True):
            self.query_one(f"#h-{col}", Static).update(Text(f"{name}  {len(cards)}", style="#565f89"))
            lv = self.query_one(f"#c-{col}", ListView)
            await lv.clear()
            for c in cards:
                await lv.append(_ProjectCard(c))
            if cards and first_filled is None:
                first_filled = f"#c-{col}"
        prs = self.query_one("#proj-prs", ListView)
        await prs.clear()
        for p in v.prs:
            await prs.append(_IncomingRow(p))
        if first_filled:
            self.query_one(first_filled, ListView).focus()

    def action_workspace(self) -> None:
        from jg.tui import ProjectDetailModal

        self.app.push_screen(ProjectDetailModal(self.project, self.config))

    @on(ListView.Selected)
    def _sel(self, ev: ListView.Selected) -> None:
        item = ev.item
        if isinstance(item, _ProjectCard):
            c = item.card
            ri = c.rec_item or rec.ReconcileItem(rec.State.TRACKED, key=c.key, jira_status=c.status, summary=c.summary)
            self.app.push_screen(FlowDetailModal(self.config, ri), self._after)
        elif isinstance(item, _IncomingRow) and item.item.url:
            webbrowser.open(item.item.url)
            self.notify(f"opened {item.item.label}", severity="information")

    def _after(self, changed: object) -> None:
        if changed:
            self.run_worker(self._load())


class _RailItem(ListItem):
    def __init__(self, action: str, label: str, project: object = None, active: bool = False):
        self.action = action
        self.project = project
        t = Text("▸ " if active else "  ", style="#ff79c6")
        t.append(label, style="bold #ff79c6" if active else "#a9b1d6")
        super().__init__(Static(t))


class FlowApp(App):
    CSS = """
    Screen { background: #16161e; }
    #title { padding: 0 1; color: #565f89; height: 1; }
    #body { height: 1fr; }
    #scope { width: 18; border-right: solid #2a2e42; background: #14151d; }
    #scope > ListItem { background: #14151d; padding: 0 1; }
    #scope:focus > ListItem.--highlight { background: #1c1e2b; }
    #flow { width: 1fr; background: #16161e; }
    #flow > ListItem { background: #16161e; padding: 0; }
    #flow:focus > ListItem.--highlight { background: #1c1e2b; }
    """
    BINDINGS = [  # noqa: RUF012
        Binding("l", "focus_right", "→ list", show=True),
        Binding("h", "focus_left", "← scope", show=True),
        Binding("right", "focus_right", "→ list", show=False),
        Binding("left", "focus_left", "← scope", show=False),
        Binding("q", "quit", "quit"),
        Binding("r", "refresh", "refresh"),
    ]
    _PANELS: ClassVar[list[str]] = ["#scope", "#flow"]

    def __init__(self, config: Config):
        super().__init__()
        self.config = config

    def action_focus_left(self) -> None:
        _shift_focus(self, self._PANELS, -1)

    def action_focus_right(self) -> None:
        _shift_focus(self, self._PANELS, 1)

    def compose(self) -> ComposeResult:
        yield Static("jg · my work — incoming / in-progress / resolving   (h/l: panels · enter: jump/open · r: refresh · q: quit)", id="title")
        with Horizontal(id="body"):
            yield ListView(id="scope")
            yield ListView(id="flow")
        yield Footer()

    def on_mount(self) -> None:
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
        lv = self.query_one("#flow", ListView)
        await lv.clear()
        await lv.append(_Header(f"INCOMING ({len(incoming)})"))
        for i in incoming:
            await lv.append(_IncomingRow(i))
        await lv.append(_Header(f"IN PROGRESS ({len(in_progress)})"))
        for i in in_progress:
            await lv.append(_FlowRow(i))
        await lv.append(_Header(f"RESOLVING ({len(resolving)})"))
        for i in resolving:
            await lv.append(_FlowRow(i))
        if len(lv.children):
            lv.index = 0

    @on(ListView.Selected)
    def _selected(self, ev: ListView.Selected) -> None:
        if ev.list_view.id == "scope":
            self._rail(ev.item)
            return
        item = ev.item
        if isinstance(item, _FlowRow):
            self.push_screen(FlowDetailModal(self.config, item.item), self._after_detail)
        elif isinstance(item, _IncomingRow) and item.item.url:
            webbrowser.open(item.item.url)
            self.notify(f"opened {item.item.label}", severity="information")

    def _after_detail(self, changed: object) -> None:
        if changed:
            self.run_worker(self._refresh())

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
