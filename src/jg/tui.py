"""Textual TUI dashboard for ch.

Layout:
  ┌─ ch dashboard ────────────────────────────────────────────────┐
  │ ╭─[search]──────────────────────────────────────────────────╮ │
  │ │ optional filter input (toggled with / )                   │ │
  │ ╰───────────────────────────────────────────────────────────╯ │
  │ ╭─Kanban─────────────────────────╮ ╭─PRs──────────────────╮  │
  │ │ ToDo│Prog│Rev │Test│Ready│Done │ │ Mine | Waiting       │  │
  │ │     │    │    │    │     │     │ │ ─────                │  │
  │ ╰────────────────────────────────╯ ╰──────────────────────╯  │
  │ status bar: refreshed 16:32 · 10 issues · q quit · ? help    │
  └───────────────────────────────────────────────────────────────┘

Keys:
  h/l ←→        switch kanban column
  j/k ↑↓        navigate within column
  enter         open ticket detail
  t             transition (modal)
  a             assign (modal)
  c             add comment (modal — $EDITOR also available)
  o             open in browser
  /             filter cards
  ctrl+p        command palette (Textual built-in)
  r             refresh
  ?             help overlay
  q             quit

The main app is `ChDashboard`. Modals are simple Textual `ModalScreen`s.
A command palette provider exposes most actions to fuzzy search.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import time
from functools import partial
from typing import Any

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    Static,
    Tab,
    TabbedContent,
    TabPane,
    Tabs,
    TextArea,
)

from jg import _compat as _jg_compat
from jg.adf import render_to_text, text_to_adf
from jg.api import ApiError, JiraClient
from jg.auth import AuthError
from jg.brainstorm import build_brainstorm_prompt
from jg.config import Config, Project
from jg.github import (
    GhError,
    my_open_prs,
    my_repos,
    pr_detail,
    repo_detail,
    repo_pulse,
    review_requested_prs,
)
from jg.gradient import (
    banner,
    gradient_text,
    hex_to_rgb,
)
from jg.notifier import notify as macos_notify
from jg.render import (
    GROUP_STYLE,
    PRIORITY_ABBR,
    TYPE_ABBR,
    normalize_status,
    relative_time,
)
from jg.themes import ALL_THEMES
from jg.tmux import quote_for_shell, spawn, spawn_in_dir

_jg_compat.apply()  # patch UTF-8 decoder before Textual driver starts

GROUPS_TO_SHOW = ["To Do", "In Progress", "In Review", "Building", "In Testing", "Ready for Production", "Done"]

# Reserved for chrome (brand wordmark, view labels, project items, panel
# titles) so it never collides with a kanban column gradient. Grey→white
# reads as pure chrome/metadata and doesn't compete with semantic colors.
CHROME_GRADIENT = ("#9893a8", "#f8f8f2")

GROUP_SHORT_LABEL = {
    "To Do": "To Do",
    "In Progress": "Progress",
    "In Review": "Review",
    "Building": "Building",
    "In Testing": "Testing",
    "Ready for Production": "Ready",
    "Done": "Done",
    "Blocked": "Blocked",
}

# Gradient endpoints per kanban column — each column gets a unique
# 2- or 3-stop spectrum so the wall feels alive without being noisy.
GROUP_GRADIENT = {
    "To Do":               ("#9893a8", "#6272a4"),               # cool gray → muted purple
    "In Progress":         ("#ff79c6", "#ffb86c"),               # pink → orange
    "In Review":           ("#8be9fd", "#bd93f9"),               # cyan → purple
    "Building":            ("#ffb86c", "#f1fa8c"),               # orange → yellow (CI/compile)
    "In Testing":          ("#bd93f9", "#ff5bc0"),               # purple → hot pink
    "Ready for Production":("#50fa7b", "#8be9fd", "#bd93f9"),    # green → cyan → purple
    "Done":                ("#50fa7b", "#7be77b"),               # solid soft green
    "Blocked":             ("#ff5555", "#ff79c6"),               # red → pink
}


# ───────────────────────────── card formatting ─────────────────────────────

def _card_text(issue: dict[str, Any]) -> Text:
    f = issue["fields"]
    key = issue["key"]
    type_name = (f.get("issuetype") or {}).get("name", "")
    type_abbr, type_color = TYPE_ABBR.get(type_name, ("?", "white"))
    pri_name = (f.get("priority") or {}).get("name", "")
    pri_abbr, pri_color = PRIORITY_ABBR.get(pri_name, ("--", "white"))
    summary = f.get("summary", "") or ""
    updated = relative_time(f.get("updated"))

    t = Text(no_wrap=False, overflow="fold")
    t.append(f"{key}", style="bold")
    t.append(" ")
    t.append(type_abbr, style=f"bold {type_color}")
    t.append(" ")
    t.append(pri_abbr, style=pri_color)
    t.append("\n")
    t.append(summary)
    t.append("\n")
    t.append(updated, style="dim")
    return t


def _pr_review_glyph(decision: str | None, is_draft: bool) -> tuple[str, str]:
    if is_draft:
        return ("✎", "yellow")
    if decision == "APPROVED":
        return ("✓", "green")
    if decision == "CHANGES_REQUESTED":
        return ("✗", "red")
    if decision == "REVIEW_REQUIRED":
        return ("⏳", "yellow")
    return ("·", "dim")


def _pr_text(p: dict[str, Any], show_author: bool = False) -> Text:
    repo = p.get("repository", {}).get("nameWithOwner", "?")
    num = p["number"]
    title = p.get("title", "") or ""
    author = p.get("author", {}).get("login", "")
    updated = relative_time(p.get("updatedAt"))
    glyph, glyph_color = _pr_review_glyph(p.get("reviewDecision"), p.get("isDraft", False))

    t = Text(no_wrap=False, overflow="fold")
    t.append(f"{repo}#{num}", style="bold cyan")
    t.append(" ")
    t.append(glyph, style=glyph_color)
    t.append("\n")
    t.append(title)
    t.append("\n")
    if show_author:
        t.append(f"by {author} · ", style="dim")
    t.append(updated, style="dim")
    return t


# ───────────────────────────── widgets ─────────────────────────────

class TicketCard(ListItem):
    def __init__(self, issue: dict[str, Any]):
        super().__init__(Static(_card_text(issue)))
        self.issue = issue
        self.key_label = issue["key"]
        self.summary = (issue.get("fields") or {}).get("summary", "")

    def matches(self, query: str) -> bool:
        if not query:
            return True
        q = query.lower()
        return q in self.key_label.lower() or q in self.summary.lower()


class _SidebarTabs(Vertical):
    """Tiny helper container that composes the three TabPanes inside a
    TabbedContent. Lets us pass the list widgets as constructor args and
    have them yielded in the proper compose context (can't pass TabPanes
    positionally to TabbedContent — it expects title strings)."""

    DEFAULT_CSS = """
    _SidebarTabs { width: 1fr; height: 1fr; background: transparent; }
    """

    def __init__(self, mine_pr: PRList, rr_pr: PRList, repo_list: RepoList):
        super().__init__()
        self._mine_pr = mine_pr
        self._rr_pr = rr_pr
        self._repo_list = repo_list

    def compose(self) -> ComposeResult:
        with TabbedContent(id="sidebar-tabs"):
            with TabPane("My PRs"):
                yield self._mine_pr
            with TabPane("Waiting on me"):
                yield self._rr_pr
            with TabPane("Repos"):
                yield self._repo_list


class KanbanScroll(HorizontalScroll):
    """HorizontalScroll subclass with arrow/page bindings stripped. The
    parent class binds left/right to scroll-by-one-cell, which intercepts
    the dashboard's left/right arrow keybindings (focus_prev_col /
    focus_next_col) because the focused ListView sits inside this container.

    Textual 8.2 builds a per-instance bindings map at __init__ time that
    includes parent BINDINGS regardless of the subclass's BINDINGS list or
    inherit_bindings flag. The only reliable way to remove them is to pop
    the keys from the instance's bindings map after super().__init__()."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key in (
            "left", "right", "up", "down",
            "home", "end", "pageup", "pagedown",
            "ctrl+pageup", "ctrl+pagedown",
        ):
            self._bindings.key_to_bindings.pop(key, None)


class KanbanColumn(Vertical):
    """A kanban column. Renders as a Vertical with its own gradient-bordered
    GradientPanel wrapper; inside, the per-column gradient title + list_view.

    We don't subclass GradientPanel directly so that the focus-width
    transition CSS stays on KanbanColumn itself and the column can own its
    list_view API (set_issues/apply_filter) cleanly."""

    can_focus = False  # focus flows to list_view

    DEFAULT_CSS = """
    KanbanColumn {
        width: 1fr;
        height: 1fr;
        min-width: 16;
        margin: 0 1 0 0;
        padding: 0;
        background: transparent;
    }
    KanbanColumn > GradientPanel { width: 1fr; height: 1fr; }
    KanbanColumn .col-title {
        text-style: bold;
        padding: 0 1;
        height: 1;
    }
    """

    def __init__(self, group_name: str):
        super().__init__()
        self.group_name = group_name
        self.list_view: ListView = ListView()
        self._all_issues: list[dict[str, Any]] = []
        self._filter: str = ""
        self._stops = list(GROUP_GRADIENT.get(group_name, ("#ffffff", "#ffffff")))
        self._panel: GradientPanel | None = None

    def compose(self) -> ComposeResult:
        label = GROUP_SHORT_LABEL.get(self.group_name, self.group_name)
        self._panel = GradientPanel(
            self.list_view,
            stops=self._stops,
            panel_title=label,
        )
        yield self._panel

    async def set_issues(self, issues: list[dict[str, Any]]) -> None:
        self._all_issues = issues
        await self._reapply()

    async def apply_filter(self, query: str) -> None:
        self._filter = query
        await self._reapply()

    async def _reapply(self) -> None:
        assert self.list_view is not None
        await self.list_view.clear()
        visible: list[dict[str, Any]] = []
        for issue in self._all_issues:
            card = TicketCard(issue)
            if self._filter and not card.matches(self._filter):
                continue
            await self.list_view.append(card)
            visible.append(issue)
        # Update the border-title with counts; GradientPanel will repaint.
        label = GROUP_SHORT_LABEL.get(self.group_name, self.group_name)
        suffix = f" ({len(visible)}/{len(self._all_issues)})" if self._filter else f" ({len(self._all_issues)})"
        if self._panel is not None:
            self._panel.panel_title = f"{label}{suffix}"
            self._panel._repaint_border()


class PRItem(ListItem):
    def __init__(self, pr: dict[str, Any], show_author: bool = False):
        super().__init__(Static(_pr_text(pr, show_author=show_author)))
        self.pr = pr


class PRList(ListView):
    can_focus = True

    async def set_prs(self, prs: list[dict[str, Any]], show_author: bool = False) -> None:
        await self.clear()
        for p in prs:
            await self.append(PRItem(p, show_author=show_author))


def _repo_text(repo: dict[str, Any], local_clone: str | None) -> Text:
    name = repo.get("nameWithOwner", "?")
    desc = (repo.get("description") or "").strip()
    updated = relative_time(repo.get("updatedAt"))
    flags = []
    if repo.get("isPrivate"):
        flags.append("private")
    if repo.get("isFork"):
        flags.append("fork")
    if repo.get("isArchived"):
        flags.append("archived")
    flags_text = " · ".join(flags)

    t = Text(no_wrap=False, overflow="fold")
    t.append(name, style="bold")
    if local_clone:
        t.append("  ✓", style="green")
    t.append("\n")
    if desc:
        t.append(desc)
        t.append("\n")
    if flags_text:
        t.append(flags_text, style="dim")
        t.append("  ")
    t.append(updated, style="dim")
    return t


class RepoItem(ListItem):
    def __init__(self, repo: dict[str, Any], local_clone: str | None):
        super().__init__(Static(_repo_text(repo, local_clone)))
        self.repo = repo
        self.local_clone = local_clone


class RepoList(ListView):
    can_focus = True

    async def set_repos(
        self,
        repos: list[dict[str, Any]],
        repo_root: str,
        config: Config | None = None,
    ) -> None:
        """Render repos. If `config` is provided, project mappings override the
        ~/repo_root heuristic (e.g. my-service → ~/code/myservice)."""
        from pathlib import Path

        await self.clear()
        root = Path(repo_root).expanduser()
        for r in repos:
            if r.get("isArchived"):
                continue
            name = r.get("nameWithOwner", "")
            local: str | None = None
            # 1. Per-repo override from any project's repo_paths
            if config:
                mapped = config.resolve_repo_path(name)
                if mapped:
                    p = Path(mapped).expanduser()
                    if p.is_dir():
                        local = str(p)
            # 2. Fall back to short-name then full-owner-name in repo_root
            if not local:
                short = name.split("/")[-1] if "/" in name else name
                candidate_short = root / short
                candidate_full = root / name.replace("/", "-")
                if candidate_short.is_dir():
                    local = str(candidate_short)
                elif candidate_full.is_dir():
                    local = str(candidate_full)
            await self.append(RepoItem(r, local))


# ───────────────────────────── projects panel ─────────────────────────────

class ProjectItem(ListItem):
    def __init__(self, project: Project | None, label: str, special: str = ""):
        """`project` may be None for synthetic 'All' / 'Unassigned' rows.
        `special` distinguishes them: '', 'all', 'unassigned'."""
        self.project = project
        self.special = special
        self.label = label
        self._badge_pr_review: int = 0
        self._static = Static(self._render_text())
        super().__init__(self._static)

    def _render_text(self) -> Text:
        text = Text()
        if self.special == "all":
            text.append("◆ ", style="bold")
            text.append_text(gradient_text(self.label, *CHROME_GRADIENT, bold=True))
        elif self.special == "unassigned":
            text.append("○ ", style="dim")
            text.append(self.label, style="dim")
        else:
            text.append("● ", style="bold")
            text.append_text(gradient_text(self.label, *CHROME_GRADIENT, bold=True))
            meta_bits: list[str] = []
            if self.project and self.project.repos:
                meta_bits.append(f"{len(self.project.repos)} repos")
            if self._badge_pr_review > 0:
                meta_bits.append(f"★ {self._badge_pr_review} awaiting review")
            if meta_bits:
                text.append("\n   " + "  ·  ".join(meta_bits), style="dim")
        return text

    def set_pr_review_badge(self, n: int) -> None:
        if n == self._badge_pr_review:
            return
        self._badge_pr_review = n
        self._static.update(self._render_text())


class ProjectList(ListView):
    can_focus = True

    async def set_projects(self, projects: list[Project]) -> None:
        await self.clear()
        await self.append(ProjectItem(None, "All", special="all"))
        for p in projects:
            await self.append(ProjectItem(p, p.name))
        await self.append(ProjectItem(None, "Unassigned", special="unassigned"))

    def update_pr_review_badges(self, counts: dict[str, int]) -> None:
        """counts: {project_name: number_of_PRs_awaiting_review_on_that_project's_repos}"""
        for child in self.query(ProjectItem):
            if child.project is not None:
                child.set_pr_review_badge(counts.get(child.project.name, 0))


# ───────────────────────────── modals ─────────────────────────────

class TransitionModal(ModalScreen[str | None]):
    DEFAULT_CSS = """
    TransitionModal {
        align: center middle;
        background: #000000 97%;
    }
    TransitionModal GradientPanel {
        background: #1c1c1e;
        width: 50;
        height: auto;
        max-height: 80%;
    }
    TransitionModal #title { text-style: bold; }
    """

    BINDINGS = [Binding("escape", "dismiss", "cancel", show=False)]  # noqa: RUF012

    def __init__(self, transitions: list[dict]):
        super().__init__()
        self.transitions = transitions

    def compose(self) -> ComposeResult:
        yield GradientPanel(panel_title="Transition to")

    def on_mount(self) -> None:
        self.lv = ListView(*[ListItem(Label(t["to"]["name"])) for t in self.transitions])
        panel = self.query_one(GradientPanel)
        panel.mount_content(
            self.lv,
            Static("[dim]enter to apply · esc to cancel[/]", markup=True),
        )
        self.lv.focus()

    @on(ListView.Selected)
    def selected(self, ev: ListView.Selected) -> None:
        idx = ev.list_view.index or 0
        self.dismiss(self.transitions[idx]["id"])

    def action_dismiss(self) -> None:
        self.dismiss(None)


