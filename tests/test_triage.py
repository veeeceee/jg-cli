"""Pure triage-floor tests. The load-bearing rule: when unsure, surface — only a
hard SUPPRESSED verdict hides an item."""

from __future__ import annotations

import asyncio

import jg.llm
from jg import triage
from jg.triage import JudgeItem, Verdict, classify, classify_slack

ME = ["vibhu@charmhealthtech.com"]


def _c(sender, *, to_cc="", is_bulk=False, noise=None, signal=None, me=ME):
    return classify(
        sender=sender, to_cc=to_cc, is_bulk=is_bulk,
        my_addresses=me, noise_senders=noise or [], signal_senders=signal or [],
    )


def test_bulk_to_me_is_still_suppressed():
    # newsletters are sent To: me too — "addressed to me" must NOT rescue bulk
    r = _c("newsletter <news@vendor.com>", to_cc="vibhu@charmhealthtech.com", is_bulk=True)
    assert r.verdict is Verdict.SUPPRESSED


def test_non_bulk_direct_mail_to_me_is_actionable():
    r = _c('"Colleague" <c@partner.com>', to_cc="vibhu@charmhealthtech.com", is_bulk=False)
    assert r.verdict is Verdict.ACTIONABLE


def test_signal_sender_overrides_bulk():
    r = _c('"Jaimon" <jaimon@charmhealthtech.com>', is_bulk=True, signal=["jaimon@charmhealthtech.com"])
    assert r.verdict is Verdict.ACTIONABLE


def test_noise_sender_suppressed():
    r = _c("Grafana <alerts@grafana.example.com>", noise=["grafana"])
    assert r.verdict is Verdict.SUPPRESSED


def test_code_forge_notification_is_unsure_not_suppressed():
    # the Jaimon case: GitHub sends bulk-headered mail, but a real reply is signal
    r = _c('"Jaimon Jose" <notifications@github.com>', is_bulk=True)
    assert r.verdict is Verdict.UNSURE
    assert r.surfaced is True


def test_bulk_marketing_suppressed():
    r = _c("ByteByteGo <news@bytebytego.com>", is_bulk=True)
    assert r.verdict is Verdict.SUPPRESSED


def test_automated_noreply_suppressed():
    r = _c("Grafana <noreply@grafana.example.com>")  # noreply local-part
    assert r.verdict is Verdict.SUPPRESSED


def test_human_no_marker_is_unsure_and_surfaces():
    r = _c('"Some Person" <person@partner.com>', to_cc="team@charmhealthtech.com")
    assert r.verdict is Verdict.UNSURE
    assert r.surfaced is True


def test_precedence_order_signal_before_noise():
    # a sender in BOTH lists: signal wins (checked first — friction on suppression)
    r = _c("x@both.com", noise=["both.com"], signal=["both.com"])
    assert r.verdict is Verdict.ACTIONABLE


def test_no_my_addresses_falls_through_safely():
    r = _c('"Person" <p@x.com>', to_cc="p@x.com", me=[])
    assert r.verdict is Verdict.UNSURE  # not crash; surfaces


# ── Slack floor ─────────────────────────────────────────────────────────────────
def test_slack_dm_is_actionable():
    r = classify_slack(kind="dm", channel_name="DM: Jaimon", noise_channels=[], signal_channels=[])
    assert r.verdict is Verdict.ACTIONABLE


def test_slack_noise_channel_suppressed():
    r = classify_slack(kind="mention", channel_name="#the-ratpack", noise_channels=["the-ratpack"], signal_channels=[])
    assert r.verdict is Verdict.SUPPRESSED


def test_slack_signal_channel_actionable():
    r = classify_slack(kind="channel", channel_name="#dev-team", noise_channels=[], signal_channels=["dev-team"])
    assert r.verdict is Verdict.ACTIONABLE


def test_slack_other_mention_is_unsure():
    r = classify_slack(kind="mention", channel_name="#some-work-chan", noise_channels=[], signal_channels=[])
    assert r.verdict is Verdict.UNSURE


def test_slack_channel_match_tolerates_hash_prefix():
    r = classify_slack(kind="mention", channel_name="#the-ratpack", noise_channels=["#the-ratpack"], signal_channels=[])
    assert r.verdict is Verdict.SUPPRESSED


# ── the LLM judge (stubbed claude) ──────────────────────────────────────────────
def test_judge_parses_verdicts_and_defaults_conservative(monkeypatch):
    async def fake_run(prompt, claude_path="claude"):
        return '[{"id":"a","verdict":"suppressed"},{"id":"b","verdict":"actionable"}]'

    monkeypatch.setattr(jg.llm, "run_claude", fake_run)
    items = [JudgeItem("a", "x", "s"), JudgeItem("b", "y", "t"), JudgeItem("c", "z", "u")]
    out = asyncio.run(triage.judge(items, use_cache=False))
    assert out["a"] == "suppressed"
    assert out["b"] == "actionable"
    assert out["c"] == "actionable"  # unresolved → surfaces (never suppress on doubt)


def test_judge_is_failsoft(monkeypatch):
    async def boom(prompt, claude_path="claude"):
        raise RuntimeError("claude down")

    monkeypatch.setattr(jg.llm, "run_claude", boom)
    out = asyncio.run(triage.judge([JudgeItem("a", "x", "s")], use_cache=False))
    assert out == {"a": "actionable"}  # everything stays surfaced on failure
