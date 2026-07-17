"""ch CLI entrypoint."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from functools import wraps
from typing import Any

import click
from rich.console import Console

from jg import __version__
from jg.config import Config

console = Console()
err_console = Console(stderr=True)


def async_command(f: Callable[..., Any]) -> Callable[..., Any]:
    """Allow click commands to be async."""

    @wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(f(*args, **kwargs))

    return wrapper


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(__version__, prog_name="jg")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """ch — fast Jira + GitHub CLI with TUI dashboard and Claude Code bridge."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = Config.load()
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# Register subcommand groups lazily to keep startup fast.
def _register() -> None:
    from jg.commands import (
        ai,
        assign,
        auth,
        cluster,
        comment,
        create,
        dashboard,
        edit,
        flow,
        link,
        points,
        pr,
        project,
        reconcile,
        research,
        roadmap,
        search,
        sprint,
        testcases,
        transition,
        view,
        zoho,
    )

    cli.add_command(auth.auth)
    cli.add_command(sprint.sprint)
    cli.add_command(view.view)
    cli.add_command(transition.transition)
    cli.add_command(assign.assign)
    cli.add_command(comment.comment)
    cli.add_command(edit.edit)
    cli.add_command(link.link)
    cli.add_command(create.create)
    cli.add_command(search.search)
    cli.add_command(points.points)
    cli.add_command(testcases.testcases)
    cli.add_command(ai.ai)
    cli.add_command(pr.pr)
    cli.add_command(project.project)
    cli.add_command(research.research)
    cli.add_command(roadmap.roadmap)
    cli.add_command(flow.flow)
    cli.add_command(cluster.cluster)
    cli.add_command(reconcile.reconcile)
    cli.add_command(zoho.zoho)
    cli.add_command(dashboard.dashboard)


def main() -> None:
    _register()
    try:
        cli()
    except KeyboardInterrupt:
        err_console.print("[yellow]Interrupted.[/]")
        sys.exit(130)


if __name__ == "__main__":
    main()
