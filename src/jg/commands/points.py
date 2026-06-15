"""jg points <KEY> [VALUE] — view or set the story-points field.

The field id + type are configured per-instance in [fields] of config.toml:

    [fields]
    story_points = "customfield_10252"
    story_points_type = "select"   # or "number"

For a "select" field the allowed options are fetched live from the issue's
editmeta and validated before writing — so an out-of-range value (the
CH-523/579/580 case) is rejected with a clear message instead of a raw 400.
"""

from __future__ import annotations

import click
from rich.console import Console

from jg.api import ApiError, JiraClient
from jg.auth import AuthError
from jg.cli import async_command
from jg.config import Config
from jg.render import points_value

console = Console()
err = Console(stderr=True)

_NOT_CONFIGURED = (
    "[yellow]No story-points field configured.[/] Add to ~/.config/jg/config.toml:\n"
    "  [fields]\n"
    '  story_points = "customfield_XXXXX"\n'
    '  story_points_type = "number"  # or "select"'
)


@click.command()
@click.argument("key")
@click.argument("value", required=False)
@click.pass_context
@async_command
async def points(ctx: click.Context, key: str, value: str | None) -> None:
    """View story points on <KEY>, or set them to VALUE."""
    config: Config = ctx.obj["config"]
    field_id = config.fields.story_points
    if not field_id:
        err.print(_NOT_CONFIGURED)
        ctx.exit(1)
    sp_type = config.fields.story_points_type

    try:
        async with JiraClient(config) as api:
            if value is None:
                issue = await api.get_issue(key, fields=["summary", field_id])
                cur = points_value(issue.get("fields", {}), field_id)
                console.print(f"{key} · points: [cyan]{cur or '—'}[/]")
                return

            if sp_type == "select":
                meta = await api.get_edit_meta(key)
                fmeta = (meta.get("fields") or {}).get(field_id)
                if fmeta is None:
                    err.print(
                        f"[red]✗[/] {field_id} isn't editable on {key} "
                        "(not on the edit screen for this issue type)."
                    )
                    ctx.exit(1)
                allowed = [str(o.get("value")) for o in (fmeta.get("allowedValues") or [])]
                if allowed and value not in allowed:
                    err.print(
                        f"[red]✗[/] '{value}' not allowed — {field_id} accepts: "
                        f"{', '.join(allowed)}"
                    )
                    ctx.exit(1)
                payload: dict = {field_id: {"value": value}}
            else:
                try:
                    num = float(value)
                except ValueError:
                    err.print(f"[red]✗[/] '{value}' is not a number (field type is 'number').")
                    ctx.exit(1)
                payload = {field_id: int(num) if num.is_integer() else num}

            await api.edit_issue(key, payload)
    except (AuthError, ApiError) as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    console.print(f"[green]✓[/] set points on {key} to {value}")
