"""Reconcile floor: the three-way join (declared ⋈ actual ⋈ artifact) and the
state classification, per the table in docs/work-model.md."""

from __future__ import annotations

from jg.reconcile import (
    PR,
    WARM_SECONDS,
    ReconcileItem,
    Session,
    State,
    Ticket,
    extract_key,
    reconcile,
)


def test_extract_key_from_titles_and_branches():
    assert extract_key("CH-314") == "CH-314"
    assert extract_key("decompose·CH-36") == "CH-36"
    assert extract_key("feature/CH-489-billing") == "CH-489"
    assert extract_key("ad-hoc research") is None
    assert extract_key("") is None


def test_session_warmth_threshold():
    assert Session("CH-1", idle_seconds=60).warm is True
    assert Session("CH-1", idle_seconds=WARM_SECONDS - 1).warm is True
    assert Session("CH-1", idle_seconds=WARM_SECONDS + 1).warm is False
    assert Session("CH-1", idle_seconds=None).warm is False


def _by_key(items: list[ReconcileItem]) -> dict[str | None, ReconcileItem]:
    return {i.key: i for i in items}


def test_each_state():
    tickets = [
        Ticket("CH-1", "In Progress", "In Progress"),   # + warm session → healthy
        Ticket("CH-2", "In Progress", "In Progress"),   # + cold session → cold
        Ticket("CH-3", "In Progress", "In Progress"),   # nothing → stalled
        Ticket("CH-4", "To Do", "To Do"),               # + live session → undeclared
        Ticket("CH-5", "In Progress", "In Progress"),   # + merged PR → done-but-open
        Ticket("CH-6", "In Progress", "In Progress"),   # + open PR → resolving
        Ticket("CH-7", "Backlog", "To Do"),             # nothing → tracked (no live work)
    ]
    sessions = [
        Session("CH-1", idle_seconds=60),
        Session("decompose·CH-2", idle_seconds=WARM_SECONDS + 500),  # cold, key via prefix
        Session("CH-4", idle_seconds=120),
        Session("just poking around", idle_seconds=30),              # no key → untracked
        Session("CH-999", idle_seconds=45),                          # key, no open ticket → untracked
    ]
    prs = [
        PR(branch="CH-5-fix", state="merged"),
        PR(branch="CH-6-wip", state="open"),
    ]

    by = _by_key(reconcile(sessions, tickets, prs))
    assert by["CH-1"].state is State.HEALTHY
    assert by["CH-2"].state is State.COLD
    assert by["CH-3"].state is State.STALLED
    assert by["CH-4"].state is State.UNDECLARED
    assert by["CH-5"].state is State.DONE_BUT_OPEN
    assert by["CH-6"].state is State.RESOLVING
    assert by["CH-7"].state is State.TRACKED
    assert by["CH-999"].state is State.UNTRACKED     # session key with no open ticket

    # the keyless session surfaces as an untracked item (key is None)
    untracked_keyless = [i for i in reconcile(sessions, tickets, prs) if i.key is None]
    assert len(untracked_keyless) == 1
    assert untracked_keyless[0].state is State.UNTRACKED
    assert untracked_keyless[0].session_title == "just poking around"


def test_mismatch_flags():
    tickets = [Ticket("CH-1", "In Progress", "In Progress"), Ticket("CH-3", "In Progress", "In Progress")]
    sessions = [Session("CH-1", idle_seconds=10)]  # CH-1 healthy, CH-3 stalled
    by = _by_key(reconcile(sessions, tickets, []))
    assert by["CH-1"].is_mismatch is False   # healthy is not a mismatch
    assert by["CH-3"].is_mismatch is True    # stalled is


def test_merged_pr_without_open_ticket_is_not_flagged():
    # ticket already done (absent from the open set) + merged PR → nothing to reconcile
    by = _by_key(reconcile([], [], [PR(branch="CH-8-done", state="merged")]))
    assert by["CH-8"].state is State.TRACKED
    assert by["CH-8"].is_mismatch is False


def test_warm_wins_when_multiple_sessions_on_one_key():
    sessions = [Session("CH-1", idle_seconds=WARM_SECONDS + 100), Session("CH-1", idle_seconds=5)]
    by = _by_key(reconcile(sessions, [Ticket("CH-1", "In Progress", "In Progress")], []))
    assert by["CH-1"].state is State.HEALTHY  # any warm session → warm


def test_empty_inputs():
    assert reconcile([], [], []) == []
