"""ch create — create a new ticket."""

from __future__ import annotations

import os
import subprocess
import tempfile

import click
from rich.console import Console

from ch.adf import text_to_adf
from ch.api import ApiError, JiraClient
from ch.auth import AuthError
from ch.cli import async_command
from ch.config import Config

console = Console()
err = Console(stderr=True)


def _editor(prompt: str = "") -> str:
    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
        if prompt:
            f.write(f"# {prompt}\n# Lines starting with # are ignored.\n")
        path = f.name
    try:
        subprocess.run([editor, path], check=True)
        with open(path) as f:
            content = f.read()
    finally:
        os.unlink(path)
    return "\n".join(ln for ln in content.splitlines() if not ln.startswith("#")).strip()


@click.command()
@click.option("--project", help="Project key (defaults to config)")
@click.option("--type", "issue_type", default="Task", help="Issue type (Task/Bug/Story/Epic)")
@click.option("--summary", help="Summary (skips prompt)")
@click.option("--description", help="Description text (skips $EDITOR)")
@click.option("--priority", help="Highest/High/Medium/Low/Lowest")
@click.pass_context
@async_command
async def create(
    ctx: click.Context,
    project: str | None,
    issue_type: str,
    summary: str | None,
    description: str | None,
    priority: str | None,
) -> None:
    """Create a new Jira ticket."""
    config: Config = ctx.obj["config"]
    project = project or config.default_project
    if not project:
        err.print("[red]✗[/] No project. Pass --project or set default in config.")
        ctx.exit(1)

    if not summary:
        summary = click.prompt("Summary").strip()
    if description is None:
        description = _editor("Write your description above this line.") or ""

    fields: dict = {
        "project": {"key": project},
        "summary": summary,
        "issuetype": {"name": issue_type},
    }
    if description:
        fields["description"] = text_to_adf(description)
    if priority:
        fields["priority"] = {"name": priority}

    try:
        async with JiraClient(config) as api:
            result = await api.create_issue(fields)
    except (AuthError, ApiError) as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)

    console.print(f"[green]✓[/] created [bold]{result.get('key')}[/]")
