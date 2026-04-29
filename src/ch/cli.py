"""ch CLI entrypoint."""

from __future__ import annotations

import asyncio
import sys
from functools import wraps
from typing import Any, Callable

import click
from rich.console import Console

from ch import __version__
from ch.config import Config

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
@click.version_option(__version__, prog_name="ch")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """ch — fast Jira + GitHub CLI with TUI dashboard and Claude Code bridge."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = Config.load()
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# Register subcommand groups lazily to keep startup fast.
def _register() -> None:
    from ch.commands import auth, sprint, view, transition, assign, comment, edit, link, create, search, testcases, ai, pr, dashboard

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
    cli.add_command(testcases.testcases)
    cli.add_command(ai.ai)
    cli.add_command(pr.pr)
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