class AIDirPickerModal(ModalScreen[str | None]):
    """Picker shown when launching claude on a ticket — the user picks which
    local repo directory to cd into. Returns the absolute path on enter, or
    None on escape (in which case the caller spawns claude without a cwd).

    Includes a fuzzy filter input at the top: typing narrows the candidate
    list against label + path (substring, case-insensitive). Useful at "All
    projects" scope where the candidate list spans every known repo."""

    DEFAULT_CSS = """
    AIDirPickerModal {
        align: center middle;
        background: #000000 97%;
    }
    AIDirPickerModal GradientPanel {
        background: #1c1c1e;
        width: 70;
        height: auto;
        max-height: 80%;
    }
    AIDirPickerModal #ai-filter { margin: 0 1; }
    AIDirPickerModal ListView { height: auto; max-height: 16; }
    AIDirPickerModal #hint { color: $text-muted; height: 1; padding: 0 1; }
    """

    BINDINGS = [  # noqa: RUF012
        Binding("escape", "dismiss", "cancel", show=False),
        Binding("down", "focus_list", "down to list", show=False),
    ]

    def __init__(self, key: str, candidates: list[tuple[str, str]]):
        """`candidates` is a list of (label, abs_path) tuples in display order."""
        super().__init__()
        self.key = key
        self.candidates = candidates
        self._visible: list[tuple[str, str]] = list(candidates)

    def compose(self) -> ComposeResult:
        yield GradientPanel(panel_title=f"Open claude for {self.key} in…")

    def on_mount(self) -> None:
        self.filter_input = Input(placeholder="filter — type to narrow", id="ai-filter")
        self.lv = ListView(*self._build_items(self.candidates))
        panel = self.query_one(GradientPanel)
        panel.mount_content(
            self.filter_input,
            self.lv,
            Static(
                "[dim]type to filter · ↑↓ to choose · enter to open · esc to cancel[/]",
                markup=True,
                id="hint",
            ),
        )
        self.filter_input.focus()

    def _build_items(self, candidates: list[tuple[str, str]]) -> list[ListItem]:
        items: list[ListItem] = []
        for label, path in candidates:
            row = Text()
            row.append("● ", style="bold")
            row.append_text(gradient_text(label, *CHROME_GRADIENT, bold=True))
            row.append(f"\n   {path}", style="dim")
            items.append(ListItem(Static(row)))
        return items

    @staticmethod
    def _fuzzy_match(query: str, target: str) -> bool:
        """Subsequence match: every char of `query` appears in `target` in
        order (not necessarily contiguous). Both should be lowercased."""
        if not query:
            return True
        qi = 0
        for c in target:
            if c == query[qi]:
                qi += 1
                if qi == len(query):
                    return True
        return False

    @on(Input.Changed, "#ai-filter")
    async def _filter_changed(self, ev: Input.Changed) -> None:
        q = ev.value.strip().lower()
        if not q:
            self._visible = list(self.candidates)
        else:
            self._visible = [
                (label, path) for (label, path) in self.candidates
                if self._fuzzy_match(q, f"{label}\n{path}".lower())
            ]
        await self.lv.clear()
        for item in self._build_items(self._visible):
            await self.lv.append(item)

    @on(Input.Submitted, "#ai-filter")
    def _filter_submitted(self, ev: Input.Submitted) -> None:
        # Enter from the filter input: pick the first match if there's exactly
        # one, otherwise jump focus into the list so ↑↓+enter takes over.
        if len(self._visible) == 1:
            self.dismiss(self._visible[0][1])
        elif self._visible:
            self.lv.index = 0
            self.lv.focus()

    def action_focus_list(self) -> None:
        if self._visible:
            self.lv.index = 0
            self.lv.focus()

    @on(ListView.Selected)
    def selected(self, ev: ListView.Selected) -> None:
        idx = ev.list_view.index or 0
        if 0 <= idx < len(self._visible):
            self.dismiss(self._visible[idx][1])

    def action_dismiss(self) -> None:
        self.dismiss(None)


class CommentModal(ModalScreen[str | None]):
    DEFAULT_CSS = """
    CommentModal {
        align: center middle;
        background: #000000 97%;
    }
    CommentModal GradientPanel {
        background: #1c1c1e;
        width: 80%;
        height: 60%;
    }
    CommentModal TextArea { height: 1fr; }
    CommentModal #hint { color: $text-muted; height: 1; }
    """

    BINDINGS = [  # noqa: RUF012
        Binding("escape", "cancel", "cancel", show=False),
        Binding("ctrl+s", "submit", "submit", show=True),
        Binding("ctrl+e", "open_editor", "$EDITOR", show=True),
    ]

    def __init__(self, key: str):
        super().__init__()
        self.key = key

    def compose(self) -> ComposeResult:
        yield GradientPanel(panel_title=f"Comment on {self.key}")

    def on_mount(self) -> None:
        self.area = TextArea(language="markdown")
        panel = self.query_one(GradientPanel)
        panel.mount_content(
            self.area,
            Static("[dim]ctrl+s submit · ctrl+e open in $EDITOR · esc cancel[/]", id="hint", markup=True),
        )
        self.area.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        text = self.area.text.strip()
        self.dismiss(text or None)

    def action_open_editor(self) -> None:
        text = self.area.text
        editor = os.environ.get("EDITOR", "vi")
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
            f.write(text)
            path = f.name
        try:
            with self.app.suspend():
                subprocess.run([editor, path], check=False)
            with open(path) as f:
                new = f.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        self.area.text = new
        self.area.focus()


