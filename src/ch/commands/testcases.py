"""ch testcases <KEY> — view or edit the Test cases custom field (CH-specific)."""

from __future__ import annotations

import os
import subprocess
import tempfile

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from ch.adf import render_to_text, text_to_adf
from ch.api import ApiError, JiraClient
from ch.auth import AuthError
from ch.cli import async_command
from ch.config import Config

console = Console()
err = Console(stderr=True)

TEST_CASES_FIELD = "customfield_10186"


@click.command()
@click.argument("key")
@click.option("--edit", is_flag=True, help="Open $EDITOR to edit")
@click.pass_context
@async_command
async def testcases(ctx: click.Context, key: str, edit: bool) -> None:
    """View or edit Test cases on <KEY>."""
    config: Config = ctx.obj["config"]
    try:
        async with JiraClient(config) as api:
            issue = await api.get_issue(key, fields=["summary", TEST_CASES_FIELD])
            current = render_to_text(issue["fields"].get(TEST_CASES_FIELD))
            if not edit:
                if not current:
                    console.print(f"[yellow]No test cases on {key}.[/]")
                    return
                console.print(Panel(Markdown(current), title=f"{key} · Test cases", border_style="dim"))
                return

            editor = os.environ.get("EDITOR", "vi")
            with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
                f.write(current)
                path = f.name
            try:
                subprocess.run([editor, path], check=True)
                with open(path) as f:
                    new = f.read().strip()
            finally:
                os.unlink(path)
            if new == current:
                console.print("[dim]No changes.[/]")
                return
            await api.edit_issue(key, {TEST_CASES_FIELD: text_to_adf(new) if new else None})
    except (AuthError, ApiError) as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    console.print(f"[green]✓[/] updated test cases on {key}")
