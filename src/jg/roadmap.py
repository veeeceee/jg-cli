"""Portfolio/roadmap altitude: epics as initiatives, each with child progress.

The dashboard's kanban is execution-altitude (tickets within one epic). This is
the layer above: every epic from a configurable JQL, with a progress bar, status,
and a blocked flag — "what's next across the whole effort."

Data source is the epic/parent structure (fix-versions are unused in this Jira).
One query lists the epics; one paginated `parent in (...)` query tallies all
their children client-side — no N+1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jg.api import JiraClient
from jg.config import Config
from jg.render import normalize_status


@dataclass
class Epic:
    key: str
    summary: str
    status: str
    status_group: str
    counts: dict[str, int] = field(default_factory=dict)  # normalized child status -> count
    total: int = 0
    done: int = 0       # by statusCategory (folds Done/Resolved/Closed)
    blocked: int = 0
    is_done_status: bool = False  # epic's own status is in the "done" category

    @property
    def pct(self) -> int:
        return round(100 * self.done / self.total) if self.total else 0


def effective_jql(config: Config) -> str:
    """Configured roadmap JQL, or derived from the default Jira project."""
    if config.roadmap.jql.strip():
        return config.roadmap.jql.strip()
    key = config.default_project
    return f"project = {key} AND issuetype = Epic" if key else "issuetype = Epic"


def _sort_key(e: Epic) -> tuple[int, int]:
    """In-progress epics first, then to-do, then done; within each, most complete
    (but not finished) first so active work surfaces at the top."""
    if e.is_done_status:
        rank = 2
    elif e.status_group in ("In Progress", "In Review", "Building", "In Testing"):
        rank = 0
    else:
        rank = 1
    return (rank, -e.pct)


async def fetch_roadmap(config: Config, jql: str | None = None) -> list[Epic]:
    """Epics (from `jql`) with child progress tallied. Empty on no epics."""
    query = jql or effective_jql(config)
    async with JiraClient(config) as api:
        edata = await api.search_jql(
            f"{query} ORDER BY status ASC, key ASC",
            fields=["summary", "status"],
            max_results=100,
        )
        epics: dict[str, Epic] = {}
        order: list[str] = []
        for e in edata.get("issues", []):
            f = e.get("fields", {})
            status_obj = f.get("status") or {}
            status = status_obj.get("name", "—")
            epics[e["key"]] = Epic(
                key=e["key"],
                summary=f.get("summary", ""),
                status=status,
                status_group=normalize_status(status),
                is_done_status=(status_obj.get("statusCategory") or {}).get("key") == "done",
            )
            order.append(e["key"])
        if not order:
            return []

        child_jql = f"parent in ({', '.join(order)})"
        token: str | None = None
        while True:
            cdata = await api.search_jql(
                child_jql, fields=["status", "parent"], max_results=100, next_page_token=token
            )
            issues = cdata.get("issues", [])
            for c in issues:
                cf = c.get("fields", {})
                pk = (cf.get("parent") or {}).get("key")
                ep = epics.get(pk)
                if ep is None:
                    continue
                status_obj = cf.get("status") or {}
                grp = normalize_status(status_obj.get("name", ""))
                category = (status_obj.get("statusCategory") or {}).get("key", "")
                ep.counts[grp] = ep.counts.get(grp, 0) + 1
                ep.total += 1
                if category == "done":
                    ep.done += 1
                if grp == "Blocked":
                    ep.blocked += 1
            token = cdata.get("nextPageToken")
            if not token or not issues:
                break

    return sorted(epics.values(), key=_sort_key)


def progress_bar(pct: int, width: int = 14) -> str:
    """Text progress bar, e.g. '━━━━━━━━░░░░░░'."""
    pct = max(0, min(100, pct))
    filled = round(width * pct / 100)
    return "━" * filled + "░" * (width - filled)