class AssignModal(ModalScreen[str | None]):
    DEFAULT_CSS = """
    AssignModal {
        align: center middle;
        background: #000000 97%;
    }
    AssignModal GradientPanel {
        background: #1c1c1e;
        width: 50;
        height: 9;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "cancel", show=False)]  # noqa: RUF012

    def compose(self) -> ComposeResult:
        yield GradientPanel(panel_title="Assign to")

    def on_mount(self) -> None:
        self.input = Input(placeholder="@me / none / name")
        panel = self.query_one(GradientPanel)
        panel.mount_content(
            self.input,
            Static("[dim]enter to apply · esc to cancel[/]", markup=True),
        )
        self.input.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    def _submitted(self, ev: Input.Submitted) -> None:
        val = ev.value.strip() or "@me"
        self.dismiss(val)


PRIORITY_CYCLE = ["Highest", "High", "Medium", "Low", "Lowest"]


class _SummaryEdit(ModalScreen[str | None]):
    DEFAULT_CSS = """
    _SummaryEdit {
        align: center middle;
        background: #000000 97%;
    }
    _SummaryEdit GradientPanel {
        background: #1c1c1e;
        width: 90;
        height: 7;
    }
    """
    BINDINGS = [Binding("escape", "cancel", "cancel", show=False)]  # noqa: RUF012

    def __init__(self, current: str):
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        yield GradientPanel(panel_title="Edit summary")

    def on_mount(self) -> None:
        self.input = Input(value=self.current)
        panel = self.query_one(GradientPanel)
        panel.mount_content(
            self.input,
            Static("[dim]enter to save · esc to cancel[/]", markup=True),
        )
        self.input.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    def _submitted(self, ev: Input.Submitted) -> None:
        new = ev.value.strip()
        if not new or new == self.current:
            self.dismiss(None)
        else:
            self.dismiss(new)


class _LabelsEdit(ModalScreen[list[str] | None]):
    DEFAULT_CSS = """
    _LabelsEdit {
        align: center middle;
        background: #000000 97%;
    }
    _LabelsEdit GradientPanel {
        background: #1c1c1e;
        width: 70;
        height: 7;
    }
    """
    BINDINGS = [Binding("escape", "cancel", "cancel", show=False)]  # noqa: RUF012

    def __init__(self, current: list[str]):
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        yield GradientPanel(panel_title="Edit labels (space-separated)")

    def on_mount(self) -> None:
        self.input = Input(value=" ".join(self.current))
        panel = self.query_one(GradientPanel)
        panel.mount_content(
            self.input,
            Static("[dim]enter to save · esc to cancel[/]", markup=True),
        )
        self.input.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    def _submitted(self, ev: Input.Submitted) -> None:
        new = [x for x in ev.value.split() if x]
        if new == self.current:
            self.dismiss(None)
        else:
            self.dismiss(new)


class TicketDetailModal(ModalScreen[None]):
    """Editable ticket detail. Same actions as the dashboard, scoped to one issue."""

    DEFAULT_CSS = """
    TicketDetailModal {
        align: center middle;
        background: #000000 97%;
    }
    TicketDetailModal GradientPanel {
        background: #1c1c1e;
        width: 80%;
        height: 80%;
    }
    TicketDetailModal #title { text-style: bold; }
    TicketDetailModal .meta { color: $text-muted; }
    TicketDetailModal #footer { color: $text-muted; }
    TicketDetailModal .section {
        border: round #6c7086 50%;
        border-title-color: #c0caf5;
        border-title-style: bold;
        padding: 0 1;
        margin: 1 0 0 0;
        height: auto;
    }
    TicketDetailModal Markdown {
        margin: 0;
        padding: 0;
        color: white;
    }
    TicketDetailModal MarkdownParagraph,
    TicketDetailModal MarkdownBulletList,
    TicketDetailModal MarkdownOrderedList,
    TicketDetailModal MarkdownListItem {
        color: white;
    }
    /* Three-tier description hierarchy. H1 is rare in this Jira instance
       (sampled: 0/95 headings are H1) but on the rare occasion it appears
       it should clearly outrank H2. H2 is the dominant header. H3 is the
       gentlest. Mapping:
         H1 → full-width accent banner, white bold text (most prominent)
         H2 → leading colored bar, accent bold text (dominant)
         H3 → accent bold text only (gentlest) */
    TicketDetailModal MarkdownH1 {
        background: $accent;
        color: white;
        text-style: bold;
        margin: 1 0 0 0;
        padding: 0 1;
    }
    TicketDetailModal MarkdownH2 {
        color: $accent;
        text-style: bold;
        margin: 1 0 0 0;
        padding: 0 0 0 1;
        border-left: thick $accent;
        background: transparent;
    }
    TicketDetailModal MarkdownH3 {
        color: $accent;
        text-style: bold;
        margin: 1 0 0 0;
        padding: 0;
        background: transparent;
    }
    TicketDetailModal MarkdownH1 .markdown--rule, TicketDetailModal MarkdownH2 .markdown--rule {
        display: none;
    }
    TicketDetailModal MarkdownCode, TicketDetailModal MarkdownFence {
        background: $boost 20%;
        color: $accent;
    }
    TicketDetailModal .meta-band {
        height: auto;
        margin: 0 0 1 0;
    }
    TicketDetailModal .meta-line {
        height: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [  # noqa: RUF012
        Binding("escape", "close", "close", show=False),
        Binding("q", "close", "close", show=False),
        Binding("r", "refresh", "refresh", show=True),
        Binding("t", "do_transition", "transition", show=True),
        Binding("a", "do_assign", "assign", show=True),
        Binding("c", "do_comment", "comment", show=True),
        Binding("e", "edit_summary", "summary", show=True),
        Binding("d", "edit_description", "description", show=True),
        Binding("T", "edit_testcases", "test cases", show=True),
        Binding("p", "cycle_priority", "priority", show=True),
        Binding("l", "edit_labels", "labels", show=True),
        Binding("o", "open_browser", "browser", show=True),
        Binding("A", "claude", "claude", show=True),
        Binding("E", "editor", "editor", show=True),
        Binding("ctrl+a", "claude_force", "re-pick AI dir", show=False),
        Binding("ctrl+e", "editor_force", "re-pick editor dir", show=False),
    ]

    def __init__(self, key: str, config: Config):
        super().__init__()
        self.key = key
        self.config = config
        self.issue: dict[str, Any] | None = None
        self.body_scroll: VerticalScroll | None = None

    def compose(self) -> ComposeResult:
        # No panel_title — content title row already shows the key + summary.
        yield GradientPanel()

    async def on_mount(self) -> None:
        panel = self.query_one(GradientPanel)
        self._title_static = Static(f"loading {self.key}…", id="title")
        self.body_scroll = VerticalScroll()
        self._footer_static = Static(self._footer_text(), id="footer", markup=True)
        panel.mount_content(self._title_static, self.body_scroll, self._footer_static)
        await self._reload()

    def _footer_text(self) -> str:
        """Two grouped lines — edit actions on top, view actions below. The
        T (test cases) hint dims to '(add)' when the field is empty so the
        affordance is honest about what pressing it will do."""
        f = (self.issue or {}).get("fields", {}) if self.issue else {}
        tc = f.get("customfield_10186") if f else None
        tc_has = bool(tc)
        tc_label = "T tests" if tc_has else "T tests (add)"
        edit = (
            "[bold #c0caf5]edit[/]  "
            "t trans · a assign · c comment · e summary · d desc · "
            f"{tc_label} · p prio · l labels"
        )
        view = (
            "[bold #c0caf5]view[/]  "
            "A claude · o browser · r refresh · esc close"
        )
        return f"[dim]{edit}\n{view}[/]"

    async def _reload(self) -> None:
        try:
            async with JiraClient(self.config) as api:
                fetched = await api.get_issue(
                    self.key,
                    fields=[
                        "summary", "description", "status", "priority", "issuetype",
                        "labels", "components", "fixVersions",
                        "assignee", "reporter",
                        "parent", "issuelinks",
                        "comment", "customfield_10186",
                    ],
                )
        except AuthError as e:
            if e.needs_relogin:
                self.app.notify("⚠ session expired — run ch auth login", severity="error", timeout=15)
            else:
                self.app.notify(f"auth error: {e}", severity="error")
            return
        except ApiError as e:
            self.app.notify(f"load failed: {e}", severity="error")
            return
        # API may return either {"key": ..., "fields": ...} or a search-style envelope.
        if "fields" not in fetched and "issues" in fetched:
            nodes = fetched.get("issues", {}).get("nodes") or []
            if nodes:
                fetched = nodes[0]
        self.issue = fetched
        await self._redraw()

    async def _redraw(self) -> None:
        if not self.issue or not self.body_scroll:
            return
        f = self.issue.get("fields", {})
        summary = f.get("summary", "")
        status = (f.get("status") or {}).get("name", "—")
        priority = (f.get("priority") or {}).get("name", "—")
        type_name = (f.get("issuetype") or {}).get("name", "")
        assignee = (f.get("assignee") or {}).get("displayName", "—")
        reporter = (f.get("reporter") or {}).get("displayName", "")
        labels = f.get("labels", []) or []
        components = [c.get("name", "") for c in (f.get("components") or [])]
        fix_versions = [v.get("name", "") for v in (f.get("fixVersions") or [])]
        parent = f.get("parent") or {}
        parent_key = parent.get("key", "")
        parent_summary = (parent.get("fields") or {}).get("summary", "")

        # Issue-link counts grouped by inward/outward type (e.g. "blocks: 2",
        # "blocked-by: 1"). Cheap signal of "how connected is this ticket?".
        link_counts: dict[str, int] = {}
        for link in (f.get("issuelinks") or []):
            t = link.get("type") or {}
            if "outwardIssue" in link:
                name = t.get("outward") or t.get("name", "links")
            elif "inwardIssue" in link:
                name = t.get("inward") or t.get("name", "links")
            else:
                continue
            link_counts[name] = link_counts.get(name, 0) + 1

        # Title line.
        title_text = Text()
        title_text.append(self.issue['key'], style="bold #c0caf5")
        title_text.append("  ")
        title_text.append(summary, style="bold")
        self._title_static.update(title_text)

        # Width-aware: at narrow modals (≤ ~70 cols) collapse meta to a single
        # line; at wider modals render a 3-line band.
        width = self.app.size.width if self.app else 140
        narrow = width < 100

        await self.body_scroll.remove_children()

        meta_band = Vertical(classes="meta-band")
        await self.body_scroll.mount(meta_band)

        # Line 1 — state: status badge · type · priority · @assignee · reporter
        line1 = Text()
        line1.append(f"[● {status}]", style=GROUP_STYLE.get(normalize_status(status), "white") + " bold")
        line1.append("  ")
        line1.append(type_name or "—", style="white")
        line1.append("  ·  ", style="dim")
        line1.append(priority or "—", style="white")
        line1.append("  ·  ", style="dim")
        line1.append(f"@{assignee}", style="white")
        if reporter and reporter != assignee:
            line1.append("  ·  reporter ", style="dim")
            line1.append(f"@{reporter}", style="white")
        meta_band.mount(Static(line1, classes="meta-line"))

        if not narrow:
            # Line 2 — structure: epic · components · fix version
            line2_bits: list[tuple[str, str]] = []
            if parent_key:
                line2_bits.append(("Epic", f"{parent_key}" + (f" · {parent_summary[:40]}" if parent_summary else "")))
            if components:
                line2_bits.append(("Components", ", ".join(components)))
            if fix_versions:
                line2_bits.append(("Fix", ", ".join(fix_versions)))
            if line2_bits:
                line2 = Text()
                for i, (k, v) in enumerate(line2_bits):
                    if i > 0:
                        line2.append("  ·  ", style="dim")
                    line2.append(f"{k}: ", style="dim")
                    line2.append(v, style="white")
                meta_band.mount(Static(line2, classes="meta-line"))

            # Line 3 — labels + link counts
            line3_bits: list[tuple[str, str]] = []
            if labels:
                line3_bits.append(("Labels", ", ".join(labels)))
            if link_counts:
                summary_links = " · ".join(f"{n} {name}" for name, n in link_counts.items())
                line3_bits.append(("Links", summary_links))
            if line3_bits:
                line3 = Text()
                for i, (k, v) in enumerate(line3_bits):
                    if i > 0:
                        line3.append("  ·  ", style="dim")
                    line3.append(f"{k}: ", style="dim")
                    line3.append(v, style="white")
                meta_band.mount(Static(line3, classes="meta-line"))
        # ── Description box ──
        desc_box = Vertical(classes="section")
        desc_box.border_title = "  Description  "
        self.body_scroll.mount(desc_box)
        desc_md = render_to_text(f.get("description"))
        if desc_md.strip():
            desc_box.mount(Markdown(desc_md))
        else:
            desc_box.mount(Static("[dim](no description)[/]", markup=True))
        # ── Test cases box (custom field — Charmhealth-specific) ──
        tc_md = render_to_text(f.get("customfield_10186"))
        tc_box = Vertical(classes="section")
        tc_box.border_title = "  Test Cases  "
        self.body_scroll.mount(tc_box)
        if tc_md.strip():
            tc_box.mount(Markdown(tc_md))
        else:
            tc_box.mount(Static("[dim](none — press T to add)[/]", markup=True))
        # ── Comments box ──
        comments = (f.get("comment") or {}).get("comments") or []
        if comments:
            comments_box = Vertical(classes="section")
            comments_box.border_title = f"  Comments ({len(comments)})  "
            self.body_scroll.mount(comments_box)
            for c in comments[-5:]:
                author = c.get("author", {}).get("displayName", "?")
                when = relative_time(c.get("created"))
                body_md = render_to_text(c.get("body"))
                comments_box.mount(
                    Static(Text.assemble((f"{author}", "bold"), " · ", (when, "dim"))),
                )
                if body_md.strip():
                    comments_box.mount(Markdown(body_md))
                else:
                    comments_box.mount(Static("[dim](empty)[/]", markup=True))
        # Footer reflects loaded state (T (add) hint when empty).
        if self._footer_static is not None:
            self._footer_static.update(self._footer_text())

    # ── actions ────────────────────────────────────────────

    def action_close(self) -> None:
        self.dismiss(None)

    @work(exclusive=False)
    async def action_refresh(self) -> None:
        await self._reload()
        self.app.notify("refreshed", severity="information", timeout=2)

    @work(exclusive=True)
    async def action_do_transition(self) -> None:
        try:
            async with JiraClient(self.config) as api:
                transitions = await api.get_transitions(self.key)
        except ApiError as e:
            self.app.notify(f"error: {e}", severity="error")
            return
        if not transitions:
            self.app.notify("no transitions", severity="warning")
            return

        def _on_pick(transition_id: str | None) -> None:
            if transition_id:
                self.run_worker(self._apply_transition(transition_id))

        self.app.push_screen(TransitionModal(transitions), _on_pick)

    async def _apply_transition(self, transition_id: str) -> None:
        try:
            async with JiraClient(self.config) as api:
                await api.transition_issue(self.key, transition_id)
        except ApiError as e:
            self.app.notify(f"transition failed: {e}", severity="error")
            return
        self.app.notify("✓ transitioned", severity="information")
        await self._reload()

    def action_do_assign(self) -> None:
        def _on_pick(target: str | None) -> None:
            if target:
                self.run_worker(self._apply_assign(target))

        self.app.push_screen(AssignModal(), _on_pick)

    async def _apply_assign(self, target: str) -> None:
        try:
            async with JiraClient(self.config) as api:
                if target.lower() in ("@me", "me"):
                    me = await api.myself()
                    await api.edit_issue(self.key, {"assignee": {"accountId": me["accountId"]}})
                    name = me.get("displayName", "you")
                elif target.lower() in ("none", "unassign", "-"):
                    await api.edit_issue(self.key, {"assignee": None})
                    name = "—"
                else:
                    results = await api.find_user(target.lstrip("@"))
                    if not results:
                        self.app.notify(f"no user matches '{target}'", severity="warning")
                        return
                    if len(results) > 1:
                        self.app.notify(f"ambiguous '{target}'", severity="warning")
                        return
                    await api.edit_issue(self.key, {"assignee": {"accountId": results[0]["accountId"]}})
                    name = results[0].get("displayName", target)
        except ApiError as e:
            self.app.notify(f"assign failed: {e}", severity="error")
            return
        self.app.notify(f"✓ assigned to {name}", severity="information")
        await self._reload()

    def action_do_comment(self) -> None:
        def _on_submit(text: str | None) -> None:
            if text:
                self.run_worker(self._apply_comment(text))

        self.app.push_screen(CommentModal(self.key), _on_submit)

    async def _apply_comment(self, text: str) -> None:
        try:
            async with JiraClient(self.config) as api:
                await api.add_comment(self.key, text_to_adf(text))
        except ApiError as e:
            self.app.notify(f"comment failed: {e}", severity="error")
            return
        self.app.notify("✓ commented", severity="information")
        await self._reload()

    def action_edit_summary(self) -> None:
        if not self.issue:
            return
        current = (self.issue.get("fields") or {}).get("summary", "")

        def _on_submit(new: str | None) -> None:
            if new and new != current:
                self.run_worker(self._apply_summary(new))

        self.app.push_screen(_SummaryEdit(current), _on_submit)

    async def _apply_summary(self, summary: str) -> None:
        try:
            async with JiraClient(self.config) as api:
                await api.edit_issue(self.key, {"summary": summary})
        except ApiError as e:
            self.app.notify(f"edit failed: {e}", severity="error")
            return
        self.app.notify("✓ summary updated", severity="information")
        await self._reload()

    @work(exclusive=False)
    async def action_edit_description(self) -> None:
        if not self.issue:
            return
        current = render_to_text((self.issue.get("fields") or {}).get("description"))
        editor = os.environ.get("EDITOR", "vi")
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
            f.write(current)
            path = f.name
        try:
            with self.app.suspend():
                subprocess.run([editor, path], check=False)
            with open(path) as fh:
                new = fh.read().strip()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if new == current.strip():
            self.app.notify("description unchanged", severity="information", timeout=2)
            return
        try:
            async with JiraClient(self.config) as api:
                await api.edit_issue(self.key, {"description": text_to_adf(new) if new else None})
        except ApiError as e:
            self.app.notify(f"edit failed: {e}", severity="error")
            return
        self.app.notify("✓ description updated", severity="information")
        await self._reload()

    @work(exclusive=False)
    async def action_edit_testcases(self) -> None:
        if not self.issue:
            return
        current = render_to_text((self.issue.get("fields") or {}).get("customfield_10186"))
        editor = os.environ.get("EDITOR", "vi")
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as fh:
            fh.write(current)
            path = fh.name
        try:
            with self.app.suspend():
                subprocess.run([editor, path], check=False)
            with open(path) as fh2:
                new = fh2.read().strip()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if new == current.strip():
            self.app.notify("test cases unchanged", severity="information", timeout=2)
            return
        try:
            async with JiraClient(self.config) as api:
                await api.edit_issue(
                    self.key,
                    {"customfield_10186": text_to_adf(new) if new else None},
                )
        except ApiError as e:
            self.app.notify(f"edit failed: {e}", severity="error")
            return
        self.app.notify("✓ test cases updated", severity="information")
        await self._reload()

    def action_edit_labels(self) -> None:
        if not self.issue:
            return
        current = (self.issue.get("fields") or {}).get("labels") or []

        def _on_submit(new: list[str] | None) -> None:
            if new is not None and new != current:
                self.run_worker(self._apply_labels(new))

        self.app.push_screen(_LabelsEdit(current), _on_submit)

    async def _apply_labels(self, labels: list[str]) -> None:
        try:
            async with JiraClient(self.config) as api:
                await api.edit_issue(self.key, {"labels": labels})
        except ApiError as e:
            self.app.notify(f"edit failed: {e}", severity="error")
            return
        self.app.notify("✓ labels updated", severity="information")
        await self._reload()

    @work(exclusive=False)
    async def action_cycle_priority(self) -> None:
        if not self.issue:
            return
        current = ((self.issue.get("fields") or {}).get("priority") or {}).get("name", "Medium")
        try:
            idx = PRIORITY_CYCLE.index(current)
        except ValueError:
            idx = 2
        next_pri = PRIORITY_CYCLE[(idx + 1) % len(PRIORITY_CYCLE)]
        try:
            async with JiraClient(self.config) as api:
                await api.edit_issue(self.key, {"priority": {"name": next_pri}})
        except ApiError as e:
            self.app.notify(f"edit failed: {e}", severity="error")
            return
        self.app.notify(f"✓ priority → {next_pri}", severity="information")
        await self._reload()

    def action_open_browser(self) -> None:
        if not self.config.default_cloud_url:
            self.app.notify("no default_cloud_url configured", severity="warning")
            return
        import webbrowser
        base = self.config.default_cloud_url.rstrip("/")
        webbrowser.open(f"{base}/browse/{self.key}")
        self.app.notify(f"opened {self.key}", severity="information")

    def action_claude(self, force_pick: bool = False) -> None:
        # Reuse the dashboard's AIDirPickerModal flow so the modal-launched
        # claude opens in the right repo dir (same UX as 'A' on a kanban card).
        launcher = getattr(self.app, "_launch_ai_for_key", None)
        if callable(launcher):
            launcher(self.key, force_pick=force_pick)
            return
        # Fallback: dashboard helper missing, spawn without cwd.
        cmd = self.config.ai.default_command
        full = f"{self.config.ai.claude_path} {quote_for_shell(f'{cmd} {self.key}')}"
        try:
            spawn(full, title=self.key, config=self.config.tmux)
        except RuntimeError as e:
            self.app.notify(str(e), severity="error")
            return
        self.app.notify(f"opened claude pane for {self.key}", severity="information")

    def action_claude_force(self) -> None:
        self.action_claude(force_pick=True)

    def action_editor(self, force_pick: bool = False) -> None:
        """E: spawn nvim in the same dir picker as claude. Shares the dashboard's
        ticket→dir cache so press order doesn't matter — but for claudecode.nvim
        WebSocket auto-discovery, press E first so nvim is up before claude."""
        launcher = getattr(self.app, "_launch_editor_for_key", None)
        if callable(launcher):
            launcher(self.key, force_pick=force_pick)
        else:
            self.app.notify("editor launcher not available", severity="warning")

    def action_editor_force(self) -> None:
        self.action_editor(force_pick=True)


class PRDetailModal(ModalScreen[None]):
    """View a PR with code-review-in-claude action."""

    DEFAULT_CSS = """
    PRDetailModal {
        align: center middle;
        background: #000000 97%;
    }
    PRDetailModal GradientPanel {
        background: #1c1c1e;
        width: 80%;
        height: 80%;
    }
    PRDetailModal #title { text-style: bold; }
    PRDetailModal .meta { color: $text-muted; }
    PRDetailModal .section {
        border: round #6c7086 50%;
        border-title-color: #c0caf5;
        border-title-style: bold;
        padding: 0 1;
        margin: 1 0 0 0;
        height: auto;
    }
    PRDetailModal Markdown { margin: 0; padding: 0; }
    PRDetailModal MarkdownH1, PRDetailModal MarkdownH2, PRDetailModal MarkdownH3 {
        color: $text;
        text-style: bold;
        margin: 1 0 0 0;
        padding: 0;
        background: transparent;
    }
    """

    BINDINGS = [  # noqa: RUF012
        Binding("escape,q", "close", "close", show=False),
        Binding("o", "open_browser", "browser", show=True),
        Binding("A", "claude_review", "claude review", show=True),
        Binding("E", "editor", "editor", show=True),
        Binding("r", "refresh", "refresh", show=True),
    ]

    def __init__(self, repo: str, number: int, url: str, config: Config):
        super().__init__()
        self.repo = repo
        self.number = number
        self.url = url
        self.config = config
        self.detail: dict[str, Any] | None = None

    def compose(self) -> ComposeResult:
        yield GradientPanel()

    async def on_mount(self) -> None:
        panel = self.query_one(GradientPanel)
        self._title_static = Static(f"loading {self.repo}#{self.number}…", id="title")
        self.body = VerticalScroll()
        self._footer_static = Static(
            "[dim]o open in browser · A claude code review · r refresh · esc close[/]",
            markup=True,
        )
        panel.mount_content(self._title_static, self.body, self._footer_static)
        await self._reload()

    async def _reload(self) -> None:
        try:
            data = await asyncio.to_thread(pr_detail, self.url)
        except GhError as e:
            self.app.notify(f"gh: {e}", severity="error")
            return
        self.detail = data
        await self._redraw()

    async def _redraw(self) -> None:
        if not self.detail:
            return
        d = self.detail
        head = Text()
        head.append(f"{self.repo}#{self.number}", style="bold #c0caf5")
        head.append("  ")
        head.append(d.get("title", ""), style="bold")
        self._title_static.update(head)

        state = d.get("state", "?")
        decision = d.get("reviewDecision") or "—"
        mergeable = d.get("mergeable") or "?"
        author = (d.get("author") or {}).get("login", "?")
        base = d.get("baseRefName", "?")
        head_ref = d.get("headRefName", "?")
        changed = d.get("changedFiles", 0)
        adds = d.get("additions", 0)
        dels = d.get("deletions", 0)
        labels = ", ".join(lbl.get("name", "") for lbl in (d.get("labels") or [])) or "—"

        await self.body.remove_children()
        # ── Metadata box ──
        meta_box = Vertical(classes="section")
        meta_box.border_title = "  Metadata  "
        self.body.mount(meta_box)
        meta_box.mount(
            Static(Text.assemble(
                ("state:    ", "dim"), state,
                "    ", ("review:   ", "dim"), decision,
                "    ", ("mergeable:", "dim"), f" {mergeable}",
            ), classes="meta"),
            Static(Text.assemble(
                ("author:   ", "dim"), f"@{author}",
                "    ", ("branch:   ", "dim"), f"{head_ref} → {base}",
            ), classes="meta"),
            Static(Text.assemble(
                ("changes:  ", "dim"), f"{changed} files  ",
                ("+", "green"), str(adds), " ",
                ("-", "red"), str(dels),
            ), classes="meta"),
            Static(Text.assemble(("labels:   ", "dim"), labels), classes="meta"),
        )

        # ── Checks box ──
        checks = d.get("statusCheckRollup") or []
        if checks:
            checks_box = Vertical(classes="section")
            checks_box.border_title = f"  Checks ({len(checks)})  "
            self.body.mount(checks_box)
            for c in checks[:10]:
                name = c.get("name") or c.get("context") or "?"
                concl = c.get("conclusion") or c.get("state") or "—"
                style = {
                    "SUCCESS": "green", "FAILURE": "red", "ERROR": "red",
                    "PENDING": "yellow", "NEUTRAL": "dim", "CANCELLED": "dim",
                    "SKIPPED": "dim",
                }.get(str(concl).upper(), "white")
                checks_box.mount(Static(Text.assemble(("•  ", "dim"), (concl, style), f"  {name}")))
            if len(checks) > 10:
                checks_box.mount(Static(f"[dim]… and {len(checks) - 10} more[/]", markup=True))

        # ── Description box ──
        body_md = d.get("body") or ""
        if body_md.strip():
            desc_box = Vertical(classes="section")
            desc_box.border_title = "  Description  "
            self.body.mount(desc_box)
            desc_box.mount(Markdown(body_md))

        # ── Comments box ──
        comments = d.get("comments") or []
        if comments:
            comments_box = Vertical(classes="section")
            comments_box.border_title = f"  Comments ({len(comments)})  "
            self.body.mount(comments_box)
            for c in comments[-5:]:
                author = (c.get("author") or {}).get("login", "?")
                cbody = c.get("body", "")
                comments_box.mount(Static(Text.assemble((f"@{author}", "bold"))))
                if cbody.strip():
                    comments_box.mount(Markdown(cbody))
                else:
                    comments_box.mount(Static("[dim](empty)[/]", markup=True))
            if len(comments) > 5:
                comments_box.mount(Static(f"[dim]({len(comments) - 5} earlier)[/]", markup=True))

    def action_close(self) -> None:
        self.dismiss(None)

    def action_open_browser(self) -> None:
        import webbrowser
        webbrowser.open(self.url)
        self.app.notify(f"opened {self.repo}#{self.number}", severity="information")

    def action_claude_review(self) -> None:
        prompt = f"/review {self.url}"
        full = f"{self.config.ai.claude_path} {quote_for_shell(prompt)}"
        # Spawn in the PR's repo dir when resolvable so a parallel editor pane
        # (E) lands in the same cwd for claudecode.nvim auto-discovery.
        cwd = self._resolve_local_dir()
        try:
            if cwd:
                spawn_in_dir(full, cwd=cwd, title=f"review·{self.repo}#{self.number}", config=self.config.tmux)
            else:
                spawn(full, title=f"review·{self.repo}#{self.number}", config=self.config.tmux)
        except RuntimeError as e:
            self.app.notify(str(e), severity="error")
            return
        where = f" in {cwd}" if cwd else ""
        self.app.notify(f"opened claude review pane for {self.repo}#{self.number}{where}", severity="information")

    def action_editor(self) -> None:
        cwd = self._resolve_local_dir()
        if not cwd:
            self.app.notify(
                f"no local clone of {self.repo} (set repo_paths or clone under {self.config.ui.repo_root})",
                severity="warning",
            )
            return
        editor = self.config.ui.editor_command
        try:
            spawn_in_dir(
                f"{editor} .",
                cwd=cwd,
                title=f"{editor}·{self.repo}#{self.number}",
                config=self.config.tmux,
            )
        except RuntimeError as e:
            self.app.notify(str(e), severity="error")
            return
        self.app.notify(f"opened {editor} for {self.repo}#{self.number} in {cwd}", severity="information")

    def _resolve_local_dir(self) -> str | None:
        resolver = getattr(self.app, "_resolve_repo_local_dir", None)
        if callable(resolver):
            return resolver(self.repo)
        return None

    @work(exclusive=False)
    async def action_refresh(self) -> None:
        await self._reload()
        self.app.notify("refreshed", severity="information", timeout=2)


class RepoDetailModal(ModalScreen[None]):
    """View a repo with editor/shell/claude actions."""

    DEFAULT_CSS = """
    RepoDetailModal {
        align: center middle;
        background: #000000 97%;
    }
    RepoDetailModal GradientPanel {
        background: #1c1c1e;
        width: 70%;
        height: auto;
        max-height: 70%;
    }
    RepoDetailModal #title { text-style: bold; color: white; }
    RepoDetailModal .desc { color: white; padding: 1 0 0 0; }
    RepoDetailModal .topics { color: $accent; padding: 0 0 1 0; }
    RepoDetailModal .chips { padding: 0 0 1 0; }
    RepoDetailModal .meta-band {
        background: $boost 5%;
        padding: 1 1;
        margin: 0 0 1 0;
    }
    RepoDetailModal .section-h {
        color: $accent;
        text-style: bold;
        padding: 0 0 0 1;
        border-left: thick $accent;
        margin: 1 0 0 0;
    }
    RepoDetailModal .clone-ok { color: $success; padding: 0 0 0 1; }
    RepoDetailModal .clone-missing { color: $warning; padding: 0 0 0 1; }
    RepoDetailModal .pr-row { padding: 0 0 0 1; }
    RepoDetailModal #footer-actions {
        padding: 1 0 0 0;
        color: $accent;
        text-style: bold;
    }
    RepoDetailModal #footer-close { color: $text-muted; }
    """

    BINDINGS = [  # noqa: RUF012
        Binding("escape,q", "close", "close", show=False),
        Binding("o", "open_browser", "browser", show=True),
        Binding("e", "open_editor", "editor", show=True),
        Binding("E", "open_editor", "editor", show=False),
        Binding("s", "open_shell", "shell", show=True),
        Binding("A", "open_claude", "claude", show=True),
        Binding("r", "refresh", "refresh", show=True),
    ]

    def __init__(self, name_with_owner: str, local_clone: str | None, config: Config):
        super().__init__()
        self.repo_name = name_with_owner
        self.local_clone = local_clone
        self.config = config
        self.detail: dict[str, Any] | None = None
        self.pulse: dict[str, Any] | None = None

    def compose(self) -> ComposeResult:
        yield GradientPanel()

    async def on_mount(self) -> None:
        panel = self.query_one(GradientPanel)
        self._title_static = Static(f"loading {self.repo_name}…", id="title")
        self.body = VerticalScroll()
        self._footer_actions = Static("", id="footer-actions", markup=True)
        self._footer_close = Static("", id="footer-close", markup=True)
        panel.mount_content(self._title_static, self.body, self._footer_actions, self._footer_close)
        await self._reload()

    async def _reload(self) -> None:
        try:
            self.detail = await asyncio.to_thread(repo_detail, self.repo_name)
        except GhError as e:
            self.app.notify(f"gh: {e}", severity="error")
            return
        # pulse is best-effort — don't block on failure
        try:
            self.pulse = await asyncio.to_thread(repo_pulse, self.repo_name)
        except Exception:
            self.pulse = None
        await self._redraw()

    async def _redraw(self) -> None:
        if not self.detail:
            return
        d = self.detail
        p = self.pulse or {}

        # ---- title row: name + visibility/state chips ----
        head = Text()
        head.append(self.repo_name, style="bold white")
        chips: list[tuple[str, str]] = []
        if d.get("isPrivate"):
            chips.append(("PRIVATE", "black on #f0c674"))
        else:
            chips.append(("PUBLIC", "black on #8ec07c"))
        if d.get("isArchived"):
            chips.append(("ARCHIVED", "black on #cc6666"))
        if d.get("isFork"):
            chips.append(("FORK", "black on #81a2be"))
        for label, style in chips:
            head.append("  ")
            head.append(f" {label} ", style=style)
        self._title_static.update(head)

        await self.body.remove_children()

        # ---- description ----
        desc = d.get("description") or "(no description)"
        self.body.mount(Static(desc, classes="desc", markup=False))

        # ---- topics ----
        topics_data = (d.get("repositoryTopics") or {})
        topic_nodes = topics_data.get("nodes") if isinstance(topics_data, dict) else topics_data
        topic_names = []
        if isinstance(topic_nodes, list):
            for t in topic_nodes:
                if isinstance(t, dict):
                    name = t.get("name") or ((t.get("topic") or {}).get("name"))
                    if name:
                        topic_names.append(name)
        if topic_names:
            topic_text = "  ".join(f"#{t}" for t in topic_names[:8])
            self.body.mount(Static(topic_text, classes="topics"))

        # ---- status chip row: stars / open PRs / open issues / forks ----
        # Two-tone chips: a colored label cap + a dim count cap, lipgloss-style.
        chip_line = Text()
        chip_count_bg = "#2d2d30"  # one notch above #1c1c1e modal background

        def chip(label: str, value: str, label_style: str) -> None:
            chip_line.append(f" {label} ", style=label_style)
            chip_line.append(f" {value} ", style=f"white on {chip_count_bg}")
            chip_line.append("  ")

        chip("★", str(d.get("stargazerCount", 0)), "black on #f0c674")
        open_prs = ((p.get("openPRs") or {}).get("totalCount"))
        if open_prs is not None:
            chip("PRs", str(open_prs), "black on #8ec07c")
        open_issues = ((p.get("openIssues") or {}).get("totalCount"))
        if open_issues is not None:
            chip("issues", str(open_issues), "black on #81a2be")
        if d.get("forkCount") is not None:
            chip("forks", str(d.get("forkCount")), "black on #b294bb")
        self.body.mount(Static(chip_line, classes="chips"))

        # ---- meta band: 2-col compact key/val ----
        primary_lang = (d.get("primaryLanguage") or {}).get("name", "—")
        default_branch = (d.get("defaultBranchRef") or {}).get("name", "—")
        license_name = (d.get("licenseInfo") or {}).get("name") or "—"
        pushed = relative_time(d.get("pushedAt"))
        created = relative_time(d.get("createdAt"))
        homepage = d.get("homepageUrl") or ""

        meta_lines: list[Text] = []

        def kv(k: str, v: str) -> Text:
            t = Text()
            t.append(f"{k:<10}", style="dim")
            t.append(v, style="white")
            return t

        row1 = Text()
        row1.append_text(kv("language", primary_lang))
        row1.append("    ")
        row1.append_text(kv("default", default_branch))
        row1.append("    ")
        row1.append_text(kv("license", license_name))
        meta_lines.append(row1)

        row2 = Text()
        row2.append_text(kv("pushed", pushed))
        row2.append("    ")
        row2.append_text(kv("created", created))
        meta_lines.append(row2)

        if homepage:
            meta_lines.append(kv("homepage", homepage))

        meta_text = Text("\n").join(meta_lines)
        self.body.mount(Static(meta_text, classes="meta-band"))

        # ---- local clone status ----
        self.body.mount(Static("local", classes="section-h"))
        if self.local_clone:
            self.body.mount(Static(f"✓ {self.local_clone}", classes="clone-ok"))
        else:
            hint = Text()
            hint.append("✗ not cloned", style="bold")
            hint.append(f"  ·  gh repo clone {self.repo_name}", style="dim")
            self.body.mount(Static(hint, classes="clone-missing"))

        # ---- recent open PRs ----
        recent_nodes = ((p.get("recentPRs") or {}).get("nodes")) or []
        if recent_nodes:
            self.body.mount(Static(f"open PRs ({len(recent_nodes)})", classes="section-h"))
            for pr in recent_nodes:
                line = Text()
                line.append(f"#{pr.get('number')}", style="bold #ff3e8e")
                line.append("  ")
                title = pr.get("title") or ""
                if pr.get("isDraft"):
                    line.append("[draft] ", style="dim italic")
                line.append(title[:80], style="white")
                line.append("  ")
                author = ((pr.get("author") or {}).get("login")) or ""
                if author:
                    line.append(f"@{author}", style="dim")
                line.append("  ")
                line.append(relative_time(pr.get("updatedAt")), style="dim")
                self.body.mount(Static(line, classes="pr-row"))

        # ---- footer (markup so $accent text-style from CSS rule applies) ----
        self._footer_actions.update(
            "[bold]o[/] browser  ·  [bold]e[/] editor  ·  [bold]s[/] shell  ·  [bold]A[/] claude"
        )
        self._footer_close.update("[bold]r[/] refresh  ·  [bold]esc[/] close")

    def action_refresh(self) -> None:
        self.run_worker(self._reload(), exclusive=True)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_open_browser(self) -> None:
        import webbrowser
        url = (self.detail or {}).get("url", f"https://github.com/{self.repo_name}")
        webbrowser.open(url)
        self.app.notify(f"opened {self.repo_name}", severity="information")

    def action_open_editor(self) -> None:
        if not self.local_clone:
            self.app.notify(f"no local clone of {self.repo_name} under {self.config.ui.repo_root}", severity="warning")
            return
        editor = self.config.ui.editor_command
        try:
            spawn_in_dir(f"{editor} .", cwd=self.local_clone, title=self.repo_name, config=self.config.tmux)
        except RuntimeError as e:
            self.app.notify(str(e), severity="error")
            return
        self.app.notify(f"opened {editor} in {self.local_clone}", severity="information")

    def action_open_shell(self) -> None:
        if not self.local_clone:
            self.app.notify(f"no local clone of {self.repo_name}", severity="warning")
            return
        shell = os.environ.get("SHELL", "/bin/sh")
        try:
            spawn_in_dir(shell, cwd=self.local_clone, title=self.repo_name, config=self.config.tmux)
        except RuntimeError as e:
            self.app.notify(str(e), severity="error")
            return
        self.app.notify(f"opened shell in {self.local_clone}", severity="information")

    def action_open_claude(self) -> None:
        cwd = self.local_clone
        if not cwd:
            self.app.notify(f"no local clone of {self.repo_name}", severity="warning")
            return
        claude = self.config.ai.claude_path
        try:
            spawn_in_dir(claude, cwd=cwd, title=f"claude·{self.repo_name}", config=self.config.tmux)
        except RuntimeError as e:
            self.app.notify(str(e), severity="error")
            return
        self.app.notify(f"opened claude in {cwd}", severity="information")


MODAL_BACKDROP = "#1a1a1d"  # near-black grey, sampled from Lipgloss reference

# Lipgloss-style perimeter gradient stops (clockwise from top-left).
LIPGLOSS_STOPS = ["#ff3e8e", "#6f47ff", "#52e5a0", "#6f47ff", "#ff3e8e"]


def _dim_rgb(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)  # type: ignore[return-value]


class GradientPanel(Vertical):
    """A container with a per-character gradient border.

    Implementation: layers. The border is a single Static rendering a multi-line
    Text covering the full panel size; each character on the perimeter is
    independently styled by interpolating `stops` clockwise. The content layer
    holds the actual widgets, offset 1 cell from each edge so the border shows
    around it. Re-paints on resize and on focus-within change.

    `panel_title` (if set) is painted into the top edge as ` Title `.
    `dim_when_unfocused` reduces border saturation when no descendant has focus.
    """

    DEFAULT_CSS = """
    GradientPanel {
        layers: border content;
    }
    GradientPanel > #gp-border {
        layer: border;
        width: 100%;
        height: 100%;
        background: transparent;
    }
    GradientPanel > #gp-content {
        layer: content;
        width: 1fr;
        height: 1fr;
        padding: 0 1;
        margin: 1 1;
        background: transparent;
    }
    """

    def __init__(
        self,
        *children: Any,
        stops: list[str] | None = None,
        panel_title: str = "",
        dim_when_unfocused: bool = True,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.stops_hex = stops or LIPGLOSS_STOPS
        self.panel_title = panel_title
        self.dim_when_unfocused = dim_when_unfocused
        self._children_to_mount = children
        self._border: Static | None = None
        self._content: Vertical | None = None

    def compose(self) -> ComposeResult:
        self._border = Static("", id="gp-border")
        # Pass children directly to the content Vertical so they're mounted
        # as part of the normal compose cycle (no race with layer ordering).
        self._content = Vertical(*self._children_to_mount, id="gp-content")
        yield self._border
        yield self._content

    def mount_content(self, *widgets: Any) -> None:
        if self._content is not None:
            self._content.mount(*widgets)

    def on_resize(self) -> None:
        self._repaint_border()

    def on_mount(self) -> None:
        self._repaint_border()

    def on_descendant_focus(self) -> None:
        self._repaint_border()

    def on_descendant_blur(self) -> None:
        self._repaint_border()

    def _repaint_border(self) -> None:
        if self._border is None:
            return
        w = self.size.width
        h = self.size.height
        if w < 3 or h < 3:
            return
        # Determine focus-driven brightness factor.
        focused = self.has_focus_within if self.dim_when_unfocused else True
        factor = 1.0 if focused else 0.45
        # Auto-palindromize if the palette doesn't already loop back to its
        # start color — otherwise the perimeter sweep produces a visible seam
        # at the top-left corner where stops[-1] meets stops[0].
        stops_hex = self.stops_hex
        if len(stops_hex) >= 2 and stops_hex[0] != stops_hex[-1]:
            stops_hex = list(stops_hex) + list(stops_hex[-2::-1])
        stops = [_dim_rgb(hex_to_rgb(s), factor) for s in stops_hex]

        perimeter = 2 * (w - 1) + 2 * (h - 1)
        TL, TR = 0, w - 1
        BR, BL = w + h - 2, 2 * w + h - 3

        out = Text(no_wrap=True, overflow="ignore")

        # Top row — rounded corners
        out.append("╭", style=f"{_pcolor(TL, perimeter, stops)} bold")
        # Title insertion: " Title " starting at column 2. Color each char
        # using the border gradient at its perimeter position, so the title
        # blends with the border sweep and uses the panel's palette.
        title = f" {self.panel_title} " if self.panel_title else ""
        title_chars = list(title)
        for i in range(1, w - 1):
            if title_chars and i >= 2 and i < 2 + len(title):
                ch = title_chars[i - 2]
                out.append(ch, style=f"{_pcolor(i, perimeter, stops)} bold")
            else:
                out.append("─", style=f"{_pcolor(i, perimeter, stops)} bold")
        out.append("╮", style=f"{_pcolor(TR, perimeter, stops)} bold")
        out.append("\n")

        # Middle rows: │ + spaces + │
        for r in range(h - 2):
            right_idx = w + r
            left_idx = (2 * w + 2 * h - 5) - r
            out.append("│", style=f"{_pcolor(left_idx, perimeter, stops)} bold")
            out.append(" " * (w - 2))
            out.append("│", style=f"{_pcolor(right_idx, perimeter, stops)} bold")
            out.append("\n")

        # Bottom row
        out.append("╰", style=f"{_pcolor(BL, perimeter, stops)} bold")
        for i in range(1, w - 1):
            out.append("─", style=f"{_pcolor(BL - i, perimeter, stops)} bold")
        out.append("╯", style=f"{_pcolor(BR, perimeter, stops)} bold")

        self._border.update(out)

    @property
    def has_focus_within(self) -> bool:
        """True if this panel or any descendant currently has focus."""
        screen = self.app.screen if self.app else None
        focused = screen.focused if screen else None
        if focused is None:
            return False
        node: Any = focused
        while node is not None:
            if node is self:
                return True
            node = getattr(node, "parent", None)
        return False


def _pcolor(idx: int, perimeter: int, stops: list[tuple[int, int, int]]) -> str:
    from jg.gradient import perimeter_color
    return perimeter_color(idx, perimeter, stops)


class HelpScreen(ModalScreen[None]):
    """Lipgloss-style modal: solid dark backdrop + true gradient border (per-char)."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
        background: #000000 97%;
    }
    HelpScreen GradientPanel {
        background: #1c1c1e;
        width: 70;
        height: auto;
        max-height: 80%;
    }
    """

    BINDINGS = [Binding("escape,question_mark,q", "dismiss_close", "close", show=False)]  # noqa: RUF012

    HELP = [  # noqa: RUF012
        ("h / l / ←→", "cycle Projects → Kanban cols → Sidebar"),
        ("j / k / ↑↓", "navigate within column / list"),
        ("enter (project)", "set project filter · scopes kanban + PRs + repos"),
        ("1 / 2 / 3 / 4", "Kanban tabs: sprint / backlog / all / recent"),
        ("[ / ]", "cycle tabs of focused detail (Kanban or Code)"),
        ("tab", "swap Kanban ⇄ Code (medium/narrow widths)"),
        ("esc", "narrow-mode: detail → projects picker"),
        ("enter (item)", "open detail modal (ticket / PR / repo)"),
        ("t / a / c", "transition / assign / comment (ticket-only)"),
        ("A", "claude pane: /issue (ticket) · /review (PR) · in dir (repo)"),
        ("E", "editor on focused ticket / PR / repo (ticket shares A's dir cache)"),
        ("ctrl+a / ctrl+e", "re-open dir picker, bypass cached choice"),
        ("o", "open in browser"),
        ("e / s", "(on Repos tab) editor / shell in tmux split"),
        ("/", "filter cards"),
        ("ctrl+p", "command palette (incl. theme cycle)"),
        ("r", "refresh · ?  this help · q  quit"),
        ("inside detail modal", "t a c · e summary · d description · T tests · p priority · l labels · o A E"),
        ("claudecode.nvim", "press E (editor) before A (claude) so nvim is up first"),
    ]

    def compose(self) -> ComposeResult:
        yield GradientPanel()

    def on_mount(self) -> None:
        panel = self.query_one(GradientPanel)
        widgets: list[Static] = [
            Static(banner("Keybindings", palette="charm")),
            Static(""),
        ]
        for k, v in self.HELP:
            row = Text()
            row.append("  ")
            row.append_text(gradient_text(f"{k:<18}", *CHROME_GRADIENT, bold=True))
            row.append(v, style="dim")
            widgets.append(Static(row))
        widgets.append(Static(""))
        widgets.append(Static("[dim]esc to close[/]", markup=True))
        panel.mount_content(*widgets)

    def action_dismiss_close(self) -> None:
        self.dismiss(None)


# ───────────────────────────── command palette ─────────────────────────────

class ChCommands(Provider):
    """Custom command palette commands for ch dashboard actions."""

    @property
    def app_actions(self) -> list[tuple[str, str, str]]:
        # (name, help, app-action-name)
        return [
            ("Refresh", "Reload sprint + PRs", "refresh"),
            ("Open ticket", "Show details for the focused ticket", "open_detail"),
            ("Transition", "Move focused ticket to a new status", "transition"),
            ("Assign", "Reassign focused ticket", "assign"),
            ("Comment", "Comment on focused ticket", "comment"),
            ("Open in browser", "Open focused ticket or PR", "open_browser"),
            ("Brainstorm", "Open Claude with project context for new tickets", "brainstorm"),
            ("Filter", "Filter cards by key or summary", "focus_filter"),
            ("Cycle theme", "Cycle through built-in Textual themes", "cycle_theme"),
            ("Help", "Show keybindings", "help"),
            ("Quit", "Exit", "quit"),
        ]

    async def discover(self) -> Hits:
        for name, help_, action in self.app_actions:
            yield DiscoveryHit(name, partial(self.app.run_action, action), help=help_)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, help_, action in self.app_actions:
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), partial(self.app.run_action, action), help=help_)


