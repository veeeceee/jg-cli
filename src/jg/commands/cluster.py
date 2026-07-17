"""`jg cluster` — group my loose work (Zoho tickets, PRs) under Jira tickets.

Deterministic backbone (authored links) + an LLM pass over the residual. LLM
groupings always show their reason + confidence and are marked `~` (soft),
never as hard links. See docs/work-model.md → Cross-source clustering.
"""

from __future__ import annotations

import asyncio

import click
from rich.console import Console
from rich.text import Text

from jg import cluster as cl
from jg.config import Config

console = Console()


async def _gather(config: Config) -> tuple[list[cl.Item], list[cl.Anchor]]:
    """Map jg's existing sources into cluster Items + Anchors.

    Anchors = my open Jira tickets. Loose items = my open PRs (branch key as an
    authored link) + Zoho tickets I'm involved in (their linked Jira keys)."""
    from jg import github
    from jg import reconcile as rec
    from jg.api import JiraClient

    anchors: list[cl.Anchor] = []
    try:
        async with JiraClient(config) as api:
            data = await api.search_jql(
                "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC",
                fields=["summary"],
                max_results=100,
            )
        for iss in data.get("issues", []):
            anchors.append(cl.Anchor(iss.get("key", ""), (iss.get("fields") or {}).get("summary", "") or ""))
    except Exception as e:
        console.print(f"[yellow]jira anchors unavailable: {type(e).__name__}[/]")

    items: list[cl.Item] = []
    try:
        for pr in await asyncio.to_thread(github.my_open_prs):
            repo = (pr.get("repository") or {}).get("nameWithOwner", "?")
            num = pr.get("number")
            branch = pr.get("headRefName", "") or ""
            key = rec.extract_key(branch) or rec.extract_key(pr.get("title", "") or "")
            items.append(
                cl.Item(
                    id=f"pr:{repo}#{num}",
                    kind="pr",
                    label=f"{repo}#{num}",
                    detail=pr.get("title", "") or "",
                    linked_keys=[key] if key else [],
                )
            )
    except Exception as e:
        console.print(f"[yellow]github PRs unavailable: {type(e).__name__}[/]")

    if config.zoho.org_id and config.zoho.agent_emails:
        try:
            from jg import zoho

            async with zoho.ZohoClient(config) as zc:
                involved = await zoho.find_involved(zc, config.zoho)
            for t in involved:
                items.append(
                    cl.Item(
                        id=f"zoho:{t.id}",
                        kind="zoho",
                        label=f"#{t.ticket_number} {t.subject}"[:60],
                        detail=t.subject,
                        linked_keys=list(t.jira_keys),
                    )
                )
        except Exception as e:
            console.print(f"[yellow]zoho unavailable: {type(e).__name__}[/]")

    return items, anchors


def _run(config: Config, no_cache: bool) -> None:
    async def go() -> cl.ClusterResult:
        items, anchors = await _gather(config)
        if not items:
            console.print("[dim]no loose items to cluster.[/]")
            return cl.ClusterResult([], [], [])
        console.print(f"[dim]{len(items)} items · {len(anchors)} anchors · running claude…[/]")
        return await cl.enrich(
            items, anchors,
            claude_path=config.ai.claude_path,
            use_cache=not no_cache,
        )

    result = asyncio.run(go())

    for c in result.clusters:
        head = Text(f"\n▸ {c.anchor_key}  ", style="bold #ff5bc0")
        head.append(c.summary, style="#c0caf5")
        console.print(head)
        for m in c.members:
            soft = m.edge.kind == cl.EdgeKind.LLM
            marker = "~" if soft else "="
            color = "#e0af68" if soft else "#9ece6a"
            line = Text(f"  {marker} ", style=color)
            line.append(f"{m.label:<44}", style="#a9b1d6")
            line.append(f"  {m.edge.reason}", style="#565f89")
            if soft:
                line.append(f" ({m.edge.confidence:.1f})", style="#565f89")
            console.print(line)

    if result.residual:
        console.print(Text(f"\n○ unclustered ({len(result.residual)})", style="bold #565f89"))
        for it in result.residual:
            console.print(Text(f"  · {it.label}", style="#565f89"))

    console.print(
        Text(
            "\n=  authored link (deterministic)    ~  grouped by topic (LLM, soft)",
            style="dim",
        )
    )


@click.command()
@click.option("--no-cache", is_flag=True, help="Skip the cluster cache; re-run the LLM.")
@click.pass_context
def cluster(ctx: click.Context, no_cache: bool) -> None:
    """Group loose work (PRs, Zoho tickets) under their Jira ticket."""
    _run(ctx.obj["config"], no_cache)
