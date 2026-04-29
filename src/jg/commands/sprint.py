"""ch sprint — list active sprint tickets grouped by status."""

from __future__ import annotations

import json

import click
from rich.console import Console

from jg.api import ApiError, JiraClient
from jg.auth import AuthError
from jg.cli import async_command
from jg.config import Config
from jg.render import render_sprint_tables

console = Console()
err = Console(stderr=True)


@click.command()
@click.option("--all", "show_all", is_flag=True, help="Don't filter by current user")
@click.option("--project", default=None, help="Limit to a project key (e.g. CH)")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
@async_command
async def sprint(ctx: click.Context, show_all: bool, project: str | None, as_json: bool) -> None:
    """Show active sprint tickets, grouped by status."""
    config: Config = ctx.obj["config"]
    project = project or config.default_project

    clauses = ["sprint in openSprints()"]
    if not show_all:
        clauses.append("assignee = currentUser()")
    if project:
        clauses.append(f"project = {project}")
    jql = " AND ".join(clauses) + " ORDER BY status ASC, priority DESC, updated DESC"

    try:
        async with JiraClient(config) as api:
            data = await api.search_jql(
                jql,
                fields=["summary", "status", "priority", "issuetype", "updated"],
                max_results=100,
            )
    except (AuthError, ApiError) as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)

    issues = data.get("issues", [])
    if as_json:
        click.echo(json.dumps(issues, indent=2))
        return

    if not issues:
        console.print("[yellow]No tickets in active sprint.[/]")
        return

    for table in render_sprint_tables(issues):
        console.print(table)
        console.print()
