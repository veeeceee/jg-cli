"""ch assign <KEY> <@me|user> — change assignee."""

from __future__ import annotations

import click
from rich.console import Console

from jg.api import ApiError, JiraClient
from jg.auth import AuthError
from jg.cli import async_command
from jg.config import Config

console = Console()
err = Console(stderr=True)


@click.command()
@click.argument("key")
@click.argument("target")
@click.pass_context
@async_command
async def assign(ctx: click.Context, key: str, target: str) -> None:
    """Assign <KEY> to '@me', a username, or 'none' to unassign."""
    config: Config = ctx.obj["config"]
    try:
        async with JiraClient(config) as api:
            if target.lower() in ("@me", "me"):
                me = await api.myself()
                account_id = me["accountId"]
                display = me.get("displayName", "you")
            elif target.lower() in ("none", "unassign", "-"):
                account_id = None
                display = "—"
            else:
                results = await api.find_user(target.lstrip("@"))
                if not results:
                    err.print(f"[red]✗[/] No user matches '{target}'.")
                    ctx.exit(1)
                if len(results) > 1:
                    err.print(f"[yellow]Ambiguous '{target}'. Candidates:[/]")
                    for u in results[:5]:
                        err.print(f"    • {u.get('displayName')} ({u.get('emailAddress', '?')})")
                    ctx.exit(1)
                account_id = results[0]["accountId"]
                display = results[0].get("displayName", target)
            await api.edit_issue(key, {"assignee": {"accountId": account_id} if account_id else None})
    except (AuthError, ApiError) as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    console.print(f"[green]✓[/] {key} assigned to [bold]{display}[/]")
