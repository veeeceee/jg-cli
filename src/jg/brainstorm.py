"""Build a context-rich prompt for `claude brainstorm` sessions.

When ideating new tickets, claude does much better with:
- The project's recent tickets (so naming/scoping/style is consistent)
- The components, issue types, labels actually used in the project
- The repos involved (so generated tickets reference the right code)
- The primary local path (so claude can grep the codebase if useful)

The output is a single prompt string passed as the first message to claude.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from jg.api import ApiError, JiraClient
from jg.config import Config, Project


async def _recent_tickets(api: JiraClient, project: Project | None) -> list[dict[str, Any]]:
    base = "assignee = currentUser()" if not project or not project.jql else f"({project.jql})"
    jql = f"{base} ORDER BY created DESC"
    try:
        data = await api.search_jql(
            jql,
            fields=["summary", "status", "issuetype", "priority", "labels", "components"],
            max_results=30,
        )
    except ApiError:
        return []
    return data.get("issues", []) or []


def _summarize_facets(issues: list[dict[str, Any]]) -> dict[str, list[tuple[str, int]]]:
    """Top types/components/labels seen in the project."""
    types: Counter[str] = Counter()
    components: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    for issue in issues:
        f = issue.get("fields", {})
        if t := (f.get("issuetype") or {}).get("name"):
            types[t] += 1
        for c in f.get("components") or []:
            if name := c.get("name"):
                components[name] += 1
        for l in f.get("labels") or []:
            labels[l] += 1
    return {
        "types": types.most_common(5),
        "components": components.most_common(5),
        "labels": labels.most_common(8),
    }


async def build_brainstorm_prompt(config: Config, project: Project | None) -> str:
    """Compose a multi-line prompt with project context."""
    lines: list[str] = []
    name = project.name if project else "(any)"
    lines.append(f"# Brainstorm session for {name}")
    lines.append("")
    lines.append("Help me ideate new tickets. Ask what's on my mind, then shape ideas")
    lines.append("into well-scoped tickets — one summary line plus a draft description,")
    lines.append("acceptance criteria, and a guess at issue type (Bug/Task/Story/Epic).")
    lines.append("Match the style of the recent tickets shown below. Keep momentum;")
    lines.append("we'll polish later. Use /create when I confirm a draft.")
    lines.append("")

    if project:
        lines.append("## Project context")
        if project.jql:
            lines.append(f"- Filter: `{project.jql}`")
        if project.repos:
            lines.append(f"- Repos: {', '.join(project.repos)}")
        if project.local_path:
            lines.append(f"- Primary local path: {project.local_path}")
        lines.append("")

    # Pull recent context from Jira
    try:
        async with JiraClient(config) as api:
            issues = await _recent_tickets(api, project)
    except Exception as e:
        lines.append(f"_(could not fetch project context: {e})_")
        return "\n".join(lines)

    if not issues:
        lines.append("_(no recent tickets found)_")
        return "\n".join(lines)

    lines.append(f"## Recent {len(issues)} tickets (most recent first)")
    for it in issues:
        f = it.get("fields", {})
        key = it.get("key", "?")
        summary = f.get("summary", "")
        type_name = (f.get("issuetype") or {}).get("name", "?")
        status = (f.get("status") or {}).get("name", "?")
        priority = (f.get("priority") or {}).get("name", "?")
        lines.append(f"- **{key}** [{type_name}/{priority}/{status}] {summary}")
    lines.append("")

    facets = _summarize_facets(issues)
    if facets["types"]:
        lines.append("## Types in use")
        for n, c in facets["types"]:
            lines.append(f"- {n} ({c})")
        lines.append("")
    if facets["components"]:
        lines.append("## Components in use")
        for n, c in facets["components"]:
            lines.append(f"- {n} ({c})")
        lines.append("")
    if facets["labels"]:
        lines.append("## Labels in use")
        for n, c in facets["labels"]:
            lines.append(f"- {n} ({c})")
        lines.append("")

    lines.append("---")
    lines.append("What's on your mind?")
    return "\n".join(lines)


def build_brainstorm_prompt_sync(config: Config, project: Project | None) -> str:
    """Sync wrapper for CLI invocations."""
    return asyncio.run(build_brainstorm_prompt(config, project))
