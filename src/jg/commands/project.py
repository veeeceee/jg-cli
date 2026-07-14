"""jg project — read-only project workspace (plan · research · docs · work).

The CLI mirror of the TUI's ProjectDetailModal: surfaces everything jg knows
about a project in one place. Local reads are instant; a single JQL call powers
the work roll-up.
"""

from __future__ import annotations

import datetime as dt

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from jg import projectdocs
from jg.api import ApiError, JiraClient
from jg.auth import AuthError
from jg.cli import async_command
from jg.config import Config, Project
from jg.render import GROUP_ORDER, GROUP_STYLE, normalize_status

console = Console()
err = Console(stderr=True)

_BORDER = "#6c7086"


@click.command()
@click.argument("name", required=False)
@click.pass_context
@async_command
async def project(ctx: click.Context, name: str | None) -> None:
    """Show a project's plan, research, docs, and work roll-up.

    With no NAME, lists configured projects.
    """
    config: Config = ctx.obj["config"]
    if not config.projects:
        err.print("[yellow]No projects configured.[/] Add [[projects]] blocks to ~/.config/jg/config.toml")
        ctx.exit(1)
    if not name:
        _list_projects(config)
        return
    proj = config.project_by_name(name)
    if proj is None:
        known = ", ".join(p.name for p in config.projects)
        err.print(f"[red]✗[/] no project named '{name}'. Known: {known}")
        ctx.exit(1)
    await _show(config, proj)


def _list_projects(config: Config) -> None:
    console.print("[bold]Projects[/]")
    for p in config.projects:
        bits = []
        if p.jql:
            bits.append(p.jql)
        if p.repos:
            bits.append(f"{len(p.repos)} repos")
        console.print(f"  [bold #c0caf5]{p.name}[/]  [dim]{'  ·  '.join(bits)}[/]")
    console.print("\n[dim]jg project <name> for the full workspace[/]")


async def _show(config: Config, proj: Project) -> None:
    header = Text()
    header.append(proj.name, style="bold #c0caf5")
    bits = []
    if proj.jql:
        bits.append(proj.jql)
    if proj.repos:
        bits.append(f"{len(proj.repos)} repos")
    if bits:
        header.append("\n" + "  ·  ".join(bits), style="dim")
    console.print(header)
    console.print()

    console.print(Panel(_plan(proj), title="Plan", title_align="left", border_style=_BORDER))
    research = projectdocs.list_research(proj, limit=8)
    console.print(Panel(_research(proj, research), title=f"Research ({len(research)})",
                        title_align="left", border_style=_BORDER))
    console.print(Panel(_docs(config, proj), title="Docs & memory", title_align="left", border_style=_BORDER))
    console.print(Panel(await _rollup(config, proj), title="Work", title_align="left", border_style=_BORDER))


def _plan(proj: Project) -> Text:
    ps = projectdocs.plan_summary(proj)
    t = Text()
    if ps is None:
        t.append("no plan pointer — set docs.plan in config", style="dim")
    elif not ps.exists:
        t.append(f"⚠ plan not found: {ps.path}", style="red")
    else:
        t.append(ps.title + "\n", style="bold")
        if ps.excerpt:
            t.append(ps.excerpt + "\n")
        t.append(str(ps.path), style="dim")
    return t


def _research(proj: Project, research: list) -> Text:
    t = Text()
    if research:
        for r in research:
            date = dt.datetime.fromtimestamp(r.mtime).strftime("%Y-%m-%d") if r.mtime else "—"
            t.append(f"{date}  ", style="cyan")
            t.append(r.title + "\n")
    else:
        t.append("no research yet — jg dashboard → p → R to start a session\n", style="dim")
    t.append(str(projectdocs.research_path(proj)), style="dim")
    return t


def _docs(config: Config, proj: Project) -> Text:
    docs = projectdocs.doc_links(proj)
    conf = projectdocs.confluence_links(proj, config)
    t = Text()
    for d in docs:
        if d.kind == "memory":
            t.append("◈ ", style="magenta")
            t.append(d.label, style="white" if d.exists else "red")
            if not d.exists:
                t.append(" (not found)", style="dim red")
        elif d.kind == "docfile":
            t.append("· ", style="dim")
            t.append(d.label)
        else:  # docdir
            t.append("▸ ", style="dim")
            t.append(d.label, style="white" if d.exists else "red")
            t.append(f"  ({d.count} md)" if d.exists else " (not found)", style="dim")
        t.append("\n")
    for c in conf:
        t.append("→ ", style="blue")
        t.append(c.key, style="blue")
        if c.url:
            t.append(f"  {c.url}", style="dim")
        t.append("\n")
    if not docs and not conf:
        t.append("no docs/memory pointers configured", style="dim")
    return t


async def _rollup(config: Config, proj: Project) -> Text:
    jql = proj.jql.strip()
    if not jql:
        return Text("no jql configured", style="dim")
    try:
        async with JiraClient(config) as api:
            data = await api.search_jql(jql, fields=["status"], max_results=100)
    except (AuthError, ApiError) as e:
        return Text(f"load failed: {e}", style="red")
    except Exception as e:  # network/unexpected
        return Text(f"load failed: {type(e).__name__}", style="red")
    issues = data.get("issues") or []
    counts: dict[str, int] = {}
    for iss in issues:
        name = ((iss.get("fields") or {}).get("status") or {}).get("name", "")
        group = normalize_status(name) if name else "?"
        counts[group] = counts.get(group, 0) + 1
    if not counts:
        return Text("no matching tickets", style="dim")
    total = sum(counts.values())
    t = Text()
    t.append(f"{total}{'+' if len(issues) >= 100 else ''} open   ", style="bold")
    ordered = [g for g in GROUP_ORDER if g in counts] + [g for g in counts if g not in GROUP_ORDER]
    for i, group in enumerate(ordered):
        if i:
            t.append("  ·  ", style="dim")
        style = GROUP_STYLE.get(group, "white")
        t.append(f"{group} ", style=style)
        t.append(str(counts[group]), style=f"bold {style}")
    return t
