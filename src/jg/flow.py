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

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, ListItem, ListView, Static

from jg import reconcile as rec
from jg.config import Config

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


class FlowApp(App):
    CSS = """
    Screen { background: #16161e; }
    #title { padding: 0 1; color: #565f89; height: 1; }
    ListView { background: #16161e; }
    ListView > ListItem { background: #16161e; padding: 0 0; }
    ListView:focus > ListItem.--highlight { background: #1c1e2b; }
    """
    BINDINGS = [  # noqa: RUF012
        Binding("q", "quit", "quit"),
        Binding("r", "refresh", "refresh"),
    ]

    def __init__(self, config: Config):
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        yield Static("jg · my work — incoming / in-progress / resolving   (enter: jump/open · r: refresh · q: quit)", id="title")
        yield ListView(id="flow")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._refresh())

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
        item = ev.item
        if isinstance(item, _FlowRow):
            self._open_flow(item.item)
        elif isinstance(item, _IncomingRow) and item.item.url:
            webbrowser.open(item.item.url)
            self.notify(f"opened {item.item.label}", severity="information")

    def _open_flow(self, r: rec.ReconcileItem) -> None:
        """Jump to the live session if there is one; else open the ticket."""
        if r.pane_id:
            from jg import tmux

            tmux.select_pane(r.pane_id)
            self.notify(f"jumped to {r.key or 'session'} · pane {r.pane_id}", severity="information")
            return
        if r.key and self.config.default_cloud_url:
            webbrowser.open(f"{self.config.default_cloud_url.rstrip('/')}/browse/{r.key}")
            self.notify(f"opened {r.key}", severity="information")


def run_flow(config: Config) -> None:
    FlowApp(config).run()
