"""ch comment <KEY> [text] — add a comment. '-' reads stdin; no text opens $EDITOR."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import click
from rich.console import Console

from jg.adf import text_to_adf
from jg.api import ApiError, JiraClient
from jg.auth import AuthError
from jg.cli import async_command
from jg.config import Config

console = Console()
err = Console(stderr=True)


def _get_text(text: str | None) -> str:
    if text == "-":
        return sys.stdin.read().strip()
    if text:
        return text
    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
        f.write("# Write your comment above this line. Lines starting with # are ignored.\n")
        path = f.name
    try:
        subprocess.run([editor, path], check=True)
        with open(path) as f:
            content = f.read()
    finally:
        os.unlink(path)
    lines = [ln for ln in content.splitlines() if not ln.startswith("#")]
    return "\n".join(lines).strip()


@click.command()
@click.argument("key")
@click.argument("text", required=False)
@click.pass_context
@async_command
async def comment(ctx: click.Context, key: str, text: str | None) -> None:
    """Add a comment to <KEY>. Use '-' for stdin or omit to open $EDITOR."""
    config: Config = ctx.obj["config"]
    body = _get_text(text)
    if not body:
        err.print("[yellow]Empty comment, nothing to add.[/]")
        ctx.exit(0)
    try:
        async with JiraClient(config) as api:
            await api.add_comment(key, text_to_adf(body))
    except (AuthError, ApiError) as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    console.print(f"[green]✓[/] commented on {key}")
