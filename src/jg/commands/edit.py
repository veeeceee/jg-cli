"""ch edit <KEY> — set priority, labels, components, fix versions, summary."""

from __future__ import annotations

import click
from rich.console import Console

from jg.api import ApiError, JiraClient
from jg.auth import AuthError
from jg.cli import async_command
from jg.config import Config

console = Console()
err = Console(stderr=True)

PRIORITY_NAMES = ("Highest", "High", "Medium", "Low", "Lowest")


def _normalize_priority(p: str) -> str | None:
    p_low = p.lower()
    # Prefer exact match (so "high" → "High", not "Highest")
    for name in PRIORITY_NAMES:
        if name.lower() == p_low:
            return name
    for name in PRIORITY_NAMES:
        if name.lower().startswith(p_low):
            return name
    return None


def _split_addremove(items: tuple[str, ...]) -> tuple[list[str], list[str]]:
    add: list[str] = []
    remove: list[str] = []
    for item in items:
        if item.startswith("-"):
            remove.append(item[1:])
        elif item.startswith("+"):
            add.append(item[1:])
        else:
            add.append(item)
    return add, remove


@click.command()
@click.argument("key")
@click.option("--summary", help="New summary")
@click.option("--priority", help="Highest/High/Medium/Low/Lowest")
@click.option("--label", "labels", multiple=True, help="+name to add, -name to remove")
@click.option("--component", "components", multiple=True, help="+name to add, -name to remove")
@click.option("--fixversion", "fix_versions", multiple=True, help="+name to add, -name to remove")
@click.pass_context
@async_command
async def edit(
    ctx: click.Context,
    key: str,
    summary: str | None,
    priority: str | None,
    labels: tuple[str, ...],
    components: tuple[str, ...],
    fix_versions: tuple[str, ...],
) -> None:
    """Edit field values on <KEY>."""
    config: Config = ctx.obj["config"]
    fields: dict = {}
    update: dict = {}

    if summary:
        fields["summary"] = summary

    if priority:
        norm = _normalize_priority(priority)
        if not norm:
            err.print(f"[red]✗[/] Unknown priority '{priority}'. Use one of: {', '.join(PRIORITY_NAMES)}")
            ctx.exit(1)
        fields["priority"] = {"name": norm}

    if labels:
        add, remove = _split_addremove(labels)
        ops = [{"add": label} for label in add] + [{"remove": label} for label in remove]
        update["labels"] = ops

    if components:
        add, remove = _split_addremove(components)
        ops = [{"add": {"name": c}} for c in add] + [{"remove": {"name": c}} for c in remove]
        update["components"] = ops

    if fix_versions:
        add, remove = _split_addremove(fix_versions)
        ops = [{"add": {"name": v}} for v in add] + [{"remove": {"name": v}} for v in remove]
        update["fixVersions"] = ops

    if not fields and not update:
        err.print("[yellow]Nothing to edit. Pass --summary, --priority, --label, --component, --fixversion.[/]")
        ctx.exit(1)

    try:
        async with JiraClient(config) as api:
            await api.edit_issue(key, fields, update or None)
    except (AuthError, ApiError) as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    console.print(f"[green]✓[/] updated {key}")
