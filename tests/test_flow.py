"""Pure helpers in flow.py."""

from __future__ import annotations

from jg.flow import _triage_rule


def test_email_rule_is_sender_name():
    assert _triage_rule("email", "Grafana") == ("Grafana", False)


def test_slack_channel_rule_strips_hash():
    assert _triage_rule("slack", "#the-ratpack") == ("the-ratpack", True)


def test_slack_dm_has_no_rule():
    assert _triage_rule("slack", "DM: Mathan Prabakaran") is None


def test_pr_and_zoho_have_no_rule():
    assert _triage_rule("review", "org/repo#47") is None
    assert _triage_rule("zoho", "#1544149") is None


def test_blank_email_sender_no_rule():
    assert _triage_rule("email", "   ") is None
