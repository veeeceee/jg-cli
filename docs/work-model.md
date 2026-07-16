# jg work model: flow-home, reconcile, and the intelligence layer

Status: design-of-record for jg's UI/UX direction. Captures a model built
collaboratively. Some parts are decided (marked **Decided**); the current layout
is a working default that may still change (marked **Working default**); some
parts are open (marked **Open**). This is the thinking the implementation should
connect back to, not a spec of shipped behavior.

## How this model came about

The starting question was how to improve jg as the surface for managing work.
An early attempt — a single-pane "altitude" zoom-stack (Portfolio → Initiative →
Task, one pane at a time, breadcrumb for orientation) — was built, tested, and
retired: it lost peripheral context and its cold-start screen didn't match "show
me my work." Deep research and live use then backed a 3-panel master-detail
dashboard.

That 3-panel dashboard is the **current** substrate — the working default because
nothing has beaten it yet, not a fixed foundation. The model below is what to
build on it for now, and the flow-home in particular is the thing most likely to
revise it: if flow-buckets fight the master-detail framing in practice, that
argues for reshaping the substrate, not forcing the buckets into panels that
resist them.

## Work as three planes

Work sorts into three planes, not a flat list of item types:

- **Communication** — incoming and outgoing email, Slack, meetings, Zoho/desk
  threads, in-person discussion. This is the plane work arrives and leaves
  through.
- **Plans** — the shaping layer: intent and structure for projects and other
  efforts.
- **Projects and tasks** — the execution layer. Tasks are either planned
  (decomposed from a plan) or emergent (an urgent bug, an inbound request).

Communication is not a sibling of tasks. It is the medium the other two flow
through: a message can be a nascent task, the closing of one, or a plan
discussion.

### The planes are in dialectical tension

Each pair mutually shapes the other, and naming the specific tension keeps the
model from being a truism:

- **Communication ⇄ Plans** — flux vs. stability. Incoming comms are what a plan
  is made from; once the plan crystallizes, new comms destabilize it.
- **Communication ⇄ Tasks** — ambiguity vs. commitment. A message is open-ended;
  turning it into a task forces scope; executing the task reopens communication.
- **Plans ⇄ Tasks** — intent vs. reality. A plan generates tasks by
  decomposition; emergent tasks are reality negating the plan and forcing it to
  adapt.

### The gate is the operator between planes

The transition between two planes is the dialectical moment, and a gate is where
it resolves through the user's judgment (thesis + antithesis → synthesis). This
is why the gate felt like the right primitive, and it generalizes:

- communication → task = **escalate** (built: Zoho → Jira)
- plan → tasks = **decompose** (built: epic → tasks)
- tasks → plan = does reality revise the plan? (not built)
- task → communication = close the loop back out — reply/notify (not built)

## The flow-home (cold-start view)

**Decided.** When the workday starts, the immediate need is "where is my work in
its lifecycle and what needs me at each stage." The home answers that with three
flow-buckets that cut across all three planes:

- **Incoming** — new arrivals needing triage: new comms, review requests, fresh
  assignments, @mentions, emergent bugs. This is *what arrived since I last
  looked*, not the standing backlog.
- **In progress** — what I'm actively executing: my in-flight tasks and their
  open PRs (see the reconcile section — this is really "live Claude sessions").
- **Resolving** — the bucket most tools miss: things needing a final push or a
  nudge — a PR waiting on a reviewer, a ticket in QA, something blocked on
  someone else. Work that is almost done but stalls if unwatched.

### Dials and cross-cuts

- **plan-scope dial** — this plan ↔ across all plans.
- **time-window dial** — day / week / sprint.
- **urgency** — a cross-cutting highlight/sort (overdue, SLA, high priority), not
  a fourth bucket. Urgency is a property, not a stage.

Plan is deliberately two different things: a **scope-dial** on the flow (daily:
"what's moving in CHAI") *and* a distinct **aggregate view** — the roadmap: %
done, blocked epics, next milestone. Filtering the flow does not tell you a
plan's health; that is a rollup, a different operation. The roadmap stays a
separate deliberate visit; the home is flow-state.

### What the real-data test taught

Built from a live snapshot across Jira / Gmail / Slack / GitHub / Zoho:

