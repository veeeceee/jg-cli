"""Zoho Desk discovery: identity resolution, the four involvement types, and
the tokenizer-false-positive drop — driven through a fake client."""

from __future__ import annotations

import pytest

from jg import zoho
from jg.config import ZohoConfig

EMAILS = ["vibhu@charmhealthtech.com", "vibhu.c@medicalmine.com"]


class FakeClient:
    def __init__(self, agents, search, convs, comments, by_assignee=None):
        self._agents = agents
        self._search = search
        self._convs = convs
        self._comments = comments
        self._by_assignee = by_assignee or {}

    async def agents(self):
        return self._agents

    async def search_tickets(self, term, limit=100):
        return self._search.get(term, [])

    async def search_by_assignee(self, agent_id, limit=100):
        return self._by_assignee.get(agent_id, [])

    async def conversations(self, tid):
        return self._convs.get(tid, [])

    async def comments(self, tid):
        return self._comments.get(tid, [])


def _mk_client():
    agents = [{"id": "A1", "emailId": "vibhu.c@medicalmine.com", "zuid": "Z1"}]
    t1 = {"id": "1", "ticketNumber": "101", "subject": "thread one", "assigneeId": "OTHER", "status": "Open", "modifiedTime": "2026-07-05"}
    t5 = {"id": "5", "ticketNumber": "105", "subject": "ping vibhu.c@medicalmine.com re x", "assigneeId": "OTHER", "status": "Open", "modifiedTime": "2026-07-04"}
    t2 = {"id": "2", "ticketNumber": "102", "subject": "assigned", "assigneeId": "A1", "status": "Open", "modifiedTime": "2026-07-03"}
    t3 = {"id": "3", "ticketNumber": "103", "subject": "mentioned", "assigneeId": "OTHER", "status": "Open", "modifiedTime": "2026-07-02"}
    t4 = {"id": "4", "ticketNumber": "104", "subject": "a different Vibhu entirely", "assigneeId": "OTHER", "status": "Open", "modifiedTime": "2026-07-01"}
    # assigned to me but my email is NOT in any searchable text → only caught by
    # the direct assignee query, not the _all email search.
    t6 = {"id": "6", "ticketNumber": "106", "subject": "no email here", "assigneeId": "A1", "status": "Open", "modifiedTime": "2026-07-06"}
    # tagged me in a comment; my email absent, not assigned to me → only findable
    # by searching my zuid (the mention markup is indexed under _all).
    t7 = {"id": "7", "ticketNumber": "107", "subject": "someone tagged me", "assigneeId": "OTHER", "status": "Open", "modifiedTime": "2026-06-29"}
    search = {
        "vibhu@charmhealthtech.com": [t1, t5],
        "vibhu.c@medicalmine.com": [t2, t3, t4],
        "Z1": [t7],  # zuid search
    }
    convs = {"1": [{"to": "vibhu@charmhealthtech.com", "cc": "", "fromEmailAddress": "x@y.com"}]}
    comments = {
        "3": [{"content": "please review zsu[@user:Z1]zsu thanks"}],
        "7": [{"content": "zsu[@user:Z1]zsu plz check this"}],
    }
    return FakeClient(agents, search, convs, comments, by_assignee={"A1": [t6]})


@pytest.mark.asyncio
async def test_resolve_identity():
    client = _mk_client()
    ident = await zoho.resolve_identity(client, EMAILS)
    # only the medicalmine address is an agent; charmhealthtech isn't (fine per spec)
    assert ident == {"vibhu.c@medicalmine.com": {"agentId": "A1", "zuid": "Z1"}}


@pytest.mark.asyncio
async def test_find_involved_classifies_and_drops_false_positives():
    client = _mk_client()
    cfg = ZohoConfig(agent_emails=EMAILS)
    result = await zoho.find_involved(client, cfg)
    by_id = {t.id: t.involvement for t in result}

    assert by_id["2"] == ["ASSIGNED"]          # assigneeId matches my agent
    assert by_id["6"] == ["ASSIGNED"]          # caught only by the direct assignee query
    assert by_id["1"] == ["THREAD"]            # my email in a thread's to/cc
    assert by_id["3"] == ["MENTIONED"]         # my zuid in a comment's @mention
    assert by_id["5"] == ["BODY"]              # my exact email in the subject
    assert by_id["7"] == ["MENTIONED"]         # tagged me; found only via the zuid search
    assert "4" not in by_id                    # fuzzy _all match, no real involvement → dropped

    # sorted newest-first by modifiedTime
    assert [t.id for t in result] == ["6", "1", "5", "2", "3", "7"]


@pytest.mark.asyncio
async def test_find_involved_no_emails_is_empty():
    assert await zoho.find_involved(_mk_client(), ZohoConfig(agent_emails=[])) == []
