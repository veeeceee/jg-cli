"""jg flow — the my-work flow-home (incoming / in-progress / resolving)."""

from __future__ import annotations

import click

from jg.config import Config
from jg.flow import run_flow


@click.command(name="flow")
@click.pass_context
def flow(ctx: click.Context) -> None:
    """My work as flow-state: incoming / in-progress / resolving."""
    config: Config = ctx.obj["config"]
    run_flow(config)