- **Incoming is mostly noise.** Of ~30 unread items, roughly 5 were real signal;
  the rest were newsletters, Grafana alerts, notification digests. The
  relevance/urgency filter is not a nicety — it is what makes the bucket usable.
- **Incoming ≠ backlog.** ~47 To-Do tickets are planned work and belong to the
  plan/project view, not the daily flow-home.
- **One work-thread scatters across planes.** "Nabla" appeared as a Zoho
  @mention, a Slack DM, and related Jira work — one effort, three sources.
  Clustering scattered items into their thread is the highest-value hard problem.

### Layout consequence

The home is not the kanban and not the roadmap — it is one flow view that
unifies inbox and kanban, because "incoming" already spans comms + reviews +
emergent + Zoho and "in-progress/resolving" are tasks + PRs. "Unified" means
unified by flow-state, not by layout. The dials already exist in the current
dashboard (Projects panel = plan-scope; kanban view-tabs = time-window); the
change is the primary grouping — flow-buckets rather than status-columns.

The 3-panel structure is the current substrate and stays for now. It is also the
most likely thing to be revised by this model: if the flow-buckets prove the
master-detail grouping wrong in use, reshape the substrate rather than force the
buckets into panels that resist them.

## In-progress as Claude Code sessions, and the reconcile

The in-progress bucket's real substance is Claude Code sessions. A Jira ticket
marked In-Progress is a *declared* state; a live Claude session is the *actual*
one. The gap between them is the plan⇄execution dialectic in the data.

jg already has the primitive: it titles the tmux pane on spawn (the Jira key, or
`decompose·CH-36`, `escalate·#1539420`) and has `find_pane_by_title` +
`select_pane`. So live sessions are enumerable and jumpable.

### The three-way join

**Decided (shape).** Reconcile is a join keyed on the Jira key across three
sources: **declared** (Jira status) ⋈ **actual** (Claude session: none / warm /
cold) ⋈ **artifact** (PR: none / open / in-review / merged). The value is the
mismatches, each with one obvious next move:

| Declared | Session | PR | State | The one move |
|---|---|---|---|---|
| In-Progress | warm | — | healthy | jump in |
| In-Progress | cold | — | paused/cold | resume (expensive) or fresh brief |
| In-Progress | none | none | stalled | resume/restart, or re-triage |
| To-Do | live | — | undeclared | move ticket → In-Progress |
| any | — | merged | done-but-open | move ticket → Done |
| In-Progress | — | in-review | actually resolving | belongs in Resolving |
| none | live | — | untracked | file a ticket, or leave ad-hoc |

### The join key is a heuristic — plan for its failure

The key works when the pane title and PR branch carry the Jira key. It breaks for
the ad-hoc session with no ticket and the PR whose branch doesn't name the key.
jg-spawned panes get the key for free (canonical); everything else falls back to
fuzzy matching (branch substring, session cwd → repo → project), and when that
fails, the item shows **unjoined** ("untracked session", "unlinked PR") rather
than guessing. Unjoined-but-visible beats confidently-mismatched.

### The actions split along jg's identity

**Decided.** Reconcile flags the mismatch and offers the move. The moves split:

- **Mechanical reconciliation** (move ticket To-Do→In-Progress or
  In-Progress→Done to match reality) — ungated quick action. Aligning declared to
  actual; nothing to force.
- **Authoring** (file a ticket from an untracked session; restart a stalled
  task) — gated, like escalate/decompose, because it creates work and the user
  should own the framing.

### Cache economics of resuming (continuity vs. frugality)

This detail is load-bearing: it is the rationale for the fresh-brief re-seed, not
implementation trivia.

Prompt caching is a prefix match with a TTL (Claude Code uses the 1-hour tier).
Within the window a cached transcript reads at ~0.1× input price; past it, the
first resumed turn re-processes the whole transcript uncached at 1× and re-writes
at ~2× — roughly 10× the input cost of a warm continue, plus reprocessing
latency. The cost scales with how long/valuable the session is, and "stale" is
the normal state for anything untouched for an hour.

This makes live-vs-resumable a cost gradient and surfaces a dialectic:

- **Resume the stale transcript** — preserves built-up context, pays the cold
  reload, drags accumulated cruft back in.
