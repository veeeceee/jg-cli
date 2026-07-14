"""Config restructure + back-compat: legacy flat files still load, the new
[atlassian] / [projects.docs] shape round-trips, and delegating properties keep
old callers working."""

from __future__ import annotations

import tomllib

import pytest

from jg.config import Config, Project, ProjectDocs


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    """Redirect config storage to a temp file for save/load round-trips."""
    d = tmp_path / "jg"
    p = d / "config.toml"
    monkeypatch.setattr("jg.config.CONFIG_DIR", d)
    monkeypatch.setattr("jg.config.CONFIG_PATH", p)
    return p


LEGACY = """\
client_id = "old123"
default_cloud_id = "cid"
default_cloud_url = "https://old.atlassian.net"
default_project = "OLD"

[ui]
theme = "jg-night"

[[projects]]
name = "legacy-proj"
jql = "project = OLD"
repos = ["o/legacy"]
local_path = "~/legacy"

[projects.repo_paths]
"o/legacy" = "~/legacy"
"""


def test_legacy_flat_file_loads(config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text(LEGACY)
    c = Config.load()
    # Flat top-level keys migrated into [atlassian].
    assert c.atlassian.client_id == "old123"
    assert c.atlassian.cloud_id == "cid"
    assert c.atlassian.cloud_url == "https://old.atlassian.net"
    assert c.atlassian.default_jira_project == "OLD"
    assert c.ui.theme == "jg-night"
    # Project without docs pointers loads with empty ProjectDocs.
    assert len(c.projects) == 1
    p = c.projects[0]
    assert p.name == "legacy-proj"
    assert p.repo_paths == {"o/legacy": "~/legacy"}
    assert p.docs.is_empty()
    assert p.research_dir == ""


def test_new_format_roundtrips(config_path):
    c = Config()
    c.client_id = "abc"
    c.default_cloud_url = "https://x.atlassian.net"
    c.default_project = "CH"
    c.projects = [
        Project(
            name="cortex",
            jql="project = CH",
            repos=["o/r"],
            local_path="~/x",
            docs=ProjectDocs(plan="docs/plan.md", dirs=["docs"], memory=["m1"], confluence=["CHAI"]),
            research_dir="~/r",
        )
    ]
    c.save()
    reloaded = Config.load()
    assert reloaded.atlassian.client_id == "abc"
    assert reloaded.atlassian.default_jira_project == "CH"
    p = reloaded.projects[0]
    assert p.docs.plan == "docs/plan.md"
    assert p.docs.memory == ["m1"]
    assert p.docs.confluence == ["CHAI"]
    assert p.research_dir == "~/r"


def test_saved_file_has_header_and_new_sections(config_path):
    c = Config()
    c.client_id = "abc"
    c.projects = [Project(name="p", jql="x", docs=ProjectDocs(plan="p.md"))]
    c.save()
    text = config_path.read_text()
    assert text.startswith("# jg config")  # don't-hand-edit banner
    data = tomllib.loads(text)
    assert "atlassian" in data
    assert "client_id" not in data  # no more loose top-level keys
    assert data["projects"][0]["docs"]["plan"] == "p.md"


def test_empty_docs_and_research_omitted(config_path):
    c = Config()
    c.projects = [Project(name="bare", jql="x")]
    c.save()
    data = tomllib.loads(config_path.read_text())
    proj = data["projects"][0]
    assert "docs" not in proj
    assert "research_dir" not in proj


def test_delegating_properties_read_write():
    c = Config()
    c.client_id = "id1"
    c.default_cloud_id = "cloud1"
    c.default_cloud_url = "https://u"
    c.default_project = "KEY"
    # Writes land on the atlassian sub-object...
    assert c.atlassian.client_id == "id1"
    assert c.atlassian.cloud_id == "cloud1"
    assert c.atlassian.cloud_url == "https://u"
    assert c.atlassian.default_jira_project == "KEY"
    # ...and reads round-trip back through the property.
    c.atlassian.client_id = "id2"
    assert c.client_id == "id2"
    assert c.is_setup


def test_missing_file_returns_defaults(config_path):
    assert not config_path.exists()
    c = Config.load()
    assert c.atlassian.client_id == ""
    assert c.projects == []
    assert not c.is_setup


def test_project_by_name_case_insensitive():
    c = Config(projects=[Project(name="Charm Cortex")])
    assert c.project_by_name("charm cortex") is not None
    assert c.project_by_name("nope") is None


def test_roadmap_config_roundtrips(config_path):
    c = Config()
    c.roadmap.jql = "labels = roadmap"
    c.save()
    assert Config.load().roadmap.jql == "labels = roadmap"


def test_zoho_config_roundtrips(config_path):
    c = Config()
    c.zoho.client_id = "zc"
    c.zoho.org_id = "5212176"
    c.zoho.department_ids = ["3154000000006907"]
    c.zoho.agent_emails = ["vibhu@charmhealthtech.com", "vibhu.c@medicalmine.com"]
    c.save()
    z = Config.load().zoho
    assert z.client_id == "zc"
    assert z.org_id == "5212176"
    assert z.department_ids == ["3154000000006907"]
    assert z.agent_emails == ["vibhu@charmhealthtech.com", "vibhu.c@medicalmine.com"]
    assert z.is_setup
