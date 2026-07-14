"""The altitude-workspace shell (WIP, built alongside `jg dashboard`).

New organizing model: one navigable ladder — Portfolio → Initiative → Task —
instead of three fixed panels. `enter`/`l` descends, `esc`/`h` ascends, a
breadcrumb always shows where you are. Reuses the data modules and rendering
helpers; only the shell is new.

Slice 0 (this file) is the walking skeleton: read-only navigation across the
three altitudes. Lenses, inbox, actions, and gate wiring land in later slices.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, ListItem, ListView, Static

from jg import gates, github, progress, projectdocs, roadmap
from jg.adf import render_to_text, text_to_adf
from jg.api import ApiError, JiraClient
from jg.auth import AuthError
from jg.config import Config
from jg.render import GROUP_ORDER, GROUP_STYLE, normalize_status
from jg.themes import ALL_THEMES
from jg.tmux import quote_for_shell, spawn, spawn_in_dir
from jg.tui import (  # reuse existing panel + action/gate modals
    AssignModal,
    CommentModal,
    GateModal,
    GradientPanel,
    ScopeGateModal,
    TransitionModal,
)

# Lenses cut across the current altitude. Portfolio lenses span all initiatives
# (Sprint = my open-sprint tasks everywhere); initiative lenses scope to the epic.
LENSES: dict[str, list[str]] = {
    "inbox": [],
    "portfolio": ["Roadmap", "Sprint"],
    "initiative": ["Board", "Mine"],
    "task": [],
}


class _EpicRow(ListItem):
    def __init__(self, epic: roadmap.Epic):
        self.epic = epic
        t = Text()
        t.append(f"{epic.key:>8}  ", style="bold #c0caf5")
        style = "green" if (epic.is_done_status or epic.pct == 100) else ("cyan" if epic.pct > 0 else "dim")
        t.append(roadmap.progress_bar(epic.pct) + " ", style=style)
        t.append(f"{epic.pct:>3}% ", style="bold")
        t.append(f"{epic.status}  ", style=GROUP_STYLE.get(epic.status_group, "white"))
        t.append(epic.summary[:44])
        super().__init__(Static(t))


class _TaskRow(ListItem):
    def __init__(self, key: str, summary: str, status: str):
        self.task_key = key
        self.task_summary = summary
        t = Text()
        t.append(f"{key:>8}  ", style="bold #c0caf5")
        grp = normalize_status(status)
        t.append(f"[{status}]".ljust(16), style=GROUP_STYLE.get(grp, "white"))
        t.append("  ")
        t.append(summary[:50])
        super().__init__(Static(t))


class _ReviewRow(ListItem):
    """An external PR awaiting my review — inbound work with no home in my tree."""

    def __init__(self, pr: dict):
        self.pr = pr
        repo = (pr.get("repository") or {}).get("nameWithOwner", "?")
        num = pr.get("number", "?")
        t = Text()
        t.append("⇄ ", style="magenta")
        t.append(f"{repo}#{num}  ", style="bold #c0caf5")
        t.append((pr.get("title") or "")[:56], style="white")
        super().__init__(Static(t))


class _HeaderRow(ListItem):
    """A non-actionable section header inside a list (skipped on descend)."""

    def __init__(self, label: str):
        super().__init__(Static(Text(label, style="bold #f5c2e7")))
        self.disabled = True


class WorkspaceApp(App):
    CSS = """
    #crumb { height: 1; padding: 0 1; color: $accent; text-style: bold; }
    #lens { height: 1; padding: 0 1; }
    WorkspaceApp GradientPanel { height: 1fr; }
    WorkspaceApp ListView { background: transparent; }
    WorkspaceApp ListView > ListItem { background: transparent; padding: 0; }
    WorkspaceApp .detail { padding: 0 1; }
    """

    BINDINGS = [  # noqa: RUF012
        Binding("q", "quit", "quit", show=True),
        Binding("r", "refresh", "refresh", show=True),
        Binding("enter", "descend", "open", show=True),
        Binding("l", "descend", "open", show=False),
        Binding("right", "descend", "open", show=False),
        Binding("escape", "ascend", "back", show=True),
        Binding("h", "ascend", "back", show=False),
        Binding("left", "ascend", "back", show=False),
        Binding("right_square_bracket", "lens_next", "lens →", show=True),
        Binding("left_square_bracket", "lens_prev", "lens ←", show=False),
        Binding("i", "inbox", "inbox", show=True),
        Binding("p", "portfolio", "portfolio", show=True),
        Binding("t", "transition", "transition", show=True),
        Binding("a", "assign", "assign", show=False),
        Binding("c", "comment", "comment", show=False),
        Binding("A", "claude", "claude", show=True),
        Binding("d", "decompose", "decompose", show=True),
        Binding("o", "open_browser", "browser", show=True),
    ]

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.altitude = "portfolio"  # portfolio | initiative | task
        self.current_epic: roadmap.Epic | None = None
        self.current_task: tuple[str, str] | None = None  # (key, summary)
        self.lens = {"portfolio": "Roadmap", "initiative": "Board"}  # active lens per altitude
        self._task_from = "initiative"  # altitude to return to when ascending from a task
        self._body: VerticalScroll | None = None

    def compose(self) -> ComposeResult:
        self._crumb = Static("", id="crumb")
        self._lens_strip = Static("", id="lens")
        self._panel = GradientPanel(panel_title="workspace")
        yield self._crumb
        yield self._lens_strip
        yield self._panel
        yield Footer()

    def on_mount(self) -> None:
        self.title = "jg workspace"
        for t in ALL_THEMES:
            try:
                self.register_theme(t)
            except Exception:
                pass
        try:
            self.theme = self.config.ui.theme  # type: ignore[attr-defined]
        except Exception:
            pass
        self._body = VerticalScroll()
        self._panel.mount_content(self._body)
        self.load_inbox()  # cold-start at the front door

    # ── breadcrumb ───────────────────────────────────────────────────────────
    def _set_crumb(self) -> None:
        if self.altitude == "inbox":
            self._crumb.update("Inbox")
            return
        # Root is Inbox for tasks opened from the inbox, else Portfolio (the tree).
        if self.altitude == "task" and self._task_from == "inbox":
            parts = ["Inbox"]
        else:
            parts = ["Portfolio"]
            if self.altitude in ("initiative", "task") and self.current_epic:
                parts.append(f"{self.current_epic.key} {self.current_epic.summary[:24]}")
        if self.altitude == "task" and self.current_task:
            parts.append(f"{self.current_task[0]} {self.current_task[1][:24]}")
        self._crumb.update(" › ".join(parts))  # noqa: RUF001 (breadcrumb separator)

    def _set_lens_strip(self) -> None:
        lenses = LENSES.get(self.altitude, [])
        active = self.lens.get(self.altitude)
        strip = Text()
        for i, name in enumerate(lenses):
            if i:
                strip.append("    ")
            strip.append(name, style="bold #c0caf5" if name == active else "dim")
        self._lens_strip.update(strip)

    def _task_rows(self, issues: list[dict]) -> list[_TaskRow]:
        rank = {g: i for i, g in enumerate(GROUP_ORDER)}
        rows = [
            (i["key"], i.get("fields", {}).get("summary", ""), (i.get("fields", {}).get("status") or {}).get("name", "—"))
            for i in issues
        ]
        rows.sort(key=lambda r: rank.get(normalize_status(r[2]), len(GROUP_ORDER)))
        return [_TaskRow(k, s, st) for k, s, st in rows]

    async def _search_rows(self, jql: str) -> list[_TaskRow] | str:
        """Run a task search, returning rows or an error string."""
        try:
            async with JiraClient(self.config) as api:
                data = await api.search_jql(jql, fields=["summary", "status"], max_results=100)
        except Exception as e:
            return self._notify_err(e)
        return self._task_rows(data.get("issues", []))

    async def _swap(self, *widgets: Any, focus_list: bool = True) -> None:
        assert self._body is not None
        await self._body.remove_children()
        await self._body.mount(*widgets)
        if focus_list:
            for w in widgets:
                if isinstance(w, ListView):
                    w.focus()
                    break

    def _notify_err(self, e: Exception) -> str:
        if isinstance(e, AuthError):
            return "session expired — run jg auth login" if getattr(e, "needs_relogin", False) else "auth error"
        if isinstance(e, ApiError):
            return f"load failed: {e}"
        return f"load failed: {type(e).__name__}"

    async def _show_rows(self, rows: list[_TaskRow] | str, empty_msg: str) -> None:
        if isinstance(rows, str):  # error
            await self._swap(Static(f"[red]{rows}[/]", classes="detail"), focus_list=False)
        elif not rows:
            await self._swap(Static(f"[dim]{empty_msg}[/]", classes="detail"), focus_list=False)
        else:
            await self._swap(ListView(*rows))

    # ── home: inbox (front door — inbound work with no home in the tree) ───────
    @work(exclusive=True)
    async def load_inbox(self) -> None:
        self.altitude = "inbox"
        self._set_crumb()
        self._set_lens_strip()
        try:  # GitHub call is sync (subprocess) — offload so the UI stays responsive
            prs = await asyncio.to_thread(github.review_requested_prs)
        except Exception:
            prs = []
        assigned = await self._search_rows(
            "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"
        )
        assigned_rows = assigned if isinstance(assigned, list) else []
        items: list[ListItem] = [_HeaderRow(f"Review requests ({len(prs)})")]
        items += [_ReviewRow(pr) for pr in prs]
        items.append(_HeaderRow(f"Assigned to me ({len(assigned_rows)})"))
        items += assigned_rows
        await self._swap(ListView(*items))

    # ── altitude 0: portfolio ─────────────────────────────────────────────────
    @work(exclusive=True)
    async def load_portfolio(self) -> None:
        self.altitude = "portfolio"
        self._set_crumb()
        self._set_lens_strip()
        if self.lens["portfolio"] == "Sprint":  # my open-sprint tasks across all initiatives
            jql = "assignee = currentUser() AND sprint in openSprints() ORDER BY status ASC, updated DESC"
            await self._show_rows(await self._search_rows(jql), "no sprint tasks assigned to you")
            return
        try:  # Roadmap
            epics = await roadmap.fetch_roadmap(self.config)
        except Exception as e:
            await self._swap(Static(f"[red]{self._notify_err(e)}[/]", classes="detail"), focus_list=False)
            return
        if not epics:
            await self._swap(Static("[dim]no epics[/]", classes="detail"), focus_list=False)
            return
        await self._swap(ListView(*[_EpicRow(e) for e in epics]))

    # ── altitude 1: initiative ────────────────────────────────────────────────
    @work(exclusive=True)
    async def load_initiative(self) -> None:
        assert self.current_epic is not None
        self.altitude = "initiative"
        self._set_crumb()
        self._set_lens_strip()
        base = f"parent = {self.current_epic.key}"
        if self.lens["initiative"] == "Mine":
            base += " AND assignee = currentUser()"
        jql = base + " ORDER BY status ASC, updated DESC"
        await self._show_rows(await self._search_rows(jql), "no tasks in this lens")

    # ── altitude 2: task ──────────────────────────────────────────────────────
    @work(exclusive=True)
    async def load_task(self) -> None:
        assert self.current_task is not None
        key = self.current_task[0]
        self.altitude = "task"
        self._set_crumb()
        self._set_lens_strip()
        try:
            async with JiraClient(self.config) as api:
                issue = await api.get_issue(
                    key, fields=["summary", "status", "priority", "issuetype", "assignee", "description"]
                )
        except Exception as e:
            await self._swap(Static(f"[red]{self._notify_err(e)}[/]", classes="detail"), focus_list=False)
            return
        f = issue.get("fields", {})
        status = (f.get("status") or {}).get("name", "—")
        head = Text()
        head.append(f"[● {status}]", style=GROUP_STYLE.get(normalize_status(status), "white") + " bold")
        head.append("  ")
        head.append((f.get("issuetype") or {}).get("name", ""), style="white")
        head.append("  ·  ", style="dim")
        head.append((f.get("priority") or {}).get("name", "—"), style="white")
        head.append("  ·  @", style="dim")
        head.append((f.get("assignee") or {}).get("displayName", "unassigned"), style="white")
        desc = f.get("description")
        body_text = render_to_text(desc) if desc else "(no description)"
        lines = body_text.splitlines()[:24]
        await self._swap(
            Static(head, classes="detail"),
            Static("\n".join(lines) or "(no description)", classes="detail"),
            focus_list=False,
        )

    # ── navigation ────────────────────────────────────────────────────────────
    def _highlighted(self) -> Any:
        for node in self.query(ListView):
            if node.has_focus or node.highlighted_child is not None:
                return node.highlighted_child
        return None

    def _descend_from(self, item: Any) -> None:
        if isinstance(item, _EpicRow):
            self.current_epic = item.epic
            self.load_initiative()
        elif isinstance(item, _ReviewRow):
            # external PR — service and dismiss; never joins the tree
            url = item.pr.get("url")
            if url:
                import webbrowser

                webbrowser.open(url)
                self.notify(f"opened review {item.pr.get('number', '')}", severity="information")
        elif isinstance(item, _TaskRow):
            self.current_task = (item.task_key, item.task_summary)
            self._task_from = self.altitude  # inbox, portfolio (Sprint lens), or initiative
            self.load_task()
        # _HeaderRow → ignored

    @on(ListView.Selected)
    def _on_selected(self, ev: ListView.Selected) -> None:
        # enter on a row is consumed by the ListView as Selected, so descend here.
        self._descend_from(ev.item)

    def action_descend(self) -> None:
        # l / → path (when a non-list altitude is focused, or as an alias).
        self._descend_from(self._highlighted())

    def action_ascend(self) -> None:
        # esc always walks back toward home (inbox).
        if self.altitude == "task":
            {"inbox": self.load_inbox, "portfolio": self.load_portfolio}.get(
                self._task_from, self.load_initiative
            )()
        elif self.altitude == "initiative":
            self.load_portfolio()
        elif self.altitude == "portfolio":
            self.load_inbox()
        # inbox is home — no-op

    def action_inbox(self) -> None:
        self.load_inbox()

    def action_portfolio(self) -> None:
        self.load_portfolio()

    def action_refresh(self) -> None:
        {
            "inbox": self.load_inbox,
            "portfolio": self.load_portfolio,
            "initiative": self.load_initiative,
            "task": self.load_task,
        }[self.altitude]()

    def _cycle_lens(self, direction: int) -> None:
        lenses = LENSES.get(self.altitude, [])
        if not lenses:
            return
        cur = self.lens.get(self.altitude, lenses[0])
        self.lens[self.altitude] = lenses[(lenses.index(cur) + direction) % len(lenses)]
        self.action_refresh()

    def action_lens_next(self) -> None:
        self._cycle_lens(1)

    def action_lens_prev(self) -> None:
        self._cycle_lens(-1)

    def action_open_browser(self) -> None:
        import webbrowser

        base = self.config.default_cloud_url.rstrip("/") if self.config.default_cloud_url else ""
        if not base:
            self.notify("no cloud_url configured", severity="warning")
            return
        key = None
        if self.altitude == "task" and self.current_task:
            key = self.current_task[0]
        else:
            item = self._highlighted()
            if isinstance(item, _EpicRow):
                key = item.epic.key
            elif isinstance(item, _TaskRow):
                key = item.task_key
        if key:
            webbrowser.open(f"{base}/browse/{key}")
            self.notify(f"opened {key}", severity="information")

    # ── actions (work on the focused item at any altitude) ─────────────────────
    def _focused_task_key(self) -> str | None:
        if self.altitude == "task" and self.current_task:
            return self.current_task[0]
        item = self._highlighted()
        return item.task_key if isinstance(item, _TaskRow) else None

    def _project_dir_for_epic(self, key: str | None) -> str | None:
        if not key:
            return None
        for p in self.config.projects:
            if key in p.jql:
                base = projectdocs.project_base(p)
                if base and base.is_dir():
                    return str(base)
        return None

    def _spawn(self, cmd: str, title: str, cwd: str | None = None) -> None:
        try:
            if cwd:
                spawn_in_dir(cmd, cwd=cwd, title=title, config=self.config.tmux)
            else:
                spawn(cmd, title=title, config=self.config.tmux)
        except RuntimeError as e:
            self.notify(str(e), severity="error")
            return
        self.notify(f"opened {title}", severity="information")

    def action_transition(self) -> None:
        key = self._focused_task_key()
        if not key:
            self.notify("focus a task first", severity="warning")
            return
        self.run_worker(self._do_transition(key))

    async def _do_transition(self, key: str) -> None:
        try:
            async with JiraClient(self.config) as api:
                transitions = await api.get_transitions(key)
        except Exception as e:
            self.notify(self._notify_err(e), severity="error")
            return
        if not transitions:
            self.notify("no transitions", severity="warning")
            return

        def _pick(tid: str | None) -> None:
            if tid:
                self.run_worker(self._apply_transition(key, tid))

        self.app.push_screen(TransitionModal(transitions), _pick)

    async def _apply_transition(self, key: str, tid: str) -> None:
        try:
            async with JiraClient(self.config) as api:
                try:
                    await api.transition_issue(key, tid)
                except ApiError as e:
                    if "resolution" in str(e).lower():
                        await api.transition_issue(key, tid, resolution="Done")
                    else:
                        raise
        except Exception as e:
            self.notify(f"transition failed: {e}", severity="error")
            return
        self.notify("✓ transitioned", severity="information")
        self.action_refresh()

    def action_assign(self) -> None:
        key = self._focused_task_key()
        if not key:
            self.notify("focus a task first", severity="warning")
            return

        def _pick(target: str | None) -> None:
            if target:
                self.run_worker(self._apply_assign(key, target))

        self.app.push_screen(AssignModal(), _pick)

    async def _apply_assign(self, key: str, target: str) -> None:
        try:
            async with JiraClient(self.config) as api:
                if target.lower() in ("@me", "me"):
                    me = await api.myself()
                    await api.edit_issue(key, {"assignee": {"accountId": me["accountId"]}})
                elif target.lower() in ("none", "unassign", "-"):
                    await api.edit_issue(key, {"assignee": None})
                else:
                    results = await api.find_user(target.lstrip("@"))
                    if not results:
                        self.notify(f"no user matches '{target}'", severity="warning")
                        return
                    await api.edit_issue(key, {"assignee": {"accountId": results[0]["accountId"]}})
        except Exception as e:
            self.notify(f"assign failed: {e}", severity="error")
            return
        self.notify("✓ assigned", severity="information")
        self.action_refresh()

    def action_comment(self) -> None:
        key = self._focused_task_key()
        if not key:
            self.notify("focus a task first", severity="warning")
            return

        def _submit(text: str | None) -> None:
            if text:
                self.run_worker(self._apply_comment(key, text))

        self.app.push_screen(CommentModal(key), _submit)

    async def _apply_comment(self, key: str, text: str) -> None:
        try:
            async with JiraClient(self.config) as api:
                await api.add_comment(key, text_to_adf(text))
        except Exception as e:
            self.notify(f"comment failed: {e}", severity="error")
            return
        self.notify("✓ commented", severity="information")

    def action_claude(self) -> None:
        claude = self.config.ai.claude_path
        key = self._focused_task_key()
        if key:  # a task → run the configured ticket command (e.g. /issue CH-142)
            self._spawn(f"{claude} {quote_for_shell(f'{self.config.ai.default_command} {key}')}", key)
            return
        item = self._highlighted()
        if isinstance(item, _ReviewRow):  # external PR → /review
            url = item.pr.get("url", "")
            self._spawn(f"{claude} {quote_for_shell('/review ' + url)}", f"review·{item.pr.get('number', '')}")
            return
        # epic (initiative altitude or a highlighted epic) → claude scoped to its dir
        ekey = item.epic.key if isinstance(item, _EpicRow) else (self.current_epic.key if self.current_epic else None)
        if ekey:
            self._spawn(claude, ekey, cwd=self._project_dir_for_epic(ekey))

    def action_decompose(self) -> None:
        if self.altitude != "initiative" or not self.current_epic:
            self.notify("decompose works on an initiative (descend into an epic)", severity="warning")
            return
        e = self.current_epic
        self.app.push_screen(ScopeGateModal(e.key, e.summary, e.total, e.done), self._decompose_after_scope)

    def _decompose_after_scope(self, scope: str | None) -> None:
        if not scope:
            return
        level = progress.read_level(gates.EPIC_DECOMPOSE.pattern)
        self.app.push_screen(
            GateModal(gates.EPIC_DECOMPOSE, level), lambda d: self._decompose_orchestrate(scope, d)
        )

    def _decompose_orchestrate(self, scope: str, decision: dict | None) -> None:
        if not decision or self.current_epic is None:
            return
        e = self.current_epic
        option = decision["option"] or gates.GateOption("(self-proposed)", "", "", "(watch your own stated risks)")
        prompt = gates.build_decompose_prompt(option, e.key, e.summary, scope, decision["reasoning"])
        cmd = f"{self.config.ai.claude_path} {quote_for_shell(prompt)}"
        cwd = self._project_dir_for_epic(e.key)
        try:
            if cwd:
                spawn_in_dir(cmd, cwd=cwd, title=f"decompose·{e.key}", config=self.config.tmux)
            else:
                spawn(cmd, title=f"decompose·{e.key}", config=self.config.tmux)
        except RuntimeError as ex:
            self.notify(str(ex), severity="error")
            return
        progress.record_use(gates.EPIC_DECOMPOSE.pattern)
        self.notify(f"orchestrating {option.name} decomposition of {e.key}", severity="information")


def run_workspace(config: Config) -> None:
    WorkspaceApp(config).run()
