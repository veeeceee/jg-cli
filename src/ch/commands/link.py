"""ch link <FROM> <type> <TO> — create an issue link."""

from __future__ import annotations

import click
from rich.console import Console

from ch.api import ApiError, JiraClient
from ch.auth import AuthError
from ch.cli import async_command
from ch.config import Config

console = Console()
err = Console(stderr=True)

# Map short forms to canonical Jira link type names.
LINK_ALIASES = {
    "blocks": "Blocks",
    "is-blocked-by": "Blocks",  # reverse direction handled via inward/outward
    "blocked-by": "Blocks",
    "relates": "Relates",
    "relates-to": "Relates",
    "duplicates": "Duplicate",
    "is-duplicated-by": "Duplicate",
    "clones": "Cloners",
    "is-cloned-by": "Cloners",
}

# Which alias means inward (target → source) vs outward (source → target).
INWARD_ALIASES = {"is-blocked-by", "blocked-by", "is-duplicated-by", "is-cloned-by"}


@click.command()
@click.argument("from_key")
@click.argument("link_type")
@click.argument("to_key")
@click.pass_context
@async_command
async def link(ctx: click.Context, from_key: str, link_type: str, to_key: str) -> None:
    """Link <FROM> [type] <TO>. Types: blocks, is-blocked-by, relates, duplicates, clones."""
    config: Config = ctx.obj["config"]
    norm = link_type.lower().replace("_", "-").replace(" ", "-")
    canonical = LINK_ALIASES.get(norm)
    if not canonical:
        err.print(f"[red]✗[/] Unknown link type '{link_type}'. Try: {', '.join(sorted(LINK_ALIASES))}")
        ctx.exit(1)

    if norm in INWARD_ALIASES:
        inward, outward = to_key, from_key
        verb = link_type
    else:
        inward, outward = from_key, to_key
        verb = link_type

    try:
        async with JiraClient(config) as api:
            await api.create_link(canonical, inward_key=inward, outward_key=outward)
    except (AuthError, ApiError) as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    console.print(f"[green]✓[/] {from_key} [bold]{verb}[/] {to_key}")
