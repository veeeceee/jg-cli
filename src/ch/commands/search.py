"""ch search "<jql>" — JQL passthrough."""

from __future__ import annotations

import json

import click
from rich.console import Console

from ch.api import ApiError, JiraClient
from ch.auth import AuthError
from ch.cli import async_command
from ch.config import Config
from ch.render import render_sprint_tables

console = Console()
err = Console(stderr=True)


@click.command()
@click.argument("jql")
@click.option("--limit", default=50, help="Max results (1-100)")
@click.option("--json", "as_json", is_flag=True, help="Raw JSON output")
@click.pass_context
@async_command
async def search(ctx: click.Context, jql: str, limit: int, as_json: bool) -> None:
    """Run a JQL query."""
    config: Config = ctx.obj["config"]
    try:
        async with JiraClient(config) as api:
            data = await api.search_jql(
                jql,
                fields=["summary", "status", "priority", "issuetype", "updated"],
                max_results=min(limit, 100),
            )
    except (AuthError, ApiError) as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)

    issues = data.get("issues", [])
    if as_json:
        click.echo(json.dumps(issues, indent=2))
        return
    if not issues:
        console.print("[yellow]No results.[/]")
        return
    for table in render_sprint_tables(issues):
        console.print(table)
        console.print()
