"""ch ai — bridge to Claude Code in a tmux pane."""

from __future__ import annotations

import shlex

import click
from rich.console import Console

from ch.brainstorm import build_brainstorm_prompt_sync
from ch.cli import async_command
from ch.config import Config
from ch.tmux import quote_for_shell, spawn

console = Console()
err = Console(stderr=True)


@click.group(invoke_without_command=True)
@click.argument("key", required=False)
@click.pass_context
@async_command
async def ai(ctx: click.Context, key: str | None) -> None:
    """Open a Claude Code session in a tmux pane.

    `ch ai CH-434` opens a pane and runs `/issue CH-434` automatically.
    Sub-commands: `ch ai brainstorm`, `ch ai standup`, `ch ai sprint-review`.
    """
    if ctx.invoked_subcommand is not None:
        return
    if not key:
        click.echo(ctx.get_help())
        return

    config: Config = ctx.obj["config"]
    cmd = config.ai.default_command
    title = key
    initial = f"{cmd} {key}"
    full = f"{config.ai.claude_path} {shlex.quote(initial)}"
    spawn(full, title=title, config=config.tmux)
    console.print(f"[green]✓[/] opened pane for {key}")


@ai.command()
@click.option("--project", "project_name", default=None, help="Project name (defaults to first defined)")
@click.pass_context
def brainstorm(ctx: click.Context, project_name: str | None) -> None:
    """Open a Claude pane pre-loaded with project context for ideating new tickets."""
    config: Config = ctx.obj["config"]
    # Resolve project from name, or fall back to first defined.
    project = None
    if project_name:
        for p in config.projects:
            if p.name.lower() == project_name.lower():
                project = p
                break
        if not project:
            err.print(f"[yellow]No project named '{project_name}' found in config; brainstorming without scope.[/]")
    elif config.projects:
        project = config.projects[0]

    console.print(f"[dim]Building context for {project.name if project else '(no project)'}…[/]")
    try:
        prompt = build_brainstorm_prompt_sync(config, project)
    except Exception as e:
        err.print(f"[red]✗[/] failed to build context: {e}")
        ctx.exit(1)

    full = f"{config.ai.claude_path} {quote_for_shell(prompt)}"
    title = f"brainstorm·{project.name}" if project else "brainstorm"
    spawn(full, title=title, config=config.tmux)
    console.print(f"[green]✓[/] opened brainstorm pane ({title})")


@ai.command()
@click.pass_context
def standup(ctx: click.Context) -> None:
    """Open a Claude pane and run /standup."""
    config: Config = ctx.obj["config"]
    full = f"{config.ai.claude_path} {quote_for_shell('/standup')}"
    spawn(full, title="standup", config=config.tmux)


@ai.command(name="sprint-review")
@click.pass_context
def sprint_review(ctx: click.Context) -> None:
    """Open a Claude pane to analyze the current sprint."""
    config: Config = ctx.obj["config"]
    prompt = (
        "Run /sprint, then analyze the active sprint. Highlight at-risk items, "
        "stale tickets, missing fields blocking transitions, and propose any reprioritization. "
        "Be direct."
    )
    full = f"{config.ai.claude_path} {quote_for_shell(prompt)}"
    spawn(full, title="sprint-review", config=config.tmux)