- **Fresh session seeded with a compact task brief** — cache-frugal and clean,
  reconstructs context from the brief.

jg's coordinator role makes the frugal path cheap: it holds the task's canonical
state (ticket, plan, prior findings, linked PR), so "resume work on CH-314" can
re-seed a fresh session from a tight brief rather than rehydrate a bloated
transcript. The old transcript becomes reference, not required reload. The
reconcile therefore carries a staleness signal: warm → jump in; cold → "resuming
reloads ~N tokens at full price, or start fresh from the brief."

## Deterministic floor, LLM intelligence layer

**Decided (principle).** The LLM is an intelligence layer on top of a
deterministic structure it cannot bypass — not the structure itself.

Deterministic, synchronous, every refresh:

- the join (key ⋈ pane title ⋈ PR branch) and the state-table lookup
- correctness-critical facts (is this PR merged? is this ticket In-Progress?)
- mechanical actions (status moves)

An LLM here is slower, costs money per refresh, flickers (classifies differently
run to run → loss of trust), and can confidently mis-join. A wrong join is worse
than no join.

LLM, asynchronous, background/on-demand, never blocking the render:

- cross-source clustering
- incoming triage / noise filtering
- stale-session → fresh-brief summarization
- fuzzy fallback matching when the deterministic join fails

The home draws instantly from the floor; the LLM decorates it a beat later. This
keeps it frugal (no LLM call per keystroke) and trustworthy (facts are
deterministic; the LLM adds fuzzy value on top).

## Cross-source clustering (first LLM investment)

**Decided:** clustering is the first LLM investment; incoming triage/noise-filter
is a close second.

Clustering groups items from Jira / GitHub / Zoho / Slack / email / sessions that
belong to the same underlying work-thread (the Nabla example). It follows the
same floor-plus-fuzzy pattern, recursively:

- **Deterministic backbone** — explicit cross-references are ground-truth edges:
  a Jira key in a Slack message, the Zoho `Associated Jira Issues` field, a PR
  branch naming the ticket, a session pane titled with the key. Build the
  backbone from these first; the LLM never overrides an explicit edge.
- **LLM assigns the loose items** — comms/sessions with no explicit key, judged
  on content + participants + entities (people, client names, product modules).
- **Transparency + confidence** — render the edge reason ("linked via CH-142" =
  deterministic, trust it; "grouped: both about Nabla" = LLM inference,
  confidence-scored, glance-check it). Calibrated trust over a black box.

### Open fork: what clusters anchor on

- **Anchored** (current lean): clusters hang off the canonical graph
  (project → ticket); the LLM assigns each loose item to an existing anchor or
  marks it *unclustered*. The unclustered residual is the "new work with no
  ticket yet" pile, which feeds escalation (file a ticket → the cluster gets its
  anchor). Ties clustering to the actionable graph.
- **Emergent**: the LLM discovers threads freely with no anchor. Catches new work
  immediately but floats free of the graph and is fuzzier to trust.

## Decisions, working defaults, and open forks

Decided:

- Home = incoming / in-progress / resolving, with plan-scope + time-window dials
  and urgency as a cross-cut. Home is distinct from kanban and roadmap.
- Plan is both a scope-dial and a separate aggregate (roadmap).
- In-progress is grounded in live Claude sessions; reconcile is a three-way join
  (declared ⋈ actual ⋈ artifact) keyed on the Jira key.
- Mechanical status reconciliation is an ungated quick action; authoring is
  gated.
- Deterministic floor runs synchronously; the LLM is an async enrichment layer.
- Clustering is the first LLM investment; triage/noise-filter is second.

Working defaults (revisable):

- The 3-panel master-detail dashboard is the current substrate — held because
  nothing has beaten it, not as a foundation. Revision trigger: the flow-home
  proving the master-detail grouping wrong in use.

Open:

- Clustering anchor: anchored vs. emergent (leaning anchored + unclustered
  residual).
- Exact resume UX: how the cold/fresh-brief choice is presented.
- How much reconcile is auto-detected on refresh vs. computed on demand.
- The unbuilt gate edges: task → plan (reality revises the plan) and
  task → communication (close the loop out).
