"""Rendering helpers — Rich-formatted tables, status badges, time formatting.

Used by all non-TUI commands. The Textual TUI has its own rendering layer
in tui.py but reuses the small helpers here (status_color, relative_time, etc).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from rich.table import Table
from rich.text import Text

# Map issue type names to one-letter abbreviation + style
TYPE_ABBR = {
    "Bug": ("B", "red"),
    "Story": ("S", "green"),
    "Task": ("T", "blue"),
    "Epic": ("E", "magenta"),
    "Sub-task": ("s", "blue"),
    "Subtask": ("s", "blue"),
}

# Map full priority names to short codes
PRIORITY_ABBR = {
    "Highest": ("P0", "red"),
    "High": ("P1", "orange3"),
    "Medium": ("P2", "yellow"),
    "Low": ("P3", "cyan"),
    "Lowest": ("P4", "dim"),
}

# Status name → group name (canonical column). Custom workflow aliases mapped here.
STATUS_GROUP_ALIASES = {
    "READY FOR REVIEW": "In Review",
    "READY FOR PRODUCTION": "Ready for Production",
    "READY FOR PROD": "Ready for Production",
    "IN REVIEW": "In Review",
    "IN PROGRESS": "In Progress",
    "IN TESTING": "In Testing",
    "BUILDING": "Building",
    "TO DO": "To Do",
    "DONE": "Done",
    "BLOCKED": "Blocked",
}

GROUP_ORDER = [
    "To Do",
    "In Progress",
    "In Review",
    "Building",
    "In Testing",
    "Ready for Production",
    "Blocked",
    "Done",
]

GROUP_STYLE = {
    "To Do": "dim",
    "In Progress": "yellow",
    "In Review": "cyan",
    "Building": "orange3",
    "In Testing": "magenta",
    "Ready for Production": "blue",
    "Blocked": "red",
    "Done": "green",
}


def normalize_status(name: str) -> str:
    return STATUS_GROUP_ALIASES.get(name.upper(), name)


def type_badge(name: str) -> Text:
    abbr, color = TYPE_ABBR.get(name, (name[:1].upper() if name else "?", "white"))
    return Text(abbr, style=f"bold {color}")


def priority_badge(name: str) -> Text:
    abbr, color = PRIORITY_ABBR.get(name, (name, "white"))
    return Text(abbr, style=color)


def status_badge(name: str) -> Text:
    group = normalize_status(name)
    color = GROUP_STYLE.get(group, "white")
    return Text(name, style=color)


def truncate(text: str, width: int = 60) -> str:
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def relative_time(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        # Jira returns timestamps like 2026-04-24T14:11:35.972-0400
        # Python's fromisoformat handles this in 3.11+.
        ts = dt.datetime.fromisoformat(iso)
    except ValueError:
        return iso
    now = dt.datetime.now(ts.tzinfo) if ts.tzinfo else dt.datetime.now()
    delta = now - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    days = secs // 86400
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def issue_row(issue: dict[str, Any]) -> tuple[Text, Text, str, Text, str]:
    fields = issue.get("fields", {})
    key = Text(issue["key"], style="bold")
    type_name = (fields.get("issuetype") or {}).get("name", "")
    summary = truncate(fields.get("summary", ""), 60)
    pri_name = (fields.get("priority") or {}).get("name", "—")
    updated = relative_time(fields.get("updated"))
    return key, type_badge(type_name), summary, priority_badge(pri_name), updated


def group_issues(issues: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group issues by canonical status column."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        status = (issue["fields"].get("status") or {}).get("name", "Unknown")
        group = normalize_status(status)
        groups.setdefault(group, []).append(issue)
    return groups


def render_sprint_tables(issues: list[dict[str, Any]]) -> list[Table]:
    groups = group_issues(issues)
    tables: list[Table] = []
    seen: set[str] = set()
    for group_name in GROUP_ORDER:
        items = groups.get(group_name)
        if not items:
            continue
        seen.add(group_name)
        tables.append(_table_for_group(group_name, items))
    # Any non-canonical groups go last.
    for group_name, items in groups.items():
        if group_name in seen:
            continue
        tables.append(_table_for_group(group_name, items))
    return tables


def _table_for_group(group_name: str, items: list[dict[str, Any]]) -> Table:
    style = GROUP_STYLE.get(group_name, "white")
    title = Text(f"{group_name} ({len(items)})", style=f"bold {style}")
    table = Table(title=title, title_justify="left", show_header=True, header_style="dim", expand=False, pad_edge=False)
    table.add_column("Key", no_wrap=True)
    table.add_column("T", width=1, no_wrap=True)
    table.add_column("Summary", overflow="ellipsis")
    table.add_column("Pri", width=3, no_wrap=True)
    table.add_column("Updated", style="dim", no_wrap=True)
    for issue in items:
        table.add_row(*issue_row(issue))
    return table
