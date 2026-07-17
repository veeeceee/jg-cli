"""Pure emergent-thread tests — the incremental-join fold (apply_join) and id
stability. No I/O, no LLM."""

from __future__ import annotations

from jg.threads import Thread, apply_join, prune_stale, thread_id

NOW = "2026-07-17T12:00:00"


def test_prune_drops_stale_keeps_fresh():
    fresh = Thread("th-f", "fresh", ["a", "b"], NOW, "2026-07-10T00:00:00")   # 7 days
    stale = Thread("th-s", "stale", ["c", "d"], NOW, "2026-06-01T00:00:00")   # ~46 days
    kept = prune_stale([fresh, stale], NOW, ttl_days=21)
    assert [t.id for t in kept] == ["th-f"]


def test_prune_tolerates_bad_timestamp():
    bad = Thread("th-b", "bad", ["a", "b"], NOW, "not-a-date")
    assert prune_stale([bad], NOW) == [bad]  # kept, not deleted, on parse error


def test_prune_bad_now_is_noop():
    stale = Thread("th-s", "x", ["a", "b"], NOW, "2000-01-01T00:00:00")
    assert prune_stale([stale], "garbage") == [stale]


def test_thread_id_stable_and_order_independent():
    assert thread_id(["a", "b"]) == thread_id(["b", "a"])
    assert thread_id(["a", "b"]) != thread_id(["a", "c"])


def test_new_thread_from_two_new_items():
    out = apply_join([], {"x", "y"}, {"new_threads": [{"descriptor": "Nabla", "members": ["x", "y"]}]}, NOW)
    assert len(out) == 1
    assert out[0].descriptor == "Nabla"
    assert set(out[0].members) == {"x", "y"}


def test_single_member_thread_is_not_created():
    out = apply_join([], {"x"}, {"new_threads": [{"descriptor": "solo", "members": ["x"]}]}, NOW)
    assert out == []


def test_assign_new_item_to_existing_thread():
    stored = [Thread("th-1", "Nabla", ["a", "b"], NOW, NOW)]
    out = apply_join(stored, {"c"}, {"assign": [{"item_id": "c", "thread_id": "th-1"}]}, NOW)
    assert set(out[0].members) == {"a", "b", "c"}


def test_assign_ignores_ids_not_in_new_set():
    # the LLM must not re-touch settled members or invent ids
    stored = [Thread("th-1", "Nabla", ["a", "b"], NOW, NOW)]
    out = apply_join(stored, {"c"}, {"assign": [{"item_id": "z", "thread_id": "th-1"}]}, NOW)
    assert set(out[0].members) == {"a", "b"}  # z (not in new_ids) rejected


def test_assign_to_unknown_thread_is_ignored():
    out = apply_join([], {"c"}, {"assign": [{"item_id": "c", "thread_id": "nope"}]}, NOW)
    assert out == []


def test_new_thread_members_filtered_to_new_ids():
    # only genuinely-new members count toward the ≥2 threshold
    out = apply_join([], {"x"}, {"new_threads": [{"descriptor": "t", "members": ["x", "stale"]}]}, NOW)
    assert out == []  # only x is new → 1 member → no thread