# ───────────────────────────── main app ─────────────────────────────

class ChDashboard(App):
    CSS = """
    App { background: transparent; }
    /* layout only — background/tint are set programmatically on the
       dashboard's own screen in on_mount, so this rule must NOT touch
       background here (it would also match ModalScreen and clobber each
       modal's overlay, which is why the lipgloss fade never worked). */
    Screen {
        layout: vertical;
    }
    /* Suppress the theme's background_tint on modal panels so the
       declared #1c1c1e renders true to color (Textual otherwise blends
       a few percent of the theme accent in, brightening the box). */
    ModalScreen GradientPanel { background-tint: 0%; }
    Header { background: transparent; color: $primary; text-style: bold; }
    HeaderIcon:hover { background: transparent; }
    Footer { background: transparent; }
    Footer > .footer--key { color: $primary; text-style: bold; }
    #view-row {
        height: 1;
        background: transparent;
        color: $foreground;
        padding: 0 1;
    }
    #search-row {
        height: 0;
        background: transparent;
        padding: 0;
    }
    #search-row.shown {
        height: 3;
    }
    #main {
        layout: horizontal;
        height: 1fr;
        background: transparent;
    }
    #projects-panel {
        width: 24;
        height: 1fr;
        background: transparent;
        margin: 0 1 0 0;
        padding: 0;
    }
    #projects-panel .panel-title {
        text-style: bold;
        padding: 0 1;
        height: 1;
    }
    #project-list {
        height: 1fr;
        background: transparent;
    }
    #project-list > ListItem {
        background: transparent;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    /* Project list highlight uses the same focus-aware pair as ListView below. */
    #projects-empty-hint {
        padding: 1;
        color: $text-muted;
    }
    #kanban-wrap {
        width: 1fr;
        height: 1fr;
        layout: vertical;
        background: transparent;
        margin: 0 1 0 0;
    }
    #kanban-stack {
        layout: vertical;
        width: 1fr;
        height: 1fr;
        background: transparent;
    }
    /* Kanban Tabs widget — match the sidebar TabbedContent strip exactly. */
    #kanban-tabs {
        background: transparent;
    }
    #kanban-tabs Tab { padding: 0 2; color: $text-muted; }
    #kanban-tabs Tab.-active { color: $accent; text-style: bold; }
    #kanban {
        layout: horizontal;
        width: 1fr;
        height: 1fr;
        padding: 0;
        scrollbar-size-horizontal: 1;
        scrollbar-size-vertical: 0;
        overflow-x: auto;
        overflow-y: hidden;
    }
    /* KanbanColumn's own DEFAULT_CSS handles border + focus width; no dashboard-level overrides. */
    KanbanColumn > .col-title {
        background: transparent;
        text-style: bold;
        padding: 0 1;
        height: 1;
    }
    KanbanColumn > .col-rule {
        height: 1;
        padding: 0 1;
        color: $primary 40%;
    }
    ListView {
        background: transparent;
    }
    ListView > ListItem {
        background: transparent;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    /* Highlight is focus-aware: the focused panel gets a strong selection
       color; unfocused panels keep a faint cursor-position marker so you
       can see where j/k will land if you tab back. Without this split,
       every ListView paints its highlighted_child identically and it looks
       like multiple things are selected at once. */
    ListView > ListItem.-highlight {
        background: $boost 8%;
    }
    ListView:focus-within > ListItem.-highlight {
        background: $boost 30%;
        color: $accent;
    }
    #sidebar {
        width: 40;
        height: 1fr;
        background: transparent;
    }
    TabbedContent { background: transparent; }
    Tabs > Tab { padding: 0 2; color: $text-muted; }
    Tabs > Tab.-active { color: $accent; text-style: bold; }
    #status {
        height: 1;
        background: transparent;
        color: $foreground;
        padding: 0 1;
        border-top: solid $primary 30%;
    }
    Toast {
        padding: 0 1;
        background: $panel;
        border-left: thick $accent;
    }
    Toast.-error { border-left: thick $error; }
    Toast.-warning { border-left: thick $warning; }
    Toast.-information { border-left: thick $success; }
    /* Medium-width responsive: hide whichever detail is not active.
       Toggled imperatively in _apply_width_bucket(). */
    #kanban-wrap.-detail-hidden, #sidebar.-detail-hidden { display: none; }
    /* At medium width, Projects shrinks to give the active detail more room. */
    #projects-panel.-compact { width: 20; }
    /* Narrow-width drill-down: at .-narrow-picker the Projects panel is the
       only thing shown (full width); at .-narrow-detail it's hidden and the
       active detail spans full width with a breadcrumb. */
    #projects-panel.-narrow-picker { width: 1fr; }
    #projects-panel.-narrow-hidden { display: none; }
    """

    BINDINGS = [  # noqa: RUF012
        Binding("q", "quit", "quit", priority=True, show=True),
        Binding("r", "refresh", "refresh", show=True),
        Binding("h", "focus_prev_col", "←", show=False),
        Binding("left", "focus_prev_col", "←", show=False),
        Binding("l", "focus_next_col", "→", show=False),
        Binding("right", "focus_next_col", "→", show=False),
        Binding("t", "transition", "transition", show=True),
        Binding("a", "assign", "assign", show=True),
        Binding("c", "comment", "comment", show=True),
        Binding("A", "ai_on_card", "AI", show=True),
        Binding("E", "editor_on_card", "editor", show=True),
        Binding("ctrl+a", "ai_on_card_force", "re-pick AI dir", show=False),
        Binding("ctrl+e", "editor_on_card_force", "re-pick editor dir", show=False),
        Binding("B", "brainstorm", "brainstorm", show=True),
        Binding("enter", "open_detail", "open", show=True),
        Binding("o", "open_browser", "browser", show=True),
        Binding("slash", "focus_filter", "filter", show=True),
        Binding("question_mark", "help", "help", show=True),
        Binding("escape", "exit_filter", "exit filter", show=False),
        Binding("1", "view_sprint", "sprint", show=False),
        Binding("2", "view_backlog", "backlog", show=False),
        Binding("3", "view_all", "all open", show=False),
        Binding("4", "view_recent", "recent done", show=False),
        Binding("e", "open_repo_editor", "edit repo", show=False),
        Binding("s", "open_repo_shell", "repo shell", show=False),
        Binding("right_square_bracket", "next_sidebar_tab", "next tab", show=False),
        Binding("left_square_bracket", "prev_sidebar_tab", "prev tab", show=False),
        Binding("tab", "swap_detail", "kanban⇄code", show=False, priority=True),
        Binding("shift+tab", "swap_detail", "kanban⇄code", show=False, priority=True),
    ]

    last_refresh: reactive[str] = reactive("never")
    sprint_count: reactive[int] = reactive(0)
    view_mode: reactive[str] = reactive("sprint")  # sprint | backlog | all | recent
    current_project_name: reactive[str] = reactive("All")  # display label only
    active_detail: reactive[str] = reactive("kanban")  # kanban | code (only matters at medium width)
    width_bucket: reactive[str] = reactive("wide")     # wide | medium | narrow
    narrow_view: reactive[str] = reactive("picker")    # picker | detail (only matters at narrow width)

    # Width thresholds. Wide = both details visible; Medium = one detail at a
    # time + Projects column; Narrow = drill-down (Projects full-width picker,
    # then detail full-width with breadcrumb).
    NARROW_WIDTH = 100
    MEDIUM_WIDTH = 140

    COMMANDS = App.COMMANDS | {ChCommands}

    AUTO_REFRESH_SECONDS = 120  # safety net; primary refresh is focus-driven via on_app_focus
    FOCUS_REFRESH_DEBOUNCE = 5   # seconds — coalesce rapid pane flicks
    STALE_AFTER_SECONDS = 600   # 10 min — status bar warning

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.columns: dict[str, KanbanColumn] = {}
        self.mine_pr: PRList | None = None
        self.rr_pr: PRList | None = None
        self.search_input: Input | None = None
        self.search_row: Vertical | None = None
        self.status: Static | None = None
        self._last_refresh_at: float = 0.0
        self._repos_loaded: bool = False
        self.current_project: Project | None = None  # None = All
        self.project_filter: str = "all"  # "all" | "<project name>" | "unassigned"
        # Notification state — track previously-seen items so we only notify on
        # changes between refreshes. First load primes the set without notifying.
        self._notify_seeded: bool = False
        self._known_review_pr_urls: set[str] = set()
        self._known_my_pr_decisions: dict[str, str] = {}  # url -> reviewDecision
        self._known_sprint_keys: set[str] = set()
        self._known_ticket_status: dict[str, str] = {}    # key -> status name
        # Process-lifetime cache: ticket key → chosen local dir. Shared between
        # `A` (claude) and `E` (editor) so claudecode.nvim integration wires up
        # without re-prompting. Cleared when the dashboard restarts.
        self._ticket_dir_cache: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        self.view_row = Static(self._view_label(), id="view-row")
        yield self.view_row
        self.search_row = Vertical(id="search-row")
        with self.search_row:
            self.search_input = Input(placeholder="filter — type to filter cards, esc to clear", id="search-input")
            yield self.search_input
        with Horizontal(id="main"):
            # Projects panel
            self.project_list = ProjectList(id="project-list")
            projects_children: list[Any] = [self.project_list]
            if not self.config.projects:
                projects_children.append(Static(
                    "[dim]No projects defined.\nAdd [[projects]] blocks to\n~/.config/jg/config.toml.[/]",
                    id="projects-empty-hint",
                    markup=True,
                ))
            yield GradientPanel(*projects_children, panel_title="Projects", id="projects-panel")

            # Kanban (Tasks) panel — tab strip on top (real Tabs widget so it
            # matches the Code panel's TabbedContent visually), then 6 columns.
            for group in GROUPS_TO_SHOW:
                self.columns[group] = KanbanColumn(group)
            self.kanban_tabs = Tabs(
                Tab("active sprint", id="sprint"),
                Tab("backlog", id="backlog"),
                Tab("all open", id="all"),
                Tab("recently closed", id="recent"),
                id="kanban-tabs",
            )
            self.kanban_tabs.can_focus = False  # h/l should never land here
            kanban_inner = Vertical(
                self.kanban_tabs,
                KanbanScroll(*self.columns.values(), id="kanban"),
                id="kanban-stack",
            )
            yield GradientPanel(kanban_inner, panel_title="Tasks", id="kanban-wrap")

            # Code sidebar
            self.mine_pr = PRList(id="prs-mine")
            self.rr_pr = PRList(id="prs-rr")
            self.repo_list = RepoList(id="repos")
            # TabbedContent doesn't accept TabPane positional args; build
            # it via a helper compose.
            tabs = _SidebarTabs(self.mine_pr, self.rr_pr, self.repo_list)
            yield GradientPanel(tabs, panel_title="Code", id="sidebar")
        self.status = Static("loading…", id="status")
        yield self.status
        yield Footer()

    def _view_label(self) -> Text:
        out = Text()
        # Brand prefix — uses the reserved chrome gradient (gold/amber) so it
        # doesn't collide with any kanban column.
        out.append_text(gradient_text("jg ", *CHROME_GRADIENT, bold=True))
        out.append(" · ", style="dim")
        # Narrow-mode picker shows just the brand + a hint; detail mode shows
        # a project > detail breadcrumb with esc-to-back.
        if self.width_bucket == "narrow" and self.narrow_view == "picker":
            out.append("pick a project ", style="dim")
            out.append("(enter)", style="dim")
            return out
        if self.width_bucket == "narrow" and self.narrow_view == "detail":
            out.append_text(gradient_text(self.current_project_name, *CHROME_GRADIENT, bold=True))
            out.append(" › ", style="dim")  # noqa: RUF001
            detail_label = "Tickets" if self.active_detail == "kanban" else "Code"
            out.append_text(gradient_text(detail_label, *CHROME_GRADIENT, bold=True))
            out.append("    ", style="dim")
            other = "Code" if self.active_detail == "kanban" else "Tickets"
            out.append(f"tab → {other}  ·  esc ← projects", style="dim")
            return out
        out.append("project: ", style="dim")
        out.append_text(gradient_text(self.current_project_name, *CHROME_GRADIENT, bold=True))
        if self.width_bucket == "medium":
            inactive = "Code" if self.active_detail == "kanban" else "Kanban"
            active = "Kanban" if self.active_detail == "kanban" else "Code"
            out.append("  · ", style="dim")
            out.append_text(gradient_text(active, *CHROME_GRADIENT, bold=True))
            out.append(f" ⇄ {inactive} ", style="dim")
            out.append("(tab)", style="dim")
        out.append("    ", style="dim")
        out.append("/  filter  · ?  help  · ctrl+p  palette", style="dim")
        return out


    def on_mount(self) -> None:
        self.title = "jg"
        self.sub_title = "kanban · PRs · repos"
        for t in ALL_THEMES:
            try:
                self.register_theme(t)
            except Exception:
                pass
        try:
            self.theme = self.config.ui.theme  # type: ignore[attr-defined]
        except Exception:
            pass
        # Force-clear any theme-imposed Screen background so the terminal
        # color shows through. CSS alone gets overridden by the theme.
        try:
            self.screen.styles.background = "transparent"
            self.screen.styles.background_tint = "transparent"
        except Exception:
            pass
        self.call_after_refresh(self._populate_projects_sync)
        self.call_after_refresh(lambda: self._apply_width_bucket(self.size.width, force=True))
        # Sync Kanban tabs widget to current view_mode (default sprint).
        self.call_after_refresh(self._sync_kanban_tab)
        # Suppress the Header's command-palette icon tooltip — it triggers
        # randomly on stray pointer crossings and is purely noise (ctrl+p
        # is documented elsewhere).
        self.call_after_refresh(self._suppress_header_icon_tooltip)
        self._refresh()
        self.set_interval(self.AUTO_REFRESH_SECONDS, self._auto_refresh)
        self.set_interval(15, self._update_status)

    def _sync_kanban_tab(self) -> None:
        if hasattr(self, "kanban_tabs") and self.kanban_tabs is not None:
            try:
                self.kanban_tabs.active = self.view_mode
            except Exception:
                pass

    def _suppress_header_icon_tooltip(self) -> None:
        try:
            from textual.widgets._header import HeaderIcon
            for icon in self.query(HeaderIcon):
                icon.tooltip = None
        except Exception:
            pass

    def on_resize(self, event) -> None:
        # Only act on bucket *crossings* — avoids thrash during a drag-resize.
        self._apply_width_bucket(event.size.width)

    def _bucket_for(self, width: int) -> str:
        if width < self.NARROW_WIDTH:
            return "narrow"
        if width < self.MEDIUM_WIDTH:
            return "medium"
        return "wide"

    def _apply_width_bucket(self, width: int, force: bool = False) -> None:
        bucket = self._bucket_for(width)
        if not force and bucket == self.width_bucket:
            return
        prev_bucket = self.width_bucket
        self.width_bucket = bucket
        # Crossing *into* narrow defaults to the picker. Crossing *out* of
        # narrow resets narrow_view so re-entry starts fresh.
        if bucket == "narrow" and prev_bucket != "narrow":
            self.narrow_view = "picker"
        try:
            projects = self.query_one("#projects-panel")
            kanban = self.query_one("#kanban-wrap")
            sidebar = self.query_one("#sidebar")
        except Exception:
            return
        # Reset all responsive classes first.
        for cls in ("-compact", "-narrow-picker", "-narrow-hidden"):
            projects.set_class(False, cls)
        kanban.set_class(False, "-detail-hidden")
        sidebar.set_class(False, "-detail-hidden")

        if bucket == "wide":
            pass  # all panels at their default sizes
        elif bucket == "medium":
            projects.set_class(True, "-compact")
            kanban.set_class(self.active_detail != "kanban", "-detail-hidden")
            sidebar.set_class(self.active_detail != "code", "-detail-hidden")
        else:  # narrow — drill-down picker/detail toggle
            if self.narrow_view == "picker":
                projects.set_class(True, "-narrow-picker")
                kanban.set_class(True, "-detail-hidden")
                sidebar.set_class(True, "-detail-hidden")
            else:  # detail
                projects.set_class(True, "-narrow-hidden")
                kanban.set_class(self.active_detail != "kanban", "-detail-hidden")
                sidebar.set_class(self.active_detail != "code", "-detail-hidden")
        self._reseat_focus_after_layout()

    def _reseat_focus_after_layout(self) -> None:
        focused = self.focused
        if focused is None:
            return
        node = focused
        owner_id: str | None = None
        while node is not None:
            nid = getattr(node, "id", None)
            if nid in ("kanban-wrap", "sidebar", "projects-panel"):
                owner_id = nid
                break
            node = node.parent
        try:
            if owner_id == "kanban-wrap" and self.query_one("#kanban-wrap").has_class("-detail-hidden"):
                self._focus_first_in_active_detail()
            elif owner_id == "sidebar" and self.query_one("#sidebar").has_class("-detail-hidden"):
                self._focus_first_in_active_detail()
        except Exception:
            pass

    def _focus_first_in_active_detail(self) -> None:
        if self.active_detail == "kanban":
            cols = self._kanban_columns()
            if cols and cols[0].list_view is not None:
                cols[0].list_view.focus()
        else:
            try:
                tabs = self.query_one(TabbedContent)
                pane = tabs.active_pane
                if pane is not None:
                    for lv in pane.query(ListView):
                        lv.focus()
                        return
            except Exception:
                pass

    def action_swap_detail(self) -> None:
        # Tab key: swap which detail is active. At wide width both are visible
        # so this is a no-op visually but still moves focus across.
        self.active_detail = "code" if self.active_detail == "kanban" else "kanban"
        self._apply_width_bucket(self.size.width, force=True)
        self._focus_first_in_active_detail()
        if hasattr(self, "view_row") and self.view_row is not None:
            self.view_row.update(self._view_label())
        self.refresh_bindings()

    def watch_width_bucket(self, _old: str, _new: str) -> None:
        if hasattr(self, "view_row") and self.view_row is not None:
            self.view_row.update(self._view_label())

    def _populate_projects_sync(self) -> None:
        # Wrapper that schedules the async populate via the worker system.
        self.run_worker(self._populate_projects(), exclusive=False)

    async def _populate_projects(self) -> None:
        try:
            pl = self.query_one("#project-list", ProjectList)
        except Exception:
            return
        await pl.set_projects(self.config.projects)

    def _auto_refresh(self) -> None:
        # Only refresh if no modal is up and the user hasn't typed in 30s.
        if self.screen_stack and len(self.screen_stack) > 1:
            return
        self._refresh()

    def on_app_focus(self, event: events.AppFocus) -> None:
        # Refresh when the dashboard pane regains terminal focus — catches
        # external mutations (e.g. Claude creating a Jira ticket via MCP in
        # another pane). Requires `set -s focus-events on` in ~/.tmux.conf.
        # Debounced so quick pane flicks don't hammer the API.
        if self.screen_stack and len(self.screen_stack) > 1:
            return
        if time.time() - self._last_refresh_at < self.FOCUS_REFRESH_DEBOUNCE:
            return
        self._refresh()

    # ── refresh ────────────────────────────────────────────

    def _notify_auth_error(self, e: AuthError) -> None:
        if e.needs_relogin:
            self.notify(
                f"⚠ Atlassian session expired — run [bold]ch auth login[/]\n[dim]({e})[/]",
                severity="error",
                timeout=15,
            )
        elif e.transient:
            self.notify(f"transient auth error: {e}", severity="warning", timeout=6)
        else:
            self.notify(f"auth error: {e}", severity="error", timeout=10)

    @work(exclusive=True)
    async def _refresh(self) -> None:
        if self.status:
            self.status.update("[yellow]loading sprint + PRs…[/]")
        try:
            await asyncio.gather(self._load_sprint(), self._load_prs())
        except AuthError as e:
            self._notify_auth_error(e)
            if self.status:
                self.status.update("[red]auth error — run ch auth login[/]")
            return
        except Exception as e:
            if self.status:
                self.status.update(f"[red]error: {e}[/]")
            return
        import datetime as dt
        self.last_refresh = dt.datetime.now().strftime("%H:%M:%S")
        self._last_refresh_at = time.time()
        self._update_status()

    def _update_status(self) -> None:
        if not self.status:
            return
        age = int(time.time() - self._last_refresh_at) if self._last_refresh_at else None
        out = Text()
        if age is None:
            out.append("● ", style="#6272a4")
            out.append("never refreshed ", style="dim")
        elif age > self.STALE_AFTER_SECONDS:
            out.append_text(gradient_text("● stale", "#ffb86c", "#ff5555", bold=True))
            out.append(f" {self.last_refresh} ({age // 60}m ago)  ", style="dim")
        else:
            out.append_text(gradient_text("●", "#7be77b", "#50fa7b", bold=True))
            out.append(" refreshed ", style="dim")
            out.append_text(gradient_text(self.last_refresh, "#7be77b", "#8be9fd", bold=True))
            out.append("  ")
        out.append("· ", style="dim")
        out.append_text(gradient_text(f"{self.sprint_count}", "#ff79c6", "#bd93f9", bold=True))
        out.append(" issues  ", style="dim")
        filt = self.search_input.value if self.search_input else ""
        if filt:
            out.append("· ", style="dim")
            out.append("filter: ", style="dim")
            out.append_text(gradient_text(filt, "#ff5bc0", "#ffb86c", bold=True))
            out.append("  ")
        out.append("· q quit · r refresh · ? help · ctrl+p palette", style="dim")
        self.status.update(out)

    def _current_jql(self) -> str:
        # Base view filter
        if self.view_mode == "backlog":
            base = "assignee = currentUser() AND sprint is EMPTY AND statusCategory != Done"
            order = "ORDER BY priority DESC, updated DESC"
        elif self.view_mode == "all":
            base = "assignee = currentUser() AND statusCategory != Done"
            order = "ORDER BY status ASC, priority DESC, updated DESC"
        elif self.view_mode == "recent":
            base = "assignee = currentUser() AND statusCategory = Done AND resolved >= -7d"
            order = "ORDER BY resolved DESC"
        else:  # sprint
            base = "assignee = currentUser() AND sprint in openSprints()"
            order = "ORDER BY status ASC, priority DESC, updated DESC"
        # Layer the current project's JQL on top, if any.
        if self.current_project and self.current_project.jql.strip():
            base = f"({base}) AND ({self.current_project.jql.strip()})"
        return f"{base} {order}"

    async def _load_sprint(self) -> None:
        try:
            async with JiraClient(self.config) as api:
                data = await api.search_jql(
                    self._current_jql(),
                    fields=["summary", "status", "priority", "issuetype", "updated"],
                    max_results=100,
                )
        except AuthError:
            raise  # bubble to _refresh's auth handler
        except ApiError as e:
            self.notify(f"Jira: {e}", severity="error", timeout=8)
            return
        # Diff before scoping (notifications track all assigned tickets, not
        # filtered project view).
        self._diff_and_notify_tickets(data.get("issues", []) or [])
        groups: dict[str, list[dict]] = {g: [] for g in GROUPS_TO_SHOW}
        issues = data.get("issues", [])
        for issue in issues:
            status = (issue["fields"].get("status") or {}).get("name", "Unknown")
            grp = normalize_status(status)
            if grp in groups:
                groups[grp].append(issue)
        self.sprint_count = len(issues)
        for grp, items in groups.items():
            await self.columns[grp].set_issues(items)
        # Re-apply current filter if any.
        if self.search_input and self.search_input.value:
            await self._apply_filter(self.search_input.value)

    async def _load_prs(self) -> None:
        try:
            mine, rr = await asyncio.gather(
                asyncio.to_thread(my_open_prs),
                asyncio.to_thread(review_requested_prs),
            )
        except GhError as e:
            self.notify(f"gh: {e}", severity="warning", timeout=6)
            return
        # Diff against last-seen state and emit notifications BEFORE scoping
        # (so a project change doesn't fire false notifications).
        self._diff_and_notify_prs(mine, rr)
        # Compute per-project review-requested counts off the unscoped list,
        # so the picker shows badges for every project regardless of current
        # filter.
        self._update_project_badges(rr)
        # Scope to current project's repos if one is selected.
        mine = self._scope_prs(mine)
        rr = self._scope_prs(rr)
        try:
            mine_pr = self.query_one("#prs-mine", PRList)
            rr_pr = self.query_one("#prs-rr", PRList)
        except Exception as e:
            self.notify(f"sidebar widgets not found: {e}", severity="error")
            return
        await mine_pr.set_prs(mine, show_author=False)
        await rr_pr.set_prs(rr, show_author=True)
        if self._repos_loaded:
            self.run_worker(self._load_repos())

    def _update_project_badges(self, rr_prs: list[dict]) -> None:
        """Push per-project 'awaiting review' counts onto the picker."""
        if not self.config.projects:
            return
        counts: dict[str, int] = {p.name: 0 for p in self.config.projects}
        for pr in rr_prs:
            repo = pr.get("repository", {}).get("nameWithOwner")
            if not repo:
                continue
            for proj in self.config.projects:
                if proj.matches_repo(repo):
                    counts[proj.name] = counts.get(proj.name, 0) + 1
        try:
            pl = self.query_one("#project-list", ProjectList)
            pl.update_pr_review_badges(counts)
        except Exception:
            pass

    def _diff_and_notify_prs(self, mine: list[dict], rr: list[dict]) -> None:
        if not self.config.ui.notifications:
            return
        # New review-requested PRs
        rr_urls = {p.get("url", "") for p in rr if p.get("url")}
        new_rr = rr_urls - self._known_review_pr_urls
        # Review-decision changes on PRs I authored
        my_decisions = {p.get("url", ""): p.get("reviewDecision") or "" for p in mine if p.get("url")}
        decision_changes: list[dict] = []
        if self._notify_seeded:
            for url, decision in my_decisions.items():
                prev = self._known_my_pr_decisions.get(url)
                if prev is not None and prev != decision and decision in ("APPROVED", "CHANGES_REQUESTED"):
                    pr = next((p for p in mine if p.get("url") == url), None)
                    if pr:
                        decision_changes.append(pr)

        if self._notify_seeded:
            for pr in rr:
                if pr.get("url") in new_rr:
                    repo = pr.get("repository", {}).get("nameWithOwner", "?")
                    num = pr.get("number")
                    title = pr.get("title", "")
                    macos_notify(
                        title="ch · review requested",
                        message=f"{repo}#{num}: {title}",
                    )
            for pr in decision_changes:
                repo = pr.get("repository", {}).get("nameWithOwner", "?")
                num = pr.get("number")
                decision = pr.get("reviewDecision", "")
                emoji = "✓" if decision == "APPROVED" else "✗"
                macos_notify(
                    title=f"ch · PR {decision.lower().replace('_', ' ')}",
                    message=f"{emoji} {repo}#{num}",
                )
        self._known_review_pr_urls = rr_urls
        self._known_my_pr_decisions = my_decisions

    def _diff_and_notify_tickets(self, issues: list[dict]) -> None:
        if not self.config.ui.notifications:
            return
        keys = {i.get("key") for i in issues if i.get("key")}
        statuses = {
            i["key"]: ((i.get("fields") or {}).get("status") or {}).get("name", "")
            for i in issues
            if i.get("key")
        }
        new_keys = keys - self._known_sprint_keys
        status_changes: list[tuple[dict, str, str]] = []  # (issue, prev, new)
        if self._notify_seeded:
            for issue in issues:
                key = issue.get("key")
                if not key:
                    continue
                prev = self._known_ticket_status.get(key)
                cur = statuses.get(key, "")
                if prev is not None and prev != cur:
                    status_changes.append((issue, prev, cur))
            for issue in issues:
                if issue.get("key") in new_keys:
                    summary = (issue.get("fields") or {}).get("summary", "")
                    macos_notify(
                        title="ch · ticket assigned",
                        message=f"{issue['key']}: {summary}",
                    )
            for issue, prev, cur in status_changes:
                summary = (issue.get("fields") or {}).get("summary", "")
                macos_notify(
                    title=f"ch · {issue['key']} → {cur}",
                    message=f"{summary} (was {prev})",
                )
        self._known_sprint_keys = keys
        self._known_ticket_status = statuses
        # Mark seeded once we've completed at least one full refresh of both
        # PRs and sprint. Done at the end of the sprint diff so PR diff in the
        # same gather already ran.
        self._notify_seeded = True

    def _scope_prs(self, prs: list[dict]) -> list[dict]:
        """Filter PRs by the current project's repo list, if any."""
        if self.project_filter == "all":
            return prs
        if self.project_filter == "unassigned":
            project_repos = {r for p in self.config.projects for r in p.repos}
            return [p for p in prs if p.get("repository", {}).get("nameWithOwner") not in project_repos]
        if self.current_project:
            wanted = set(self.current_project.repos)
            return [p for p in prs if p.get("repository", {}).get("nameWithOwner") in wanted]
        return prs

    def _scope_repos(self, repos: list[dict]) -> list[dict]:
        if self.project_filter == "all":
            return repos
        if self.project_filter == "unassigned":
            project_repos = {r for p in self.config.projects for r in p.repos}
            return [r for r in repos if r.get("nameWithOwner") not in project_repos]
        if self.current_project:
            wanted = set(self.current_project.repos)
            return [r for r in repos if r.get("nameWithOwner") in wanted]
        return repos

    # ── focus + filter ────────────────────────────────────

    async def _apply_filter(self, query: str) -> None:
        for col in self.columns.values():
            await col.apply_filter(query)
        self._update_status()

    def action_focus_filter(self) -> None:
        if self.search_row and "shown" not in self.search_row.classes:
            self.search_row.add_class("shown")
        if self.search_input:
            self.search_input.focus()

    def action_exit_filter(self) -> None:
        if self.search_input and self.search_input.has_focus:
            self.search_input.value = ""
            self.run_worker(self._apply_filter(""))
            if self.search_row and "shown" in self.search_row.classes:
                self.search_row.remove_class("shown")
            # Refocus the first kanban column.
            cols = list(self.columns.values())
            if cols and cols[0].list_view:
                cols[0].list_view.focus()
            return
        # Narrow-mode drill-down: esc returns from detail to picker.
        if self.width_bucket == "narrow" and self.narrow_view == "detail":
            self.narrow_view = "picker"
            self._apply_width_bucket(self.size.width, force=True)
            try:
                pl = self.query_one("#project-list", ProjectList)
                pl.focus()
            except Exception:
                pass
            if hasattr(self, "view_row") and self.view_row is not None:
                self.view_row.update(self._view_label())

    @on(Input.Changed)
    def _filter_changed(self, ev: Input.Changed) -> None:
        if ev.input.id == "search-input":
            self.run_worker(self._apply_filter(ev.value))

    @on(Input.Submitted)
    def _filter_submitted(self, ev: Input.Submitted) -> None:
        if ev.input.id == "search-input":
            cols = list(self.columns.values())
            if cols and cols[0].list_view:
                cols[0].list_view.focus()

    @on(ListView.Highlighted)
    def _list_highlighted(self, ev: ListView.Highlighted) -> None:
        # Re-evaluate footer bindings whenever the highlighted item changes.
        self.refresh_bindings()

    @on(ListView.Selected)
    def _list_selected(self, ev: ListView.Selected) -> None:
        item = ev.item
        if isinstance(item, TicketCard):
            self.run_worker(self._open_card_detail(item))
        elif isinstance(item, PRItem):
            self._open_pr_modal(item.pr)
        elif isinstance(item, RepoItem):
            self._open_repo_modal(item)
        elif isinstance(item, ProjectItem):
            self._select_project(item)

    def _select_project(self, item: ProjectItem) -> None:
        if item.special == "all":
            self.current_project = None
            self.project_filter = "all"
            self.current_project_name = "All"
        elif item.special == "unassigned":
            self.current_project = None
            self.project_filter = "unassigned"
            self.current_project_name = "Unassigned"
        else:
            self.current_project = item.project
            self.project_filter = item.project.name if item.project else "all"
            self.current_project_name = item.project.name if item.project else "All"
        # In medium/narrow mode, picking a project is overwhelmingly a "show
        # me the tickets" action — auto-flip to the Kanban detail.
        if self.width_bucket in ("medium", "narrow") and self.active_detail != "kanban":
            self.active_detail = "kanban"
        # In narrow mode, picking a project drills into the detail view.
        if self.width_bucket == "narrow":
            self.narrow_view = "detail"
            self._apply_width_bucket(self.size.width, force=True)
            self._focus_first_in_active_detail()
        else:
            self._apply_width_bucket(self.size.width, force=True)
        if hasattr(self, "view_row") and self.view_row is not None:
            self.view_row.update(self._view_label())
        self._refresh()

    async def _open_card_detail(self, card: TicketCard) -> None:
        # Modal fetches its own data on mount so it can refresh after edits.
        self.push_screen(TicketDetailModal(card.key_label, self.config))

    def _open_pr_modal(self, pr: dict[str, Any]) -> None:
        url = pr.get("url")
        if not url:
            return
        repo = pr.get("repository", {}).get("nameWithOwner", "?")
        self.push_screen(PRDetailModal(repo=repo, number=pr["number"], url=url, config=self.config))

    def _open_repo_modal(self, item: RepoItem) -> None:
        name = item.repo.get("nameWithOwner")
        if not name:
            return
        self.push_screen(RepoDetailModal(name_with_owner=name, local_clone=item.local_clone, config=self.config))

    def _open_pr_browser(self, pr: dict[str, Any]) -> None:
        url = pr.get("url")
        if not url:
            return
        import webbrowser
        webbrowser.open(url)
        repo = pr.get("repository", {}).get("nameWithOwner", "?")
        self.notify(f"opened {repo}#{pr.get('number')}", severity="information")

    # ── focus helpers ──────────────────────────────────────

    def _focused_card(self) -> TicketCard | None:
        focused = self.focused
        if isinstance(focused, ListView):
            highlighted = focused.highlighted_child
            if isinstance(highlighted, TicketCard):
                return highlighted
        return None

    def _focused_kind(self) -> str:
        """Return 'ticket' | 'pr' | 'repo' | 'none' based on what's highlighted."""
        focused = self.focused
        if isinstance(focused, ListView):
            item = focused.highlighted_child
            if isinstance(item, TicketCard):
                return "ticket"
            if isinstance(item, PRItem):
                return "pr"
            if isinstance(item, RepoItem):
                return "repo"
        return "none"

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Filter footer entries by what's currently focused."""
        kind = self._focused_kind()
        # Ticket-only actions
        if action in ("transition", "assign", "comment"):
            return kind == "ticket"
        # AI works on any of the three; keep visible always
        if action == "ai_on_card":
            return kind in ("ticket", "pr", "repo")
        # Repo-only actions (e/s overlap with detail-modal bindings, but those
        # have their own bindings list so won't collide)
        if action in ("open_repo_editor", "open_repo_shell"):
            return kind == "repo"
        # open_browser works on all three
        if action == "open_browser":
            return kind != "none"
        # Everything else (refresh, help, palette, view switchers, quit) always shown
        return True

    def _kanban_columns(self) -> list[KanbanColumn]:
        return list(self.columns.values())

    def _focus_panels(self) -> list[ListView]:
        """Ordered left-to-right: ProjectList | kanban cols | active sidebar tab.
        Skips detail panels collapsed by the responsive layout."""
        panels: list[ListView] = []
        try:
            pl = self.query_one("#project-list", ProjectList)
            panels.append(pl)
        except Exception:
            pass
        try:
            kanban_hidden = self.query_one("#kanban-wrap").has_class("-detail-hidden")
        except Exception:
            kanban_hidden = False
        if not kanban_hidden:
            for col in self._kanban_columns():
                if col.list_view is not None:
                    panels.append(col.list_view)
        try:
            sidebar_hidden = self.query_one("#sidebar").has_class("-detail-hidden")
        except Exception:
            sidebar_hidden = False
        if not sidebar_hidden:
            try:
                tabs = self.query_one(TabbedContent)
                active_pane = tabs.active_pane
                if active_pane is not None:
                    for lv in active_pane.query(ListView):
                        panels.append(lv)
                        break
            except Exception:
                pass
        return panels

    def _current_panel_index(self, panels: list[ListView]) -> int | None:
        """Find the index of the panel that contains/equals the currently focused widget."""
        focused = self.focused
        if focused is None:
            return None
        for i, panel in enumerate(panels):
            if panel is focused:
                return i
            # Focused widget is a descendant of the panel? Walk up the parent chain.
            parent = focused.parent
            while parent is not None:
                if parent is panel:
                    return i
                parent = parent.parent
        return None

    def _scroll_focused_column_into_view(self, target: ListView) -> None:
        """If the newly-focused panel is a kanban column, snap that column's
        left edge to the container's left edge by computing the target
        scroll_x from the column's *index* + the widths of preceding columns.
        Earlier attempts using node.region.x produced tiny deltas because
        region.x reports the on-screen position (already small for a fully-
        visible column) rather than the virtual position in scroll content."""
        cols = self._kanban_columns()
        target_idx = -1
        for i, col in enumerate(cols):
            if col.list_view is target:
                target_idx = i
                break
        if target_idx < 0:
            return
        try:
            container = self.query_one("#kanban", KanbanScroll)
            # Sum the outer widths (content + margin) of all columns before
            # the target. That's exactly the virtual x at which the target
            # column's left edge sits.
            target_x = sum(c.outer_size.width for c in cols[:target_idx])
            container.scroll_to(x=target_x, animate=False)
        except Exception:
            pass

    def action_focus_next_col(self) -> None:
        panels = self._focus_panels()
        if not panels:
            return
        idx = self._current_panel_index(panels)
        target = panels[((idx if idx is not None else -1) + 1) % len(panels)]
        target.focus()
        self._scroll_focused_column_into_view(target)
        self.refresh_bindings()

    def action_focus_prev_col(self) -> None:
        panels = self._focus_panels()
        if not panels:
            return
        idx = self._current_panel_index(panels)
        target = panels[((idx if idx is not None else 0) - 1) % len(panels)]
        target.focus()
        self._scroll_focused_column_into_view(target)
        self.refresh_bindings()

    # ── actions: transition, assign, comment, open ────────

    @work(exclusive=True)
    async def action_transition(self) -> None:
        card = self._focused_card()
        if not card:
            self.notify("focus a ticket first", severity="warning")
            return
        try:
            async with JiraClient(self.config) as api:
                transitions = await api.get_transitions(card.key_label)
        except ApiError as e:
            self.notify(f"error: {e}", severity="error")
            return
        if not transitions:
            self.notify("no transitions available", severity="warning")
            return

        def _on_pick(transition_id: str | None) -> None:
            if transition_id:
                self.run_worker(self._do_transition(card.key_label, transition_id))

        self.push_screen(TransitionModal(transitions), _on_pick)

    async def _do_transition(self, key: str, transition_id: str) -> None:
        try:
            async with JiraClient(self.config) as api:
                await api.transition_issue(key, transition_id)
        except ApiError as e:
            self.notify(f"transition failed: {e}", severity="error")
            return
        self.notify(f"✓ {key} transitioned", severity="information")
        self._refresh()

    @work(exclusive=True)
    async def action_assign(self) -> None:
        card = self._focused_card()
        if not card:
            self.notify("focus a ticket first", severity="warning")
            return

        def _on_pick(target: str | None) -> None:
            if target:
                self.run_worker(self._do_assign(card.key_label, target))

        self.push_screen(AssignModal(), _on_pick)

    async def _do_assign(self, key: str, target: str) -> None:
        try:
            async with JiraClient(self.config) as api:
                if target.lower() in ("@me", "me"):
                    me = await api.myself()
                    await api.edit_issue(key, {"assignee": {"accountId": me["accountId"]}})
                    name = me.get("displayName", "you")
                elif target.lower() in ("none", "unassign", "-"):
                    await api.edit_issue(key, {"assignee": None})
                    name = "—"
                else:
                    results = await api.find_user(target.lstrip("@"))
                    if not results:
                        self.notify(f"no user matches '{target}'", severity="warning")
                        return
                    if len(results) > 1:
                        self.notify(f"ambiguous '{target}' — be more specific", severity="warning")
                        return
                    await api.edit_issue(key, {"assignee": {"accountId": results[0]["accountId"]}})
                    name = results[0].get("displayName", target)
        except ApiError as e:
            self.notify(f"assign failed: {e}", severity="error")
            return
        self.notify(f"✓ {key} assigned to {name}", severity="information")
        self._refresh()

    def action_comment(self) -> None:
        card = self._focused_card()
        if not card:
            self.notify("focus a ticket first", severity="warning")
            return

        def _on_submit(text: str | None) -> None:
            if text:
                self.run_worker(self._do_comment(card.key_label, text))

        self.push_screen(CommentModal(card.key_label), _on_submit)

    async def _do_comment(self, key: str, text: str) -> None:
        try:
            async with JiraClient(self.config) as api:
                await api.add_comment(key, text_to_adf(text))
        except ApiError as e:
            self.notify(f"comment failed: {e}", severity="error")
            return
        self.notify(f"✓ commented on {key}", severity="information")

    def action_open_detail(self) -> None:
        card = self._focused_card()
        if not card:
            self.notify("focus a ticket first", severity="warning")
            return
        self.run_worker(self._open_card_detail(card))

    def action_open_browser(self) -> None:
        # Works on whichever list view is focused — kanban / PR / Repo.
        focused = self.focused
        if isinstance(focused, ListView):
            item = focused.highlighted_child
            if isinstance(item, PRItem):
                self._open_pr_browser(item.pr)
                return
            if isinstance(item, RepoItem):
                url = item.repo.get("url")
                if url:
                    import webbrowser
                    webbrowser.open(url)
                    self.notify(f"opened {item.repo.get('nameWithOwner')}", severity="information")
                return
            if isinstance(item, TicketCard):
                if not self.config.default_cloud_url:
                    self.notify("no default_cloud_url configured", severity="warning")
                    return
                import webbrowser
                base = self.config.default_cloud_url.rstrip("/")
                webbrowser.open(f"{base}/browse/{item.key_label}")
                self.notify(f"opened {item.key_label}", severity="information")
                return
        self.notify("focus a ticket / PR / repo first", severity="warning")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_refresh(self) -> None:
        self._refresh()

    # ── view modes ─────────────────────────────────────────

    def _switch_view(self, mode: str) -> None:
        if mode == self.view_mode:
            return
        self.view_mode = mode
        if hasattr(self, "view_row") and self.view_row is not None:
            self.view_row.update(self._view_label())
        # Keep the kanban Tabs widget in sync — setting .active programmatically
        # fires TabActivated, which our handler short-circuits if it matches.
        if hasattr(self, "kanban_tabs") and self.kanban_tabs is not None:
            try:
                if self.kanban_tabs.active != mode:
                    self.kanban_tabs.active = mode
            except Exception:
                pass
        self._refresh()

    def action_view_sprint(self) -> None: self._switch_view("sprint")
    def action_view_backlog(self) -> None: self._switch_view("backlog")
    def action_view_all(self) -> None: self._switch_view("all")
    def action_view_recent(self) -> None: self._switch_view("recent")

    def _cycle_sidebar(self, delta: int) -> None:
        try:
            tabs = self.query_one(TabbedContent)
        except Exception:
            return
        # tabs.tab_count attribute differs across versions; fall back to counting panes.
        try:
            ids = [p.id for p in tabs.query("TabPane")]
            if not ids:
                return
            current = tabs.active or ids[0]
            try:
                idx = ids.index(current)
            except ValueError:
                idx = 0
            new_active = ids[(idx + delta) % len(ids)]
            tabs.active = new_active
            # Lazy-load repos if user just landed on the Repos tab.
            self._maybe_load_repos()
        except Exception:
            pass

    @on(TabbedContent.TabActivated)
    def _tab_activated(self, ev: TabbedContent.TabActivated) -> None:
        self._maybe_load_repos()
        self.refresh_bindings()

    @on(Tabs.TabActivated, "#kanban-tabs")
    def _kanban_tab_activated(self, ev: Tabs.TabActivated) -> None:
        # Mouse-click on a kanban tab. _switch_view short-circuits if the
        # mode is already current (so this doesn't recurse when 1/2/3/4 set
        # tabs.active programmatically).
        new_mode = ev.tab.id
        if new_mode:
            self._switch_view(new_mode)

    def _maybe_load_repos(self) -> None:
        if self._repos_loaded:
            return
        try:
            tabs = self.query_one(TabbedContent)
            active = tabs.active_pane
            if active is None:
                return
            # Find a RepoList inside the active pane to confirm we're on Repos tab.
            if not list(active.query(RepoList)):
                return
        except Exception:
            return
        self._repos_loaded = True
        self.run_worker(self._load_repos())

    async def _load_repos(self) -> None:
        try:
            repos = await asyncio.to_thread(my_repos, 100)
        except GhError as e:
            self.notify(f"gh: {e}", severity="warning", timeout=6)
            self._repos_loaded = False
            return
        repos = self._scope_repos(repos)
        try:
            repo_list = self.query_one("#repos", RepoList)
        except Exception:
            return
        await repo_list.set_repos(repos, self.config.ui.repo_root, config=self.config)

    def _focused_detail(self) -> str:
        """Return 'kanban', 'code', or 'other' based on which panel owns focus.
        Used to make [ ] cycle the tabs of whichever detail is focused."""
        focused = self.focused
        if focused is None:
            return "other"
        node = focused
        while node is not None:
            nid = getattr(node, "id", None)
            if nid == "kanban-wrap":
                return "kanban"
            if nid == "sidebar":
                return "code"
            node = node.parent
        return "other"

    def _cycle_kanban_view(self, delta: int) -> None:
        modes = ["sprint", "backlog", "all", "recent"]
        try:
            idx = modes.index(self.view_mode)
        except ValueError:
            idx = 0
        self._switch_view(modes[(idx + delta) % len(modes)])

    def action_next_sidebar_tab(self) -> None:
        # Focus-aware: cycle whichever detail's tabs are visible.
        if self._focused_detail() == "kanban":
            self._cycle_kanban_view(1)
        else:
            self._cycle_sidebar(1)
            panels = self._focus_panels()
            if panels:
                panels[-1].focus()
        self.refresh_bindings()

    def action_prev_sidebar_tab(self) -> None:
        if self._focused_detail() == "kanban":
            self._cycle_kanban_view(-1)
        else:
            self._cycle_sidebar(-1)
            panels = self._focus_panels()
            if panels:
                panels[-1].focus()
        self.refresh_bindings()

    # ── AI bridge ─────────────────────────────────────────

    @work(exclusive=False)
    async def action_brainstorm(self) -> None:
        target_project = self.current_project or (self.config.projects[0] if self.config.projects else None)
        target_name = target_project.name if target_project else "(no project)"
        self.notify(f"building brainstorm context for {target_name}…", severity="information", timeout=3)
        try:
            prompt = await build_brainstorm_prompt(self.config, target_project)
        except Exception as e:
            self.notify(f"brainstorm build failed: {e}", severity="error")
            return
        full = f"{self.config.ai.claude_path} {quote_for_shell(prompt)}"
        title = f"brainstorm·{target_project.name}" if target_project else "brainstorm"
        try:
            spawn(full, title=title, config=self.config.tmux)
        except RuntimeError as e:
            self.notify(str(e), severity="error")
            return
        self.notify(f"opened {title}", severity="information")

    def _ai_dir_candidates(self) -> list[tuple[str, str]]:
        """Build (label, abs_path) candidates for where to cd before launching
        claude on a ticket. Scopes to current_project if one is selected,
        otherwise covers all configured projects. Dedupes by absolute path."""
        from pathlib import Path
        seen: set[str] = set()
        out: list[tuple[str, str]] = []

        def _add(label: str, raw_path: str | None) -> None:
            if not raw_path:
                return
            p = Path(raw_path).expanduser()
            if not p.is_dir():
                return
            abs_path = str(p)
            if abs_path in seen:
                return
            seen.add(abs_path)
            out.append((label, abs_path))

        projects = [self.current_project] if self.current_project else list(self.config.projects)
        for proj in projects:
            if proj is None:
                continue
            if proj.local_path:
                _add(f"{proj.name} (project root)", proj.local_path)
            root = Path(self.config.ui.repo_root).expanduser() if self.config.ui.repo_root else None
            for repo in proj.repos:
                resolved = proj.resolve_repo_path(repo)
                if resolved:
                    _add(repo, resolved)
                elif root is not None:
                    short = repo.split("/")[-1] if "/" in repo else repo
                    _add(repo, str(root / short))
        return out

    def _resolve_repo_local_dir(self, name_with_owner: str) -> str | None:
        """Find the local clone dir for a repo by name. Tries explicit per-
        project overrides first (Config.resolve_repo_path scans projects'
        repo_paths + single-repo local_path), then falls back to the
        ui.repo_root / <short-name> heuristic. Returns None if nothing
        resolves to an existing directory."""
        from pathlib import Path
        explicit = self.config.resolve_repo_path(name_with_owner)
        if explicit:
            p = Path(explicit).expanduser()
            if p.is_dir():
                return str(p)
        if self.config.ui.repo_root and "/" in name_with_owner:
            short = name_with_owner.split("/")[-1]
            candidate = Path(self.config.ui.repo_root).expanduser() / short
            if candidate.is_dir():
                return str(candidate)
        return None

    def _spawn_claude_for_ticket(self, key: str, cwd: str | None) -> None:
        cmd = self.config.ai.default_command
        full = f"{self.config.ai.claude_path} {quote_for_shell(f'{cmd} {key}')}"
        try:
            if cwd:
                spawn_in_dir(full, cwd=cwd, title=key, config=self.config.tmux)
            else:
                spawn(full, title=key, config=self.config.tmux)
        except RuntimeError as e:
            self.notify(str(e), severity="error")
            return
        where = f" in {cwd}" if cwd else ""
        self.notify(f"opened claude pane for {key}{where}", severity="information")

    def _spawn_editor_for_ticket(self, key: str, cwd: str | None) -> None:
        if not cwd:
            self.notify(f"no local dir resolved for {key}", severity="warning")
            return
        editor = self.config.ui.editor_command
        try:
            spawn_in_dir(f"{editor} .", cwd=cwd, title=f"{editor}·{key}", config=self.config.tmux)
        except RuntimeError as e:
            self.notify(str(e), severity="error")
            return
        self.notify(f"opened {editor} for {key} in {cwd}", severity="information")

    def _resolve_ticket_dir(
        self,
        key: str,
        force_pick: bool,
        on_resolved,
    ) -> None:
        """Resolve the local dir for `key` (cache → single candidate → picker)
        and call `on_resolved(dir_or_none)`. `dir_or_none` is None when no
        candidates exist; the caller then decides how to spawn (e.g. claude
        falls back to no-cwd, editor warns)."""
        if not force_pick and key in self._ticket_dir_cache:
            on_resolved(self._ticket_dir_cache[key])
            return

        candidates = self._ai_dir_candidates()
        if not candidates:
            on_resolved(None)
            return
        if len(candidates) == 1:
            chosen = candidates[0][1]
            self._ticket_dir_cache[key] = chosen
            on_resolved(chosen)
            return

        def _on_pick(path: str | None) -> None:
            if path is None:
                return  # user cancelled
            self._ticket_dir_cache[key] = path
            on_resolved(path)

        self.push_screen(AIDirPickerModal(key, candidates), _on_pick)

    def _launch_ai_for_key(self, key: str, force_pick: bool = False) -> None:
        """Pick a local repo dir (via AIDirPickerModal if 2+ candidates) and
        spawn claude there with the configured default_command + key. Reads/
        writes self._ticket_dir_cache so the choice persists for the session
        and is shared with the `E` (editor) launcher."""
        self._resolve_ticket_dir(
            key,
            force_pick,
            lambda cwd: self._spawn_claude_for_ticket(key, cwd=cwd),
        )

    def _launch_editor_for_key(self, key: str, force_pick: bool = False) -> None:
        """Mirror of _launch_ai_for_key for the editor. Shares the same dir
        cache so pressing E then A on the same ticket lands both in the same
        cwd (required for claudecode.nvim auto-discovery to wire up)."""
        self._resolve_ticket_dir(
            key,
            force_pick,
            lambda cwd: self._spawn_editor_for_ticket(key, cwd=cwd),
        )

    def action_ai_on_card(self, force_pick: bool = False) -> None:
        focused = self.focused
        if isinstance(focused, ListView):
            item = focused.highlighted_child
            if isinstance(item, TicketCard):
                self._launch_ai_for_key(item.key_label, force_pick=force_pick)
                return
            if isinstance(item, PRItem):
                url = item.pr.get("url", "")
                repo = item.pr.get("repository", {}).get("nameWithOwner", "?")
                num = item.pr.get("number")
                full = f"{self.config.ai.claude_path} {quote_for_shell(f'/review {url}')}"
                # Spawn in the PR's repo dir when we can resolve it — keeps
                # claude and a parallel editor pane (E) in the same cwd so
                # claudecode.nvim auto-discovery wires up.
                cwd = self._resolve_repo_local_dir(repo)
                try:
                    if cwd:
                        spawn_in_dir(full, cwd=cwd, title=f"review·{repo}#{num}", config=self.config.tmux)
                    else:
                        spawn(full, title=f"review·{repo}#{num}", config=self.config.tmux)
                except RuntimeError as e:
                    self.notify(str(e), severity="error")
                    return
                where = f" in {cwd}" if cwd else ""
                self.notify(f"opened claude review pane for {repo}#{num}{where}", severity="information")
                return
            if isinstance(item, RepoItem):
                if not item.local_clone:
                    self.notify(f"no local clone of {item.repo.get('nameWithOwner')}", severity="warning")
                    return
                claude = self.config.ai.claude_path
                try:
                    spawn_in_dir(claude, cwd=item.local_clone, title=f"claude·{item.repo.get('nameWithOwner')}", config=self.config.tmux)
                except RuntimeError as e:
                    self.notify(str(e), severity="error")
                    return
                self.notify(f"opened claude in {item.local_clone}", severity="information")
                return
        self.notify("focus a ticket / PR / repo first", severity="warning")

    def action_ai_on_card_force(self) -> None:
        """ctrl+a: re-open the dir picker even if a cached choice exists."""
        self.action_ai_on_card(force_pick=True)

    def action_editor_on_card(self, force_pick: bool = False) -> None:
        """E: open the editor in the same dir picker as `A`. On a focused
        ticket we share the dir cache with claude so claudecode.nvim
        auto-discovery wires up. PRs and repos fall back to the existing
        per-item editor flow."""
        focused = self.focused
        if isinstance(focused, ListView):
            item = focused.highlighted_child
            if isinstance(item, TicketCard):
                self._launch_editor_for_key(item.key_label, force_pick=force_pick)
                return
            if isinstance(item, RepoItem):
                self.action_open_repo_editor()
                return
            if isinstance(item, PRItem):
                repo = item.pr.get("repository", {}).get("nameWithOwner", "")
                num = item.pr.get("number")
                cwd = self._resolve_repo_local_dir(repo)
                if not cwd:
                    self.notify(
                        f"no local clone of {repo} (set repo_paths or clone under {self.config.ui.repo_root})",
                        severity="warning",
                    )
                    return
                editor = self.config.ui.editor_command
                try:
                    spawn_in_dir(
                        f"{editor} .",
                        cwd=cwd,
                        title=f"{editor}·{repo}#{num}",
                        config=self.config.tmux,
                    )
                except RuntimeError as e:
                    self.notify(str(e), severity="error")
                    return
                self.notify(f"opened {editor} for {repo}#{num} in {cwd}", severity="information")
                return
        self.notify("focus a ticket / PR / repo first", severity="warning")

    def action_editor_on_card_force(self) -> None:
        """ctrl+e: re-open the dir picker for the editor."""
        self.action_editor_on_card(force_pick=True)

    # ── repo actions (only when focus is on the Repos list) ─

    def _focused_repo(self) -> RepoItem | None:
        focused = self.focused
        if isinstance(focused, ListView) and focused.id == "repos":
            item = focused.highlighted_child
            if isinstance(item, RepoItem):
                return item
        return None

    def action_open_repo_editor(self) -> None:
        item = self._focused_repo()
        if not item:
            return  # silent: 'e' is also used by detail modal
        if not item.local_clone:
            self.notify(
                f"no local clone of {item.repo.get('nameWithOwner')} under {self.config.ui.repo_root}",
                severity="warning",
                timeout=5,
            )
            return
        editor = self.config.ui.editor_command
        # Pass '.' so the editor opens the directory (vs. its empty dashboard).
        try:
            spawn_in_dir(
                f"{editor} .",
                cwd=item.local_clone,
                title=item.repo.get("nameWithOwner", "repo"),
                config=self.config.tmux,
            )
        except RuntimeError as e:
            self.notify(str(e), severity="error")
            return
        self.notify(f"opened {editor} in {item.local_clone}", severity="information")

    def action_open_repo_shell(self) -> None:
        item = self._focused_repo()
        if not item:
            return
        if not item.local_clone:
            self.notify(
                f"no local clone of {item.repo.get('nameWithOwner')} under {self.config.ui.repo_root}",
                severity="warning",
                timeout=5,
            )
            return
        shell = os.environ.get("SHELL", "/bin/sh")
        try:
            spawn_in_dir(
                shell,
                cwd=item.local_clone,
                title=item.repo.get("nameWithOwner", "shell"),
                config=self.config.tmux,
            )
        except RuntimeError as e:
            self.notify(str(e), severity="error")
            return
        self.notify(f"opened shell in {item.local_clone}", severity="information")

    THEMES = [  # noqa: RUF012
        "jg-pink",
        "jg-night",
        "jg-paper",
        "textual-dark",
        "textual-light",
        "nord",
        "gruvbox",
        "tokyo-night",
        "dracula",
        "monokai",
        "catppuccin-mocha",
        "flexoki",
    ]

    def action_cycle_theme(self) -> None:
        try:
            current = self.theme  # type: ignore[attr-defined]
        except Exception:
            current = "jg-pink"
        try:
            idx = self.THEMES.index(current)
        except ValueError:
            idx = -1
        next_theme = self.THEMES[(idx + 1) % len(self.THEMES)]
        try:
            self.theme = next_theme  # type: ignore[attr-defined]
        except Exception as e:
            self.notify(f"theme switch failed: {e}", severity="error")
            return
        # Persist the choice to config.
        self.config.ui.theme = next_theme
        try:
            self.config.save()
        except Exception:
            pass
        self.notify(f"theme: {next_theme} (saved)", severity="information", timeout=3)


def run_dashboard(config: Config) -> None:
    ChDashboard(config).run()
