"""Read-only surface over a project's canonical artifacts.

jg surfaces plan / research / docs / memory pointers; it never owns or edits
them. Everything here tolerates missing files/dirs and never raises into the UI
— a project that hasn't wired up pointers just yields empty results.

Live work data (ticket counts, PRs) is deliberately NOT here — the dashboard
already holds it. This module is pure local-filesystem reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jg.config import Config, Project

# Central default for research when a project sets no `research_dir` override.
RESEARCH_ROOT = Path.home() / "DeveloperLocal" / "research"
# Claude Code's per-project memory dirs: ~/.claude/projects/<encoded-cwd>/memory/
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

_MAX_DOC_FILES = 200  # cap the per-dir md count so a huge docs/ tree can't stall the UI


def slug(name: str) -> str:
    """Project name → filesystem-safe slug (matches research dir naming)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def project_base(project: Project) -> Path | None:
    """Base dir that relative plan/doc paths resolve against: primary local_path,
    else the first repo's resolved local path, else None."""
    if project.local_path:
        return Path(project.local_path).expanduser()
    for repo in project.repos:
        rp = project.resolve_repo_path(repo)
        if rp:
            return Path(rp).expanduser()
    return None


def _resolve(base: Path | None, p: str) -> Path:
    """Expand `p`; if relative, anchor it to `base` when we have one."""
    pp = Path(p).expanduser()
    if pp.is_absolute() or base is None:
        return pp
    return base / pp


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _strip_frontmatter(lines: list[str]) -> list[str]:
    """Drop a leading YAML frontmatter block (--- ... ---) if present."""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[i + 1 :]
    return lines


def _title_and_excerpt(text: str, excerpt_lines: int = 4) -> tuple[str, str]:
    """First markdown heading (or first content line) as title; the next few
    non-blank content lines as an excerpt."""
    lines = _strip_frontmatter(text.splitlines())
    title = ""
    body: list[str] = []
    for line in lines:
        s = line.strip()
        if not title:
            if s.startswith("#"):
                title = s.lstrip("#").strip()
                continue
            if s:
                title = s
                continue
        elif s and not s.startswith("#"):
            body.append(s)
            if len(body) >= excerpt_lines:
                break
    return title, " ".join(body)


# ── Research ──────────────────────────────────────────────────────────────
def research_path(project: Project) -> Path:
    """Override if set, else central default ~/DeveloperLocal/research/<slug>/."""
    if project.research_dir:
        return Path(project.research_dir).expanduser()
    return RESEARCH_ROOT / slug(project.name)


def new_research_file(project: Project, topic: str) -> Path:
    """Scaffold a dated, frontmatter'd research file in the project's research
    dir and return its path. Idempotent: if the file for today+topic already
    exists it's returned untouched (so re-running never clobbers findings).

    Frontmatter is jg-authored (title/date/project) so `list_research` and the
    UI render consistently no matter who fills in the body."""
    import datetime as _dt

    d = research_path(project)
    d.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    topic = topic.strip()
    stem = slug(topic) or "notes"
    path = d / f"{today}-{stem}.md"
    if not path.exists():
        title = topic or "Untitled research"
        path.write_text(
            f"---\ntitle: {title}\ndate: {today}\nproject: {project.name}\n---\n\n# {title}\n\n",
            encoding="utf-8",
        )
    return path


@dataclass
class ResearchDoc:
    path: Path
    mtime: float
    title: str


def list_research(project: Project, limit: int | None = None) -> list[ResearchDoc]:
    """Newest-first research markdown for a project. Empty if the dir is absent."""
    d = research_path(project)
    if not d.is_dir():
        return []
    docs: list[ResearchDoc] = []
    try:
        for f in d.glob("*.md"):
            if not f.is_file():
                continue
            text = _read_text(f)
            title = _title_and_excerpt(text)[0] if text else ""
            try:
                mtime = f.stat().st_mtime
            except OSError:
                mtime = 0.0
            docs.append(ResearchDoc(f, mtime, title or f.stem))
    except OSError:
        return []
    docs.sort(key=lambda r: r.mtime, reverse=True)
    return docs[:limit] if limit else docs


# ── Plan ────────────────────────────────────────────────────────────────────
@dataclass
class PlanSummary:
    path: Path
    exists: bool
    title: str
    excerpt: str


def plan_summary(project: Project) -> PlanSummary | None:
    """None if no plan pointer configured; otherwise a summary (exists=False if
    the pointer is dangling)."""
    if not project.docs.plan:
        return None
    path = _resolve(project_base(project), project.docs.plan)
    text = _read_text(path) if path.is_file() else None
    if text is None:
        return PlanSummary(path=path, exists=False, title=project.docs.plan, excerpt="")
    title, excerpt = _title_and_excerpt(text)
    return PlanSummary(path=path, exists=True, title=title or path.name, excerpt=excerpt)


# ── Docs & memory ────────────────────────────────────────────────────────────
@dataclass
class DocLink:
    label: str
    path: Path
    kind: str  # "docdir" | "docfile" | "memory"
    exists: bool
    count: int = 0  # docdir: number of .md files


def _resolve_memory(slug_name: str) -> Path | None:
    """Find a memory file by slug across all Claude Code project memory dirs."""
    if not CLAUDE_PROJECTS.is_dir():
        return None
    fname = slug_name if slug_name.endswith(".md") else f"{slug_name}.md"
    try:
        matches = sorted(CLAUDE_PROJECTS.glob(f"*/memory/{fname}"))
    except OSError:
        return None
    return matches[0] if matches else None


def doc_links(project: Project) -> list[DocLink]:
    """Configured doc dirs (as dir entries with a file count) + memory slugs."""
    base = project_base(project)
    out: list[DocLink] = []
    for d in project.docs.dirs:
        dp = _resolve(base, d)
        if dp.is_dir():
            count = 0
            try:
                for _ in dp.rglob("*.md"):
                    count += 1
                    if count >= _MAX_DOC_FILES:
                        break
            except OSError:
                count = 0
            out.append(DocLink(label=dp.name, path=dp, kind="docdir", exists=True, count=count))
        elif dp.is_file():
            out.append(DocLink(label=dp.name, path=dp, kind="docfile", exists=True))
        else:
            out.append(DocLink(label=str(d), path=dp, kind="docdir", exists=False))
    for slug_name in project.docs.memory:
        mp = _resolve_memory(slug_name)
        out.append(DocLink(label=slug_name, path=mp or Path(slug_name), kind="memory", exists=mp is not None))
    return out


# ── Confluence (Phase 1: link-only) ──────────────────────────────────────────
@dataclass
class ConfluenceLink:
    key: str
    url: str  # empty if no cloud_url configured


def confluence_links(project: Project, config: Config) -> list[ConfluenceLink]:
    base = config.default_cloud_url.rstrip("/") if config.default_cloud_url else ""
    return [
        ConfluenceLink(key=key, url=f"{base}/wiki/spaces/{key}" if base else "")
        for key in project.docs.confluence
    ]
