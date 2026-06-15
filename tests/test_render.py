from jg.render import (
    normalize_status,
    points_value,
    priority_badge,
    relative_time,
    truncate,
    type_badge,
)


def test_points_value_select():
    # single-select option field arrives as {"value": "3"}
    assert points_value({"cf": {"value": "3"}}, "cf") == "3"


def test_points_value_number():
    # numeric field arrives as a bare float; render as a clean int
    assert points_value({"cf": 4.0}, "cf") == "4"
    assert points_value({"cf": 2.5}, "cf") == "2.5"


def test_points_value_unset_or_disabled():
    assert points_value({}, "cf") == ""          # field present in config, no value
    assert points_value({"cf": None}, "cf") == ""  # explicit null
    assert points_value({"cf": 4.0}, None) == ""   # feature disabled (no field configured)


def test_normalize_status_aliases():
    assert normalize_status("READY FOR REVIEW") == "In Review"
    assert normalize_status("READY FOR PROD") == "Ready for Production"
    assert normalize_status("IN PROGRESS") == "In Progress"


def test_normalize_status_passthrough():
    assert normalize_status("Custom Stage") == "Custom Stage"


def test_truncate():
    assert truncate("short", 10) == "short"
    long = "a" * 100
    truncated = truncate(long, 10)
    assert len(truncated) == 10
    assert truncated.endswith("…")


def test_type_badge():
    badge = type_badge("Bug")
    assert "B" in str(badge)
    badge = type_badge("Story")
    assert "S" in str(badge)


def test_priority_badge():
    badge = priority_badge("Highest")
    assert "P0" in str(badge)
    badge = priority_badge("Low")
    assert "P3" in str(badge)


def test_relative_time_format():
    # Just verify it doesn't crash on Jira-style timestamps.
    out = relative_time("2026-04-24T14:11:35.972-0400")
    assert "ago" in out or out == "—"


def test_relative_time_none():
    assert relative_time(None) == "—"


def test_relative_time_invalid():
    assert relative_time("not-a-date") == "not-a-date"
