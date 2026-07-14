"""jg reading/updating ~/.ai/progress.json (redirected to a temp file)."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from jg import progress


@pytest.fixture
def progress_file(tmp_path, monkeypatch):
    p = tmp_path / "progress.json"
    monkeypatch.setattr(progress, "PROGRESS_PATH", p)
    return p


def test_read_level_missing_file_defaults_zero(progress_file):
    assert not progress_file.exists()
    assert progress.read_level("anything") == 0


def test_read_level_reads_value(progress_file):
    progress_file.write_text(json.dumps({"patterns": {"epic-decomposition": {"level": 2}}}))
    assert progress.read_level("epic-decomposition") == 2
    assert progress.read_level("unknown") == 0


def test_record_use_increments_and_preserves(progress_file):
    progress_file.write_text(json.dumps({
        "patterns": {"epic-decomposition": {"level": 1, "uses": 4, "correct": 3, "lastUsed": "2026-01-01"}},
        "weakAreas": ["x"],
    }))
    progress.record_use("epic-decomposition")
    data = json.loads(progress_file.read_text())
    entry = data["patterns"]["epic-decomposition"]
    assert entry["uses"] == 5
    assert entry["lastUsed"] == dt.date.today().isoformat()
    assert entry["level"] == 1 and entry["correct"] == 3  # jg never touches these
    assert data["weakAreas"] == ["x"]  # rest of the file preserved


def test_record_use_creates_entry_for_new_pattern(progress_file):
    progress_file.write_text(json.dumps({"patterns": {}}))
    progress.record_use("epic-decomposition")
    entry = json.loads(progress_file.read_text())["patterns"]["epic-decomposition"]
    assert entry["uses"] == 1 and entry["level"] == 0


def test_record_use_never_creates_file(progress_file):
    # jg must not author the framework's canonical file from scratch.
    assert not progress_file.exists()
    progress.record_use("epic-decomposition")
    assert not progress_file.exists()