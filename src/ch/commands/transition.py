"""ch transition <KEY> <status> — fuzzy-match transition + apply."""

from __future__ import annotations

import re

import click
from rich.console import Console

from ch.api import ApiError, JiraClient
from ch.auth import AuthError
from ch.cli import async_command
from ch.config import Config

console = Console()
err = Console(stderr=True)


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _match_transition(transitions: list[dict], query: str) -> dict | None:
    norm_q = _normalize(query)
    if not norm_q:
        return None
    # Exact normalized match first.
    for t in transitions:
        if _normalize(t["name"]) == norm_q:
            return t
        if _normalize(t["to"]["name"]) == norm_q:
            return t
    # Substring match.
    candidates = [
        t for t in transitions
        if norm_q in _normalize(t["name"]) or norm_q in _normalize(t["to"]["name"])
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


@click.command()
@click.argument("key")
@click.argument("status_words", nargs=-1, required=True)
@click.pass_context
@async_command
async def transition(ctx: click.Context, key: str, status_words: tuple[str, ...]) -> None:
    """Move <KEY> to a new status (fuzzy match)."""
    config: Config = ctx.obj["config"]
    query = " ".join(status_words)
    try:
        async with JiraClient(config) as api:
            transitions = await api.get_transitions(key)
            match = _match_transition(transitions, query)
            if not match:
                err.print(f"[red]✗[/] No transition matches '{query}'.")
                err.print("  Available:")
                for t in transitions:
                    err.print(f"    • {t['name']} → {t['to']['name']}")
                ctx.exit(1)
            await api.transition_issue(key, match["id"])
    except (AuthError, ApiError) as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)
    console.print(f"[green]✓[/] {key} → [bold]{match['to']['name']}[/]")
