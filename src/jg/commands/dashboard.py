"""jg dashboard — the altitude workspace (Inbox → Portfolio → Initiative → Task)."""

from __future__ import annotations

import click

from jg.config import Config


@click.command()
@click.pass_context
def dashboard(ctx: click.Context) -> None:
    """Open the workspace: Inbox → Portfolio → Initiative → Task."""
    config: Config = ctx.obj["config"]
    from jg.workspace import run_workspace

    run_workspace(config)
