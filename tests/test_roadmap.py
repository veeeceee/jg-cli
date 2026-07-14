"""Roadmap aggregation: epic JQL resolution, child tally (progress/done/blocked),
sort order, and the progress bar."""

from __future__ import annotations

import pytest

from jg import roadmap
from jg.config import Config
from jg.roadmap import Epic


def test_effective_jql():
    assert roadmap.effective_jql(Config()) == "issuetype = Epic"
    c = Config()
    c.default_project = "CH"
    assert roadmap.effective_jql(c) == "project = CH AND issuetype = Epic"
    c.roadmap.jql = "labels = roadmap"
    assert roadmap.effective_jql(c) == "labels = roadmap"


def test_progress_bar():
    assert roadmap.progress_bar(0, width=10) == "░" * 10
    assert roadmap.progress_bar(100, width=10) == "━" * 10
    assert roadmap.progress_bar(50, width=10) == "━" * 5 + "░" * 5
    # clamps out-of-range
    assert roadmap.progress_bar(150, width=4) == "━" * 4


def test_epic_pct():
    assert Epic("K", "s", "st", "To Do", total=0, done=0).pct == 0
    assert Epic("K", "s", "st", "To Do", total=8, done=2).pct == 25


class _FakeApi:
    """Stands in for JiraClient: returns canned epic + child responses."""

    def __init__(self, epics, children):
        self._epics = epics
        self._children = children

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def search_jql(self, jql, fields=None, max_results=100, next_page_token=None):
        if "parent in" in jql:
            return {"issues": self._children, "nextPageToken": None}
        return {"issues": self._epics}


def _epic(key, name, status, cat):
    return {"key": key, "fields": {"summary": name, "status": {"name": status, "statusCategory": {"key": cat}}}}


def _child(parent, status, cat):
    return {"fields": {"parent": {"key": parent}, "status": {"name": status, "statusCategory": {"key": cat}}}}


@pytest.mark.asyncio
async def test_fetch_roadmap_tally_and_sort(monkeypatch):
    epics = [
        _epic("CH-1", "Alpha", "In Progress", "indeterminate"),
        _epic("CH-2", "Beta", "Done", "done"),
        _epic("CH-3", "Gamma", "To Do", "new"),
    ]
    children = [
        _child("CH-1", "Done", "done"),
        _child("CH-1", "To Do", "new"),
        _child("CH-1", "Blocked", "indeterminate"),
        _child("CH-2", "Done", "done"),
        _child("CH-2", "Done", "done"),
        _child("CH-3", "In Progress", "indeterminate"),
    ]
    monkeypatch.setattr(roadmap, "JiraClient", lambda config: _FakeApi(epics, children))

    result = await roadmap.fetch_roadmap(Config())
    by_key = {e.key: e for e in result}

    assert by_key["CH-1"].total == 3
    assert by_key["CH-1"].done == 1  # only the statusCategory=done child
    assert by_key["CH-1"].blocked == 1
    assert by_key["CH-2"].is_done_status is True
    assert by_key["CH-2"].pct == 100

    # Sort: In Progress first, To Do next, done-category epic last.
    assert [e.key for e in result] == ["CH-1", "CH-3", "CH-2"]


@pytest.mark.asyncio
async def test_fetch_roadmap_empty(monkeypatch):
    monkeypatch.setattr(roadmap, "JiraClient", lambda config: _FakeApi([], []))
    assert await roadmap.fetch_roadmap(Config()) == []
