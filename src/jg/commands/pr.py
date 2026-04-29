"""ch pr — GitHub PR operations (wraps `gh`)."""

from __future__ import annotations

import subprocess
import sys

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from jg.github import GhError, gh_available, my_open_prs, review_requested_prs

console = Console()
err = Console(stderr=True)


def _check_gh(ctx: click.Context) -> None:
    if not gh_available():
        err.print("[red]✗[/] gh CLI not installed. Install with: brew install gh")
        ctx.exit(1)


def _review_decision(decision: str | None) -> Text:
    if not decision:
        return Text("⏳", style="yellow")
    if decision == "APPROVED":
        return Text("✓", style="green")
    if decision == "CHANGES_REQUESTED":
        return Text("✗", style="red")
    if decision == "REVIEW_REQUIRED":
        return Text("⏳", style="yellow")
    return Text(decision, style="dim")


@click.group(invoke_without_command=True)
@click.pass_context
def pr(ctx: click.Context) -> None:
    """GitHub pull request commands."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(_list)


@pr.command(name="list")
@click.pass_context
def _list(ctx: click.Context) -> None:
    """List my open PRs and PRs awaiting my review."""
    _check_gh(ctx)
    try:
        mine = my_open_prs()
        rr = review_requested_prs()
    except GhError as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)

    from jg.render import relative_time, truncate

    if mine:
        t = Table(title=Text("My PRs", style="bold cyan"), title_justify="left", header_style="dim")
        t.add_column("Repo", no_wrap=True)
        t.add_column("#", no_wrap=True)
        t.add_column("Title", overflow="ellipsis")
        t.add_column("State", no_wrap=True)
        t.add_column("Review", width=2, no_wrap=True)
        t.add_column("Updated", style="dim", no_wrap=True)
        for p in mine:
            repo = p.get("repository", {}).get("nameWithOwner", "?")
            state = "draft" if p.get("isDraft") else "ready"
            state_t = Text(state, style="yellow" if state == "draft" else "green")
            t.add_row(
                repo,
                str(p["number"]),
                truncate(p["title"], 60),
                state_t,
                _review_decision(p.get("reviewDecision")),
                relative_time(p.get("updatedAt")),
            )
        console.print(t)
        console.print()
    else:
        console.print("[dim]My PRs: (none)[/]\n")

    if rr:
        t = Table(title=Text("Waiting on me", style="bold magenta"), title_justify="left", header_style="dim")
        t.add_column("Repo", no_wrap=True)
        t.add_column("#", no_wrap=True)
        t.add_column("Title", overflow="ellipsis")
        t.add_column("Author", no_wrap=True)
        t.add_column("Updated", style="dim", no_wrap=True)
        for p in rr:
            repo = p.get("repository", {}).get("nameWithOwner", "?")
            author = p.get("author", {}).get("login", "?")
            t.add_row(
                repo,
                str(p["number"]),
                truncate(p["title"], 60),
                author,
                relative_time(p.get("updatedAt")),
            )
        console.print(t)
    else:
        console.print("[dim]Waiting on me: (none)[/]")


@pr.command()
@click.argument("repo")
@click.argument("number", type=int)
@click.pass_context
def view(ctx: click.Context, repo: str, number: int) -> None:
    """View a PR (wraps `gh pr view`)."""
    _check_gh(ctx)
    subprocess.run(["gh", "pr", "view", str(number), "--repo", repo])


@pr.command()
@click.argument("repo")
@click.argument("number", type=int)
@click.option("--approve", is_flag=True)
@click.option("--request-changes", is_flag=True)
@click.option("--comment", "comment_text", default=None)
@click.pass_context
def review(ctx: click.Context, repo: str, number: int, approve: bool, request_changes: bool, comment_text: str | None) -> None:
    """Submit a PR review."""
    _check_gh(ctx)
    args = ["gh", "pr", "review", str(number), "--repo", repo]
    if approve:
        args.append("--approve")
    elif request_changes:
        args.append("--request-changes")
    elif comment_text is not None:
        args.extend(["--comment", "--body", comment_text])
    else:
        err.print("[red]✗[/] Pass --approve, --request-changes, or --comment.")
        ctx.exit(1)
    subprocess.run(args)
