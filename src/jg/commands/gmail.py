"""jg gmail — recent relevant email in the inbox (+ auth setup)."""

from __future__ import annotations

import click
from rich.console import Console
from rich.text import Text

from jg import gmail as gmailmod
from jg.cli import async_command
from jg.config import Config

console = Console()
err = Console(stderr=True)


@click.group(name="gmail", invoke_without_command=True)
@click.pass_context
@async_command
async def gmail(ctx: click.Context) -> None:
    """List recent relevant email (jg's query). `jg gmail auth` to set up."""
    if ctx.invoked_subcommand is not None:
        return
    config: Config = ctx.obj["config"]
    if not config.gmail.is_setup:
        err.print("[yellow]Gmail not configured.[/] Run `jg gmail auth`.")
        ctx.exit(1)
    try:
        async with gmailmod.GmailClient(config) as client:
            msgs = await client.recent()
    except gmailmod.GmailError as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    if not msgs:
        console.print("[dim]no messages match the query[/]")
        return
    console.print(f"[bold]Gmail — {config.gmail.query}[/]  ({len(msgs)})\n")
    for m in msgs:
        line = Text()
        line.append("✉ " if not m.is_bulk else "▤ ", style="magenta" if not m.is_bulk else "dim")
        line.append(f"{gmailmod.sender_name(m.sender)[:22]:<22}", style="bold #c0caf5")
        line.append(m.subject[:44], style="#a9b1d6")
        if m.jira_keys:
            line.append("  → " + ",".join(m.jira_keys), style="green")
        if m.is_bulk:
            line.append("  bulk", style="dim")
        console.print(line)


@gmail.command()
@click.pass_context
def auth(ctx: click.Context) -> None:
    """Set up jg's Google OAuth client and run the read-only consent flow."""
    config: Config = ctx.obj["config"]
    console.print(
        "Create a [bold]Google OAuth client[/] (one-time):\n"
        "  1. https://console.cloud.google.com/ → create/pick a project\n"
        "  2. APIs & Services → Library → enable [cyan]Gmail API[/]\n"
        "  3. APIs & Services → Credentials → Create credentials → [bold]OAuth client ID[/]\n"
        "     → Application type: [bold]Desktop app[/]\n"
        "  4. On the OAuth consent screen, add your address as a [bold]Test user[/]\n"
        f"     (jg requests only the read-only scope: [cyan]{gmailmod.SCOPE}[/])\n"
        "  5. Copy the Client ID + Client secret below.\n"
    )
    client_id = click.prompt("Client ID").strip()
    client_secret = click.prompt("Client Secret", hide_input=True).strip()

    config.gmail.client_id = client_id
    gmailmod.set_client_secret(client_secret)
    console.print("[dim]Opening browser for consent…[/]")
    try:
        gmailmod.login(config.gmail)
    except gmailmod.GmailError as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    config.save()
    console.print("[green]✓[/] Gmail authorized (read-only).")
