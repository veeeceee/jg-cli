"""jg zoho — Zoho Desk support tickets where I'm involved (+ auth setup)."""

from __future__ import annotations

import click
from rich.console import Console
from rich.text import Text

from jg import zoho as zohomod
from jg.cli import async_command
from jg.config import Config

console = Console()
err = Console(stderr=True)


@click.group(name="zoho", invoke_without_command=True)
@click.pass_context
@async_command
async def zoho(ctx: click.Context) -> None:
    """List Zoho Desk tickets where I'm involved. `jg zoho auth` to set up."""
    if ctx.invoked_subcommand is not None:
        return
    config: Config = ctx.obj["config"]
    if not config.zoho.is_setup:
        err.print("[yellow]Zoho not configured.[/] Set [zoho] client_id + org_id in config, then run `jg zoho auth`.")
        ctx.exit(1)
    try:
        async with zohomod.ZohoClient(config) as client:
            tickets = await zohomod.find_involved(client, config.zoho)
    except zohomod.ZohoError as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    if not tickets:
        console.print("[dim]no support tickets you're involved in[/]")
        return
    console.print(f"[bold]Zoho Desk — involved ({len(tickets)})[/]\n")
    for t in tickets:
        line = Text()
        line.append(f"#{t.ticket_number:<9}", style="bold #c0caf5")
        line.append(" [" + " ".join(t.involvement) + "] ", style="magenta")
        line.append(f"{t.status[:14]:<14}  ", style="cyan")
        link = "→ " + ",".join(t.jira_keys) if t.jira_keys else "○ unlinked"
        line.append(f"{link:<16}", style="green" if t.jira_keys else "dim")
        line.append("  " + t.subject[:40])
        console.print(line)


@zoho.command()
@click.pass_context
def auth(ctx: click.Context) -> None:
    """Set up jg's Zoho self-client and exchange a grant token for tokens."""
    config: Config = ctx.obj["config"]
    console.print(
        "Create a [bold]Self Client[/] at https://api-console.zoho.com/ (US DC), then generate a "
        f"grant token for scopes:\n  [cyan]{zohomod.SCOPES}[/]\n"
    )
    client_id = click.prompt("Client ID").strip()
    client_secret = click.prompt("Client Secret", hide_input=True).strip()
    grant = click.prompt("Grant token (code)").strip()

    config.zoho.client_id = client_id
    zohomod.set_client_secret(client_secret)
    try:
        zohomod.exchange_grant(config.zoho, grant)
    except zohomod.ZohoError as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    config.save()
    console.print("[green]✓[/] Zoho authorized.")
    if not config.zoho.org_id or not config.zoho.agent_emails:
        console.print(
            r"[yellow]Next:[/] add [dim]org_id[/] and [dim]agent_emails[/] under \[zoho] in "
            "~/.config/jg/config.toml so involvement detection works."
        )
