"""ch view <KEY> — render a ticket compactly."""

from __future__ import annotations

import json

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from ch.adf import render_to_text
from ch.api import ApiError, JiraClient
from ch.auth import AuthError
from ch.cli import async_command
from ch.config import Config
from ch.github import GhError, gh_available, prs_mentioning
from ch.render import priority_badge, relative_time, status_badge, truncate, type_badge

console = Console()
err = Console(stderr=True)


@click.command()
@click.argument("key")
@click.option("--json", "as_json", is_flag=True, help="Raw JSON output")
@click.option("--comments/--no-comments", default=True, help="Show recent comments")
@click.option("--prs/--no-prs", default=True, help="Show GitHub PRs mentioning this key")
@click.pass_context
@async_command
async def view(ctx: click.Context, key: str, as_json: bool, comments: bool, prs: bool) -> None:
    """View a Jira ticket."""
    config: Config = ctx.obj["config"]
    fields = [
        "summary", "status", "priority", "issuetype", "labels", "components",
        "fixVersions", "assignee", "reporter", "issuelinks", "comment",
        "description", "customfield_10186",
    ]
    try:
        async with JiraClient(config) as api:
            issue = await api.get_issue(key, fields=fields)
    except (AuthError, ApiError) as e:
        err.print(f"[red]✗[/] {e}")
        ctx.exit(1)

    if as_json:
        click.echo(json.dumps(issue, indent=2))
        return

    f = issue.get("fields", {})
    type_name = (f.get("issuetype") or {}).get("name", "")
    pri_name = (f.get("priority") or {}).get("name", "—")
    status_name = (f.get("status") or {}).get("name", "—")
    assignee = (f.get("assignee") or {}).get("displayName", "—")
    labels = ", ".join(f.get("labels", [])) or "—"
    components = ", ".join(c["name"] for c in f.get("components", []) or []) or "—"
    fix_versions = ", ".join(v["name"] for v in f.get("fixVersions", []) or []) or "—"
    base = config.default_cloud_url.rstrip("/") if config.default_cloud_url else ""
    url = f"{base}/browse/{key}" if base else f"(set default_cloud_url) {key}"

    header = Text()
    header.append(f"{key}", style="bold")
    header.append(" — ")
    header.append(f.get("summary", ""))
    console.print(header)

    meta = Text()
    meta.append_text(status_badge(status_name))
    meta.append("  ·  ")
    meta.append_text(type_badge(type_name))
    meta.append(f" {type_name}")
    meta.append("  ·  ")
    meta.append_text(priority_badge(pri_name))
    meta.append(f"  ·  @{assignee}")
    console.print(meta)

    facets = Text()
    facets.append(f"labels: {labels}", style="dim")
    facets.append("   ", style="dim")
    facets.append(f"components: {components}", style="dim")
    facets.append("   ", style="dim")
    facets.append(f"fix: {fix_versions}", style="dim")
    console.print(facets)
    console.print()

    desc = render_to_text(f.get("description"))
    if desc:
        console.print(Panel(Markdown(desc), title="Description", title_align="left", border_style="dim", expand=True))

    test_cases = render_to_text(f.get("customfield_10186"))
    if test_cases:
        console.print(Panel(Markdown(test_cases), title="Test cases", title_align="left", border_style="dim", expand=True))

    links = f.get("issuelinks") or []
    if links:
        console.print("[bold]Links[/]")
        for link in links[:10]:
            link_type = link.get("type", {})
            if "outwardIssue" in link:
                rel = link_type.get("outward", "relates to")
                target = link["outwardIssue"]
            elif "inwardIssue" in link:
                rel = link_type.get("inward", "is related to")
                target = link["inwardIssue"]
            else:
                continue
            target_summary = (target.get("fields", {}).get("summary") or "")
            console.print(f"  • {rel} [bold]{target['key']}[/] — {truncate(target_summary, 60)}")
        console.print()

    if prs and gh_available():
        try:
            linked_prs = prs_mentioning(key, limit=10)
        except GhError:
            linked_prs = []
        if linked_prs:
            console.print("[bold]Linked PRs[/]")
            for p in linked_prs:
                repo = p.get("repository", {}).get("nameWithOwner", "?")
                num = p["number"]
                title = truncate(p.get("title", ""), 60)
                state = p.get("state", "?").lower()
                draft = " (draft)" if p.get("isDraft") else ""
                console.print(f"  • [cyan]{repo}#{num}[/] {title} [dim]· {state}{draft}[/]")
            console.print()

    if comments:
        all_comments = (f.get("comment") or {}).get("comments") or []
        total = (f.get("comment") or {}).get("total", len(all_comments))
        if all_comments:
            console.print(f"[bold]Comments ({total})[/]")
            for c in all_comments[-5:]:
                author = c.get("author", {}).get("displayName", "?")
                when = relative_time(c.get("created"))
                body = render_to_text(c.get("body"))
                console.print(f"  [bold]{author}[/] · [dim]{when}[/]")
                console.print(f"    {truncate(body, 280)}")
            if total > 5:
                console.print(f"  [dim]({total - 5} earlier comments)[/]")
            console.print()

    console.print(f"[dim]→ {url}[/]")
