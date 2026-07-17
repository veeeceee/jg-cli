"""Pure triage-floor tests. The load-bearing rule: when unsure, surface — only a
hard SUPPRESSED verdict hides an item."""

from __future__ import annotations

from jg.triage import Verdict, classify

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
