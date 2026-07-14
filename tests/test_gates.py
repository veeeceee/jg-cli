"""Gate content + prompt baking for task-typed orchestration."""

from __future__ import annotations

from jg import gates


def test_epic_decompose_spec_shape():
    spec = gates.EPIC_DECOMPOSE
    assert spec.pattern == "epic-decomposition"
    names = [o.name for o in spec.options]
    assert names == ["Vertical slice", "Risk-first / spike-led", "Milestone/phase", "Horizontal layer"]
    assert len(spec.pruned) == 2
    assert gates.GATES["epic-decompose"] is spec


def test_build_decompose_prompt_bakes_decision():
    opt = gates.EPIC_DECOMPOSE.options[0]  # Vertical slice
    prompt = gates.build_decompose_prompt(
        opt, "CH-36", "AugmentedEHR", scope="the eRx feature", reasoning="ship per-workflow"
    )
    assert "CH-36" in prompt and "AugmentedEHR" in prompt
    assert "Vertical slice" in prompt
    assert "the eRx feature" in prompt            # scope decision carried through
    assert "ship per-workflow" in prompt          # user's reasoning carried through
    assert opt.failure_mode in prompt             # failure mode to guard against


def test_build_decompose_prompt_handles_missing_fields():
    opt = gates.EPIC_DECOMPOSE.options[3]  # Horizontal layer
    prompt = gates.build_decompose_prompt(opt, "CH-2", "AWS Migration", scope="", reasoning="   ")
    assert "(none given)" in prompt
    assert "(unspecified)" in prompt
