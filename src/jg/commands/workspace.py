"""jg workspace — the altitude-workspace shell (WIP; will replace `jg dashboard`)."""

from __future__ import annotations

import click

from jg.config import Config


@click.command()
@click.pass_context
def workspace(ctx: click.Context) -> None:
    """Open the altitude workspace: Home → Portfolio → Initiative → Task (WIP)."""
    config: Config = ctx.obj["config"]
    from jg.workspace import run_workspace

    run_workspace(config)
