"""Pure-core clustering tests — no live LLM. Exercise the backbone extraction,
the merge asymmetry (the load-bearing invariants), grouping, and fail-soft."""

from __future__ import annotations

from jg.cluster import (
    Anchor,
    EdgeKind,
    Item,
    _extract_json_array,
    build_backbone,
    group,
    merge_llm_edges,
)

ANCHORS = [Anchor("CH-1", "Nabla AI-scribe integration"), Anchor("CH-2", "Billing codes")]


def test_backbone_authored_edges():
    items = [
        Item("pr:r#1", "pr", "fix", linked_keys=["CH-1"]),
        Item("zoho:9", "zoho", "ticket", linked_keys=["CH-2"]),
        Item("zoho:8", "zoho", "loose ticket"),  # no key => loose
    ]
    edges = build_backbone(items)
    assert len(edges) == 2
    assert all(e.kind == EdgeKind.AUTHORED for e in edges)
    assert {e.item_id for e in edges} == {"pr:r#1", "zoho:9"}
    # authored priors are high-confidence
    assert all(e.confidence >= 0.9 for e in edges)


def test_llm_never_overrides_authored():
    items = [Item("pr:r#1", "pr", "fix", linked_keys=["CH-1"])]
    backbone = build_backbone(items)
    # LLM tries to move an authored item to a different anchor — must be ignored.
    rows = [{"item_id": "pr:r#1", "anchor_key": "CH-2", "reason": "x", "confidence": 0.9}]
    edges = merge_llm_edges(backbone, rows, {"CH-1", "CH-2"}, loose_ids=set())
    assert len(edges) == 1
    assert edges[0].anchor_key == "CH-1"
    assert edges[0].kind == EdgeKind.AUTHORED


def test_llm_assigns_loose_item_tentatively():
    rows = [{"item_id": "zoho:8", "anchor_key": "CH-1", "reason": "both about Nabla", "confidence": 0.6}]
    edges = merge_llm_edges([], rows, {"CH-1"}, loose_ids={"zoho:8"})
    assert len(edges) == 1
    e = edges[0]
    assert e.kind == EdgeKind.LLM
    assert e.anchor_key == "CH-1"
    # the invariant: LLM edges never read as a hard link
    assert e.reason.startswith("grouped:")
    assert "both about Nabla" in e.reason


def test_llm_cannot_invent_an_anchor():
    rows = [{"item_id": "zoho:8", "anchor_key": "CH-999", "reason": "x", "confidence": 0.9}]
    edges = merge_llm_edges([], rows, {"CH-1"}, loose_ids={"zoho:8"})
    assert edges == []  # CH-999 is not a real anchor


def test_llm_null_anchor_is_dropped():
    rows = [{"item_id": "zoho:8", "anchor_key": None, "reason": "no fit", "confidence": 0.1}]
    edges = merge_llm_edges([], rows, {"CH-1"}, loose_ids={"zoho:8"})
    assert edges == []


def test_confidence_is_clamped_and_defaulted():
    rows = [
        {"item_id": "a", "anchor_key": "CH-1", "confidence": 5},      # over 1
        {"item_id": "b", "anchor_key": "CH-1", "confidence": "huh"},  # unparseable
    ]
    edges = merge_llm_edges([], rows, {"CH-1"}, loose_ids={"a", "b"})
    assert edges[0].confidence == 1.0
    assert 0.0 <= edges[1].confidence <= 1.0


def test_group_forms_clusters_and_residual():
    items = [
        Item("pr:r#1", "pr", "fix", linked_keys=["CH-1"]),
        Item("zoho:8", "zoho", "loose"),
    ]
    backbone = build_backbone(items)
    edges = merge_llm_edges(
        backbone,
        [{"item_id": "zoho:8", "anchor_key": "CH-1", "reason": "topic", "confidence": 0.7}],
        {"CH-1", "CH-2"},
        loose_ids={"zoho:8"},
    )
    clusters, residual = group(edges, ANCHORS, items)
    assert len(clusters) == 1
    c = clusters[0]
    assert c.anchor_key == "CH-1"
    assert {m.id for m in c.members} == {"pr:r#1", "zoho:8"}
    assert residual == []  # everything anchored


def test_group_leaves_unmatched_in_residual():
    items = [Item("zoho:8", "zoho", "orphan")]
    clusters, residual = group([], ANCHORS, items)
    assert clusters == []
    assert [it.id for it in residual] == ["zoho:8"]


def test_authored_edge_to_non_anchor_forms_no_cluster():
    # linked to a key that isn't among current anchors (e.g. a closed ticket)
    items = [Item("pr:r#1", "pr", "fix", linked_keys=["CH-999"])]
    edges = build_backbone(items)
    clusters, residual = group(edges, ANCHORS, items)
    assert clusters == []
    assert [it.id for it in residual] == ["pr:r#1"]


def test_extract_json_array_tolerates_fences_and_prose():
    assert _extract_json_array('```json\n[{"a":1}]\n```') == [{"a": 1}]
    assert _extract_json_array('here you go: [{"x":2}] done') == [{"x": 2}]
    assert _extract_json_array("not json at all") == []
    assert _extract_json_array("") == []
