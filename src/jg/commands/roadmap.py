"""jg roadmap — portfolio altitude: epics with child progress.

The layer above the sprint kanban: every epic (initiative) with a progress bar,
its own status, and a blocked flag. Active work sorts to the top; finished epics
sink to the bottom.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.text import Text

from jg import roadmap as rm
from jg.cli import async_command
from jg.config import Config
from jg.render import GROUP_STYLE

console = Console()
err = Console(stderr=True)


@click.command()
@click.option("--jql", default=None, help="Override which epics to show")
@click.pass_context
@async_command
async def roadmap(ctx: click.Context, jql: str | None) -> None:
    """Show epics as a portfolio roadmap with progress."""
    config: Config = ctx.obj["config"]
    effective = jql or rm.effective_jql(config)
    try:
        epics = await rm.fetch_roadmap(config, jql=jql)
    except Exception as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    if not epics:
        console.print(f"[yellow]No epics.[/] [dim]{effective}[/]")
        return
    console.print(f"[bold]Roadmap[/]  [dim]{effective} · {len(epics)} epics[/]\n")
    for e in epics:
        console.print(_row(e))


def _bar_style(e: rm.Epic) -> str:
    if e.is_done_status or e.pct == 100:
        return "green"
    return "cyan" if e.pct > 0 else "dim"


def _row(e: rm.Epic) -> Text:
    t = Text()
    t.append(f"{e.key:>8}  ", style="bold #c0caf5")
    t.append(rm.progress_bar(e.pct) + " ", style=_bar_style(e))
    t.append(f"{e.pct:>3}% ", style="bold")
    t.append(f"{e.done}/{e.total}".ljust(8), style="dim")
    t.append(f"{e.status}  ", style=GROUP_STYLE.get(e.status_group, "white"))
    t.append(e.summary[:44])
    if e.blocked:
        t.append(f"  ⚠ {e.blocked} blocked", style="red")
    return t
