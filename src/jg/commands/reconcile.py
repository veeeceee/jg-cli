"""jg reconcile — where declared (Jira) / actual (live sessions) / artifact (PRs)
disagree for my in-progress work. The deterministic reconcile floor, standalone."""

from __future__ import annotations

import click
from rich.console import Console
from rich.text import Text

from jg import reconcile as rec
from jg.cli import async_command
from jg.config import Config

console = Console()
err = Console(stderr=True)

# state → (label, style)
_STYLE = {
    rec.State.STALLED: ("stalled", "red"),
    rec.State.COLD: ("cold", "yellow"),
    rec.State.UNDECLARED: ("undeclared", "magenta"),
    rec.State.DONE_BUT_OPEN: ("done→open", "green"),
    rec.State.RESOLVING: ("resolving", "cyan"),
    rec.State.UNTRACKED: ("untracked", "orange3"),
    rec.State.HEALTHY: ("healthy", "green"),
    rec.State.TRACKED: ("tracked", "dim"),
}
_MISMATCH_ORDER = [
    rec.State.STALLED,
    rec.State.COLD,
    rec.State.UNDECLARED,
    rec.State.DONE_BUT_OPEN,
    rec.State.RESOLVING,
    rec.State.UNTRACKED,
]


def _line(item: rec.ReconcileItem) -> Text:
    label, style = _STYLE.get(item.state, (str(item.state), "white"))
    t = Text("  ")
    t.append(f"{label:<11}", style=style)
    t.append(f"{item.key or '—':<9} ", style="bold #c0caf5")
    detail: list[str] = []
    if item.jira_status:
        detail.append(item.jira_status)
    if item.session_warm is True:
        detail.append("warm session")
    elif item.session_warm is False:
        detail.append("cold session")
    if item.pr_state:
        detail.append(f"PR {item.pr_state}")
    if item.key is None and item.session_title:
        detail.append(f'pane "{item.session_title[:32]}"')
    t.append("  ".join(detail), style="dim")
    return t


@click.command(name="reconcile")
@click.pass_context
@async_command
async def reconcile(ctx: click.Context) -> None:
    """Reconcile declared / actual / artifact for my in-progress work."""
    config: Config = ctx.obj["config"]
    items = await rec.gather(config)
    if not items:
        console.print("[dim]nothing in progress — no live sessions, open tickets, or PRs[/]")
        return

    mism = [i for i in items if i.is_mismatch]
    good = [i for i in items if not i.is_mismatch]

    if mism:
        console.print(f"[bold]⚠ needs attention ({len(mism)})[/]")
        for i in sorted(mism, key=lambda x: _MISMATCH_ORDER.index(x.state)):
            console.print(_line(i))
    if good:
        console.print(f"\n[dim]✓ healthy / tracked ({len(good)})[/]")
        for i in good:
            console.print(_line(i))
