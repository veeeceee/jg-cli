"""ch auth — OAuth setup, login, logout, status."""

from __future__ import annotations

import click
from rich.console import Console
from rich.panel import Panel

from jg import auth as auth_lib
from jg.config import Config

console = Console()
err = Console(stderr=True)


@click.group()
def auth() -> None:
    """Manage Atlassian OAuth credentials."""


@auth.command()
@click.pass_context
def setup(ctx: click.Context) -> None:
    """Walk through one-time OAuth app registration."""
    config: Config = ctx.obj["config"]

    intro = (
        "[bold]ch — OAuth setup[/bold]\n\n"
        "ch uses Atlassian OAuth 2.0 (3LO) to access Jira on your behalf.\n"
        "You need to register an OAuth app once. Steps:\n\n"
        "  1. Go to [cyan]https://developer.atlassian.com/console/myapps/[/cyan]\n"
        "  2. Click [bold]'Create' → 'OAuth 2.0 integration'[/bold]\n"
        "  3. Name it [bold]'ch-cli'[/bold] (or anything you like)\n"
        "  4. Once created, open the app's [bold]Permissions[/bold] tab and add Jira API scopes:\n"
        "       read:jira-work, write:jira-work, read:jira-user\n"
        "  5. Open [bold]Authorization → OAuth 2.0 (3LO)[/bold] and set callback URL:\n"
        f"       [cyan]{config.redirect_uri}[/cyan]\n"
        "  6. Copy [bold]Client ID[/bold] and [bold]Client secret[/bold] from the [bold]Settings[/bold] tab\n"
    )
    console.print(Panel(intro, expand=False, border_style="cyan"))

    client_id = click.prompt("Client ID", type=str).strip()
    client_secret = click.prompt("Client secret", type=str, hide_input=True).strip()

    config.client_id = client_id
    config.save()
    auth_lib.set_client_secret(client_secret)

    console.print("[green]✓[/] Saved client_id to config and client_secret to Keychain.")
    console.print("Next: [bold]ch auth login[/]")


@auth.command()
@click.pass_context
def login(ctx: click.Context) -> None:
    """Open browser, complete OAuth flow, store tokens."""
    config: Config = ctx.obj["config"]
    if not config.is_setup:
        err.print("[red]OAuth not set up.[/] Run: [bold]ch auth setup[/]")
        ctx.exit(1)
    console.print(f"[dim]Using callback: {config.redirect_uri}[/]")
    console.print("[dim](This must be registered exactly in your Atlassian OAuth app's Authorization tab.)[/]\n")
    try:
        tokens = auth_lib.login(config)
    except auth_lib.AuthError as e:
        err.print(f"[red]✗[/] {e}")
        if "redirect_uri" in str(e) or "unauthorized_client" in str(e):
            err.print(
                "\n[yellow]Hint:[/] go to https://developer.atlassian.com/console/myapps/ → "
                "your app → Authorization → OAuth 2.0 (3LO), and ensure the Callback URL is exactly:\n"
                f"  [bold]{config.redirect_uri}[/]"
            )
        ctx.exit(1)
    console.print("[green]✓[/] Logged in.")

    # Discover accessible resources, set default cloud_id if unset.
    resources = auth_lib.list_resources(tokens.access_token)
    if not resources:
        err.print("[yellow]No accessible Atlassian resources found.[/]")
        return
    if not config.default_cloud_id:
        # Pick first resource that has Jira scopes.
        for r in resources:
            if any("jira" in s for s in r.get("scopes", [])):
                config.default_cloud_id = r["id"]
                config.default_cloud_url = r["url"]
                config.save()
                console.print(f"  default cloud: [bold]{r['name']}[/] ({r['url']})")
                break

    if len(resources) > 1:
        console.print("\n  Other accessible resources:")
        for r in resources:
            mark = " (default)" if r["id"] == config.default_cloud_id else ""
            console.print(f"    • {r['name']} — {r['url']}{mark}")


@auth.command()
@click.pass_context
def logout(ctx: click.Context) -> None:
    """Remove stored tokens (keeps client_id/secret)."""
    auth_lib.clear_tokens()
    console.print("[green]✓[/] Tokens cleared. (Run [bold]ch auth login[/] to re-authenticate.)")


@auth.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show current auth state."""
    config: Config = ctx.obj["config"]
    console.print(f"  client_id:        {config.client_id or '[red]not set[/]'}")
    console.print(f"  client_secret:    {'[green]in keychain[/]' if auth_lib.get_client_secret() else '[red]missing[/]'}")
    tokens = auth_lib.get_tokens()
    if tokens:
        import time
        remaining = int(tokens.expires_at - time.time())
        if remaining > 0:
            console.print(f"  access_token:     [green]valid[/] ({remaining}s remaining)")
        else:
            console.print(f"  access_token:     [yellow]expired[/] (auto-refreshes on next call)")
        console.print(f"  refresh_token:    [green]present[/]")
    else:
        console.print("  tokens:           [red]not logged in[/]")
    console.print(f"  default_cloud_id: {config.default_cloud_id or '[yellow]not set[/]'}")
    console.print(f"  default_project:  {config.default_project or '[yellow]not set[/]'}")
