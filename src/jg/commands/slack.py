"""jg slack — DMs, @mentions, and followed-channel messages (+ auth setup)."""

from __future__ import annotations

import click
from rich.console import Console
from rich.text import Text

from jg import slack as slackmod
from jg.cli import async_command
from jg.config import Config

console = Console()
err = Console(stderr=True)

_GLYPH = {"dm": ("✉", "magenta"), "mention": ("@", "#7aa2f7"), "channel": ("#", "green")}


@click.group(name="slack", invoke_without_command=True)
@click.pass_context
@async_command
async def slack(ctx: click.Context) -> None:
    """List Slack DMs / @mentions / followed channels. `jg slack auth` to set up."""
    if ctx.invoked_subcommand is not None:
        return
    config: Config = ctx.obj["config"]
    if not slackmod.is_setup():
        err.print("[yellow]Slack not configured.[/] Run `jg slack auth`.")
        ctx.exit(1)
    try:
        async with slackmod.SlackClient(config) as client:
            msgs = await client.incoming()
    except slackmod.SlackError as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    if not msgs:
        console.print("[dim]no recent DMs / mentions / followed-channel messages[/]")
        return
    console.print(f"[bold]Slack — incoming ({len(msgs)})[/]\n")
    for m in msgs:
        glyph, color = _GLYPH.get(m.kind, ("·", "dim"))
        line = Text(f"{glyph} ", style=color)
        line.append(f"{m.channel_name[:20]:<20}", style="bold #c0caf5")
        line.append(f"{m.user_name[:14]:<14} ", style="#7aa2f7")
        line.append(m.text[:44], style="#a9b1d6")
        if m.jira_keys:
            line.append("  → " + ",".join(m.jira_keys), style="green")
        console.print(line)


@slack.command()
@click.pass_context
def auth(ctx: click.Context) -> None:
    """Store the Slack user token from your app (read-only ingestion)."""
    console.print(
        "Create a [bold]Slack app[/] at https://api.slack.com/apps (From scratch),\n"
        "add these [bold]User Token Scopes[/] under OAuth & Permissions:\n"
        "  [cyan]search:read  im:read  im:history  channels:history  groups:history  users:read[/]\n"
        "Install to the workspace, then copy the [bold]User OAuth Token[/] (xoxp-…).\n"
        "Add followed channels under [dim]\\[slack] channels[/] in ~/.config/jg/config.toml.\n"
    )
    token = click.prompt("User OAuth Token", hide_input=True).strip()
    if not token.startswith("xoxp-"):
        err.print("[yellow]Warning:[/] expected a user token (xoxp-…); a bot token can't see your DMs.")
    slackmod.set_token(token)
    console.print("[green]✓[/] Slack token stored.")
