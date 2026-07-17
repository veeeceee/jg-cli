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

- **Deterministic backbone** — explicit cross-references are the starting edges:
  a Jira key in a Slack message, the Zoho `Associated Jira Issues` field, a PR
  branch naming the ticket, a session pane titled with the key. Build the
  backbone from these first, but treat them as high-confidence priors, not axioms
  (see below).
- **LLM assigns the loose items** — comms/sessions with no explicit key, judged
  on content + participants + entities (people, client names, product modules).
- **Transparency + confidence** — render the edge reason ("linked via CH-142
  branch" vs "mentioned CH-142 in passing" vs "grouped: both about Nabla") with a
  confidence, so a weak or suspect link is glance-checkable. Calibrated trust over
  a black box.

### Deterministic edges are priors, not axioms

Extraction is deterministic; the link's validity is not. The regex reliably finds
"CH-142" in a message, but stale references, typos, copied branch names, a quoted
ticket, or a speculatively-filled Zoho field all produce a clean edge that is
semantically wrong. Treating the backbone as ground truth compounds badly: there
is no correction path, and one bad edge that causes a *merge* contaminates two
threads at once.

So edges are high-confidence priors, still falsifiable:

- **Weight by how deliberately the link was authored.** A PR branch, the Zoho
  field, a jg-set pane title are authored-links — strong. A key in free text is a
  *mention* — weak, needs corroboration before it is load-bearing.
- **Cheap deterministic sanity checks downgrade suspect edges** — does the key
  resolve to a real ticket, in a project I actually work? A key pointing elsewhere
  is probably a passing mention.
- **The LLM dissents, it does not silently override.** It can flag a contradiction
  (edge says CH-142, content is clearly unrelated) through the transparency layer;
  the human resolves it. That is the correction path.
- **Assignment vs. merge asymmetry.** Assigning an item to a thread is tentative
  and reversible, so a weak edge may do it. Merging two established threads is
  contaminating and hard to undo, so it demands corroboration — multiple edges or
  content agreement — never a single weak edge. This is what stops one bad edge
  from spreading.

Nothing is unquestionable ground truth: deterministic edges are strong priors,
the LLM corroborates and dissents, and the transparency layer plus gates make the
human the final arbiter — the "unjoined-but-visible beats confidently-mismatched"
rule, one level down.

### Anchoring: anchored-first, emergent on the residual

The anchored-vs-emergent choice dissolves — use both, layered. Anchor every item
that can attach to the canonical graph (explicit edges + LLM assignment to an
existing project/ticket). Then run emergent clustering only on the *residual* —
items that anchored to nothing — so a real thread with no ticket yet (a Zoho
ticket + two Slack DMs + an email about Nabla) is recognized as one thread rather
than scattered across a flat "unclustered" pile. This is also the frugal option:
anchored assignment is a bounded classification against N known anchors, and
emergent runs only over the leftover.

### Stability of emergent threads

Emergent threads must be sticky or membership flickers and trust erodes (the
reconcile flicker, one level down). Flicker comes from re-partitioning the
residual from scratch each run, so the fix is structural:

- **Durable thread objects + incremental join.** A thread is a stored entity
  (stable ID, LLM-authored descriptor, member list) that persists across
  refreshes. Each refresh classifies only the *new* residual items — join an
  existing thread or spawn one — and never re-derives settled membership.
- **The descriptor is the thread's durable self and its label.** New items match
  against an LLM-authored descriptor ("Nabla AI-scribe integration: credentials,
  KT, the Zoho ticket"), not an embedding — transparent, cheap, and it doubles as
  the displayed name. Names are sticky too: the descriptor updates only on a
  material scope shift, not every run.
- **Join is cheap and every-refresh; merge/split is rare and corroboration-gated**
  (the same asymmetry). A single new item never silently merges two threads.
- **Sticky but correctable.** Stickiness preserves early mistakes, so it stays
  falsifiable: human correction (split/re-home an item) plus transparency
  (low-confidence memberships surfaced), and a *manual* re-audit trigger. Because
  the re-audit is human-initiated, its re-partition is expected rather than
  surprise flicker; it refines rather than resets (preserves identity where it
  holds), scopes to one thread by default, and reports its diff. No automatic
  re-partitioning ever.

### Promotion: emergent thread → anchored, at the escalate gate

Promotion rides on the escalate gate; no new mechanism. Escalating one item from
an emergent thread creates a Jira ticket, and the escalated item gets the hard
bidirectional link (Zoho `Associated Jira Issues` ← the new key). Membership is
**ratified at the gate**, not auto-migrated: when the escalated item belongs to a
multi-item emergent thread, the gate shows the members (pre-checked from the LLM
assignment) and the human confirms or prunes which come onto the new anchor. This
is the natural point to harden soft, LLM-assigned membership into a real anchor —
the members were priors, and escalation is already a gate.

- **Confirmed members re-home onto the anchor.** Those that can hold a hard link
  (another Zoho ticket) get the key written in; those that cannot (Slack, email)
  become soft members of the anchored cluster, still shown transparently as soft.
- **Pruned members fall back to the residual** — not deleted; they just don't join
  this anchor, and can join or seed another thread later.
- **The emergent thread dissolves** into the anchored cluster and the residual
  shrinks — the residual draining into the graph over time.
- **Solo escalations skip ratification** — no emergent threadmates, no extra step,
  so friction is proportional to the value.

### Build phasing: anchored walking-skeleton first

**Decided.** The design above is complete; the build lands in phases so the
invariants and the value get a live-test before the stateful machinery. Phase 1
is anchored-only, stateless, recomputable. Emergent durable threads (the stateful
part) and promotion are deferred.

**Decided (invocation).** jg's first result-returning LLM call is a headless
`claude -p --output-format json` subprocess, not the Anthropic SDK. It reuses the
existing Claude CLI auth (no API key, no new keyring secret) and matches jg's
"claude on tap" identity. Latency is acceptable because the call is async and
never blocks the render; jg validates the JSON itself and fails soft.

Phase 1 (anchored clustering):

- **Sources are only what `gather_flow` already has** — Jira tickets/sessions,
  PRs, Zoho involved-tickets. Slack and email are not wired into jg's gather yet,
  so the full Nabla collapse (Zoho + Slack + email as one thread) is explicitly
  *not* a phase-1 outcome — it needs emergent clustering **and** those sources.
- **The deterministic backbone is nearly free** — jg already extracts every
  authored edge: PR branch → key (`reconcile.extract_key`), Zoho `Associated Jira
  Issue Keys` → key (`InvolvedTicket.jira_keys`), pane `@jg_key` → key.
- **The LLM assigns only the loose residual** — Zoho tickets with no linked key,
  PRs whose branch names no key — to an *existing* anchor (one of my open Jira
  tickets) or leaves them unanchored. Bounded classification against N known
  anchors; emergent grouping of the residual is phase 2.
- **Invariants enforced in code, not prose.** LLM edges carry `kind="llm"` and the
  render path has no branch that can print them as "linked" — they always show
  reason + confidence. An LLM edge can only *assign a loose item to one anchor*; it
  structurally cannot merge two anchors (merge is phase 2/3). The floor renders
  first; `enrich()` runs in a worker and re-groups a beat later; a slow/absent/
  malformed LLM run degrades to backbone-only.
- **Caching:** `~/.cache/jg/clusters.json`, keyed on a digest of input item-ids +
  statuses, so an unchanged flow reuses the verdict — no per-refresh call, no
  flicker.

Module shape: a pure, tested `src/jg/cluster.py` (`build_backbone`, the
prior/asymmetry merge, edge/cluster types) plus an async `enrich()` adapter wired
into `gather_flow` as an overlay. Tests cover authored-vs-mention weighting, the
merge asymmetry (LLM never overrides an authored edge, never merges anchors), and
fail-soft on malformed JSON — no live LLM in tests.

Phase-1 done means: loose items the LLM assigns to a shared existing ticket render
grouped under it with visible reason + confidence; a guess reads as `grouped: both
mention Nabla (0.6)`, never a hard link; unanchored residual shows honestly as
ungrouped; killing the `claude` binary still renders the flow from the backbone.

Deferred to phase 2/3: emergent durable-thread objects, stickiness, manual
re-audit, promotion + ratification at the escalate gate, Slack/email sources,
split/merge correction UI.

**Shipped since:** phase-1 anchored clustering (`cluster.py`), incoming triage
floor + LLM judge over email **and** Slack (`triage.py`), Gmail + Slack ingestion
(`gmail.py`/`slack.py`), and **phase-2 emergent durable threads** (`threads.py`):
incremental join over the unanchored residual, LLM-authored descriptors, persisted
to `~/.cache/jg/threads.json`, join+spawn only. Still deferred: merge/split of
established threads, descriptor updates, promotion+ratification at the escalate
gate, manual re-audit, staleness cleanup, and the triage correction loop
(rescue/suppress → durable floor rule).

## Incoming triage (second LLM investment)

The incoming bucket is mostly noise — the real snapshot ran ~5 signal in ~30
items. Triage separates actionable signal from noise, governed by one constraint
the other LLM work does not share: **the error costs are asymmetric.** A false
positive (a newsletter slips through) is mildly annoying; a false negative (a
colleague's real ask buried as noise) is catastrophic — miss real work once and
you stop trusting the filter. So the rule is *when unsure, surface* — the
friction goes on suppression, the way it went on merges in clustering.

The shape is the familiar floor-plus-LLM:

- **The deterministic floor handles the bulk of the noise.** Hard markers carry
  it: List-Unsubscribe / bulk headers (newsletters, marketing), known-noise
  senders (Grafana alerts, Jira/Confluence/calendar bots, `noreply@`) → suppress.
  Strong-signal markers → surface as actionable: a direct @mention, a
  review-request-of-me, an assignment, a DM from a colleague, a reply to my own
  thread. All rules, user-editable.
- **The LLM judges only the ambiguous middle** — a human message with no clear
  marker. Biased conservative (unsure → actionable, never suppress). Classified
  once on arrival and cached, so a verdict does not flicker between refreshes.
- **Suppressed is never deleted — it collapses** into an expandable "N filtered"
  line, so every false negative is recoverable. That is what lets an aggressive
  filter be trusted.
- **Corrections feed the floor first.** Rescuing a suppressed item or suppressing
  a surfaced one becomes a deterministic rule wherever it is sender/structure-
  based ("never surface Grafana", "always surface Jaimon"); only content-based
  ambiguity that cannot be reduced to a rule stays with the LLM. The floor grows
  from corrections and the LLM's share shrinks over time.

### Two-way, and clustering carries the FYI

The bucket is binary — actionable vs suppressed — not three-way. There is a real
middle (your own meeting notes, activity on a ticket you follow, a resolved
alert), but a standalone FYI tier becomes a second inbox you feel obligated to
scan, recreating the noise problem one level up. The distinguishing test is
"would you ever go looking for it?" — and where you would look is inside the
work-thread. So relevant FYI surfaces through **clustering**, as context on its
thread (the Nabla meeting notes on the Nabla thread, the CH-688 activity on the
Billing thread), pulled on demand rather than pushed daily. FYI that maps to no
thread (a generic digest, a self-healed alert) is treated as noise: suppressed
and expandable. The triage bucket stays binary; clustering does the FYI work.

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
- Clustering is anchored-first with emergent clustering on the residual.
- Cluster edges are falsifiable priors weighted by how deliberately they were
  authored; assignment is tentative and reversible, merging demands corroboration.
- Emergent-thread stability: durable thread objects + incremental join; sticky
  LLM-authored descriptors; human correction plus a manual, identity-preserving,
  diff-reporting re-audit; no automatic re-partitioning.
- Promotion rides the escalate gate with membership ratified (pre-checked,
  prunable) at the gate; solo escalations skip it; pruned members return to the
  residual.
- Clustering builds in phases: phase 1 is anchored-only, stateless, over jg's
  existing sources (Jira/PR/Zoho); emergent durable threads and promotion follow.
  The LLM call is a headless `claude -p --output-format json` subprocess (existing
  Claude CLI auth, no API key), async and fail-soft.
- Triage is governed by asymmetric error cost: when unsure, surface. It is a
  two-way filter (actionable vs suppressed-expandable), not three-way; relevant
  FYI surfaces via clustering as thread-context, not a middle inbox tier;
  corrections feed the deterministic floor first, LLM only for irreducible
  content ambiguity.

Working defaults (revisable):

- The 3-panel master-detail dashboard is the current substrate — held because
  nothing has beaten it, not as a foundation. Revision trigger: the flow-home
  proving the master-detail grouping wrong in use.

Open:

- Exact resume UX: how the cold/fresh-brief choice is presented.
- How much reconcile is auto-detected on refresh vs. computed on demand.
- The unbuilt gate edges: task → plan (reality revises the plan) and
  task → communication (close the loop out).
