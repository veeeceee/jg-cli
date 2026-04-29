"""ch dashboard — Textual TUI."""

from __future__ import annotations

import click

from jg.config import Config


@click.command()
@click.pass_context
def dashboard(ctx: click.Context) -> None:
    """Open the TUI dashboard (kanban + PR sidebar)."""
    config: Config = ctx.obj["config"]
    from jg.tui import run_dashboard

    run_dashboard(config)
