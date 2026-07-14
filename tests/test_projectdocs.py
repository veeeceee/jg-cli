"""projectdocs readers: research listing/sorting, plan summary, doc/memory
resolution, confluence links — all defensive against missing paths."""

from __future__ import annotations

import os

import pytest

from jg import projectdocs as pd
from jg.config import Config, Project, ProjectDocs


def _touch(path, text, mtime=None):
    path.write_text(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_slug():
    assert pd.slug("Charm Cortex") == "charm-cortex"
    assert pd.slug("insurance-payer-mcp") == "insurance-payer-mcp"
    assert pd.slug("A/B  &  C!") == "a-b-c"


def test_research_path_default_vs_override(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "RESEARCH_ROOT", tmp_path / "research")
    default = pd.research_path(Project(name="Foo Bar"))
    assert default == tmp_path / "research" / "foo-bar"
    override = pd.research_path(Project(name="Foo", research_dir=str(tmp_path / "custom")))
    assert override == tmp_path / "custom"


def test_list_research_sorted_newest_first_with_titles(tmp_path):
    rdir = tmp_path / "r"
    rdir.mkdir()
    _touch(rdir / "old.md", "# Old finding\nbody", mtime=1000)
    _touch(rdir / "new.md", "# New finding\nbody", mtime=2000)
    _touch(rdir / "notes.txt", "ignored")  # non-md skipped
    proj = Project(name="p", research_dir=str(rdir))
    docs = pd.list_research(proj)
    assert [d.title for d in docs] == ["New finding", "Old finding"]
    assert pd.list_research(proj, limit=1)[0].title == "New finding"


def test_list_research_missing_dir_is_empty(tmp_path):
    proj = Project(name="p", research_dir=str(tmp_path / "does-not-exist"))
    assert pd.list_research(proj) == []


def test_research_title_falls_back_to_stem(tmp_path):
    rdir = tmp_path / "r"
    rdir.mkdir()
    _touch(rdir / "untitled.md", "")  # no heading
    proj = Project(name="p", research_dir=str(rdir))
    assert pd.list_research(proj)[0].title == "untitled"


def test_plan_summary_strips_frontmatter(tmp_path):
    base = tmp_path / "proj"
    (base / "docs").mkdir(parents=True)
    (base / "docs" / "plan.md").write_text(
        "---\nname: x\ndescription: y\n---\n# Strategic Plan\n\nShip by Q3.\nSecond line.\n"
    )
    proj = Project(name="p", local_path=str(base), docs=ProjectDocs(plan="docs/plan.md"))
    ps = pd.plan_summary(proj)
    assert ps is not None and ps.exists
    assert ps.title == "Strategic Plan"
    assert "Ship by Q3." in ps.excerpt


def test_plan_summary_none_when_unset():
    assert pd.plan_summary(Project(name="p")) is None


def test_plan_summary_dangling_pointer(tmp_path):
    proj = Project(name="p", local_path=str(tmp_path), docs=ProjectDocs(plan="missing.md"))
    ps = pd.plan_summary(proj)
    assert ps is not None and ps.exists is False


def test_doc_links_dir_count_and_missing(tmp_path):
    base = tmp_path / "proj"
    (base / "docs").mkdir(parents=True)
    (base / "docs" / "a.md").write_text("# a")
    (base / "docs" / "b.md").write_text("# b")
    proj = Project(name="p", local_path=str(base), docs=ProjectDocs(dirs=["docs", "ghost"]))
    links = pd.doc_links(proj)
    by_label = {d.label: d for d in links}
    assert by_label["docs"].kind == "docdir" and by_label["docs"].count == 2 and by_label["docs"].exists
    assert by_label["ghost"].exists is False


def test_doc_links_memory_resolution(tmp_path, monkeypatch):
    # Simulate Claude Code's ~/.claude/projects/<enc>/memory/ layout.
    proj_dir = tmp_path / "projects" / "-some-enc" / "memory"
    proj_dir.mkdir(parents=True)
    (proj_dir / "my_note.md").write_text("# note")
    monkeypatch.setattr(pd, "CLAUDE_PROJECTS", tmp_path / "projects")
    proj = Project(name="p", docs=ProjectDocs(memory=["my_note", "missing_note"]))
    links = {d.label: d for d in pd.doc_links(proj)}
    assert links["my_note"].kind == "memory" and links["my_note"].exists
    assert links["missing_note"].exists is False


def test_confluence_links(tmp_path):
    cfg = Config()
    cfg.default_cloud_url = "https://charmhealthtech.atlassian.net"
    proj = Project(name="p", docs=ProjectDocs(confluence=["CHAI"]))
    links = pd.confluence_links(proj, cfg)
    assert links[0].url == "https://charmhealthtech.atlassian.net/wiki/spaces/CHAI"
    # No cloud_url → key surfaced with empty url.
    assert pd.confluence_links(proj, Config())[0].url == ""


def test_project_base_prefers_local_path_then_repo(tmp_path):
    base = tmp_path / "primary"
    base.mkdir()
    assert pd.project_base(Project(name="p", local_path=str(base))) == base
    # No local_path → single-repo path via resolve_repo_path.
    repo = tmp_path / "repo"
    repo.mkdir()
    proj = Project(name="p", repos=["o/r"], repo_paths={"o/r": str(repo)})
    assert pd.project_base(proj) == repo
    assert pd.project_base(Project(name="p")) is None


@pytest.mark.parametrize("empty", [Project(name="blank")])
def test_empty_project_never_raises(empty):
    assert pd.list_research(empty) == []
    assert pd.plan_summary(empty) is None
    assert pd.doc_links(empty) == []


def test_new_research_file_scaffolds_frontmatter(tmp_path):
    import datetime as dt

    proj = Project(name="Cortex", research_dir=str(tmp_path / "r"))
    path = pd.new_research_file(proj, "Vector DB Options")
    assert path.name == f"{dt.date.today().isoformat()}-vector-db-options.md"
    text = path.read_text()
    assert "title: Vector DB Options" in text
    assert "project: Cortex" in text
    assert f"date: {dt.date.today().isoformat()}" in text
    assert "# Vector DB Options" in text
    # jg-authored frontmatter makes it resurface with a clean title.
    assert pd.list_research(proj)[0].title == "Vector DB Options"


def test_new_research_file_is_idempotent(tmp_path):
    proj = Project(name="Cortex", research_dir=str(tmp_path / "r"))
    p1 = pd.new_research_file(proj, "Topic")
    p1.write_text(p1.read_text() + "FINDINGS")
    p2 = pd.new_research_file(proj, "Topic")
    assert p2 == p1
    assert "FINDINGS" in p2.read_text()  # never clobbers existing findings


def test_new_research_file_empty_topic(tmp_path):
    proj = Project(name="Cortex", research_dir=str(tmp_path / "r"))
    assert pd.new_research_file(proj, "   ").name.endswith("-notes.md")
