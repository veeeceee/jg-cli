"""Task-typed orchestration gates.

jg is a coordinator: it never authors work, it orchestrates Claude to. But every
authoring orchestration is gated first — jg forces the architectural decision out
of you (modulated by your mastery level in ~/.ai/progress.json) and only then
launches Claude with your decision baked in. Delegate production, never delegate
understanding — enforced structurally at the moment of orchestration.

This holds the gate *content* declaratively so it can move to a config/file
later. The prototype ships one gate: epic → tasks decomposition.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GateOption:
    name: str
    optimizes: str
    sacrifices: str
    failure_mode: str


@dataclass
class GateSpec:
    key: str            # orchestration id, e.g. "epic-decompose"
    pattern: str        # progress.json pattern name that governs the level
    title: str
    contradiction: str  # the two forces in tension
    goal: str
    options: list[GateOption]
    pruned: list[str] = field(default_factory=list)  # "name — why excluded"


EPIC_DECOMPOSE = GateSpec(
    key="epic-decompose",
    pattern="epic-decomposition",
    title="Slice epic into tasks",
    contradiction=(
        "Tasks must be independently workable yet coherent as a whole — slice for "
        "independence and you risk integration surprises; slice for coherence and "
        "tasks bottleneck each other."
    ),
    goal="A task breakdown that ships the epic, not one that just accumulates done tickets.",
    options=[
        GateOption(
            "Vertical slice",
            "shippable increments, early feedback, each task demoable",
            "more upfront design to find the slices; cross-cutting concerns awkward to slice",
            "slices that secretly share hidden infra → false independence, late integration surprises",
        ),
        GateOption(
            "Risk-first / spike-led",
            "kills uncertainty early, prevents late catastrophes, informs the real breakdown",
            "front-loads no-visible-progress work; the tail is hard to plan until spikes resolve",
            "spikes become untimed rabbit holes that never converge",
        ),
        GateOption(
            "Milestone/phase",
            "stakeholder legibility, natural checkpoints, matches roadmap thinking",
            "coarse-grained — phases aren't tasks; can hide big tasks inside a phase",
            "'part 1/2/3' with no independent value → collapses into horizontal",
        ),
        GateOption(
            "Horizontal layer",
            "matches code structure, clear per-layer ownership, easy to estimate",
            "nothing ships until all layers land; late integration; value backloaded",
            "the '90% done' trap — every layer half-built, nothing works end to end",
        ),
    ],
    pruned=[
        "by component/team — optimizes for Conway's-law coordination absent at this team size",
        "point-balanced — sizing is orthogonal; applied within a strategy, not a strategy itself",
    ],
)

# Registry (one entry for the prototype). Keyed by orchestration id.
GATES: dict[str, GateSpec] = {EPIC_DECOMPOSE.key: EPIC_DECOMPOSE}


def build_decompose_prompt(
    option: GateOption, epic_key: str, epic_summary: str, scope: str, reasoning: str
) -> str:
    """Bake the user's gated decisions (scope + strategy + reasoning) into the
    Claude orchestration prompt."""
    return (
        f'Decompose work under epic {epic_key} "{epic_summary}" into Jira tasks.\n'
        f"Scope I defined (what to slice / correct parent): {scope.strip() or '(unspecified)'}.\n"
        f"Decomposition strategy I chose: {option.name} — optimize for: {option.optimizes}.\n"
        f"My reasoning: {reasoning.strip() or '(none given)'}.\n"
        f"Guard against this failure mode: {option.failure_mode}.\n"
        f"First read the relevant issues (via the Jira MCP or `jg view`). If my stated "
        f"scope implies a different parent than {epic_key} (e.g. a fresh child epic), "
        f"do that. Propose the task breakdown, get my confirmation, then create the tasks."
    )
