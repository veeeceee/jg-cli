"""Pure Gmail parsing tests — no network, no auth."""

from __future__ import annotations

from jg.gmail import parse_message, sender_name


def _raw(headers: dict, *, snippet: str = "", labels=None, mid="m1", tid="t1") -> dict:
    return {
        "id": mid,
        "threadId": tid,
        "snippet": snippet,
        "labelIds": labels or [],
        "payload": {"headers": [{"name": k, "value": v} for k, v in headers.items()]},
    }


def test_parse_basic_headers():
    m = parse_message(_raw(
        {"From": '"Heather Duplessis" <heather@x.com>', "Subject": "CH-526 AI Scribe", "Date": "Wed, 16 Jul 2026"},
        snippet="following up on the scribe", labels=["INBOX", "UNREAD"],
    ))
    assert m.id == "m1" and m.thread_id == "t1"
    assert m.subject == "CH-526 AI Scribe"
    assert m.snippet == "following up on the scribe"
    assert "INBOX" in m.label_ids


def test_bulk_detection_via_list_unsubscribe():
    m = parse_message(_raw({"From": "news@vendor.com", "Subject": "Weekly digest",
                            "List-Unsubscribe": "<https://vendor.com/u>"}))
    assert m.is_bulk is True


def test_bulk_detection_via_precedence():
    m = parse_message(_raw({"From": "alerts@grafana.io", "Subject": "alert", "Precedence": "bulk"}))
    assert m.is_bulk is True


def test_human_mail_is_not_bulk():
    m = parse_message(_raw({"From": "colleague@charmhealthtech.com", "Subject": "quick q"}))
    assert m.is_bulk is False


def test_jira_keys_from_subject_and_snippet():
    m = parse_message(_raw({"From": "x@y.com", "Subject": "Re: CH-526 and CH-100"}, snippet="also CH-999"))
    assert set(m.jira_keys) == {"CH-526", "CH-100", "CH-999"}


def test_sender_name_extraction():
    assert sender_name('"Heather Duplessis" <heather@x.com>') == "Heather Duplessis"
    assert sender_name("bare@x.com") == "bare@x.com"
    assert sender_name("") == "—"


def test_missing_headers_are_empty_not_error():
    m = parse_message({"id": "m", "threadId": "t"})
    assert m.subject == "" and m.sender == "" and m.is_bulk is False
