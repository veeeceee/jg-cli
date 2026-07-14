# jg — Jira + GitHub TUI dashboard

Fast Python+Textual dashboard for Jira (Atlassian Cloud) + GitHub (`gh` CLI), with a Claude Code bridge for inferential work. Built to bypass LLM latency for mechanical operations (list, transition, assign, comment) while keeping the AI on tap via tmux panes.

## Why this exists

The MCP-server-via-Claude approach is great for inference (drafting test cases, summarizing tickets, picking transitions) but slow for mechanical ops. This tool splits the layers:

- **Mechanical** (sub-second, no LLM) — `jg sprint`, `jg view`, `jg transition`, `jg comment`, `jg dashboard`, etc.
- **Inferential** (LLM, latency justified) — `jg ai <KEY>` opens a tmux pane with `claude /issue CH-XXX` pre-loaded; `jg ai brainstorm` pre-loads project context for new-ticket ideation; `A` key on cards/PRs/repos in the TUI does the same.

## Architecture

```
src/jg/
├── cli.py              # click entrypoint, lazy command registration
├── config.py           # ~/.config/jg/config.toml + Project dataclass + per-repo path overrides
├── auth.py             # Atlassian OAuth 2.0 (3LO), tokens in macOS Keychain via `keyring`
├── api.py              # async httpx client for Atlassian REST v3 (uses /rest/api/3/search/jql)
├── adf.py              # Atlassian Document Format render/build (custom fields like Test Cases need ADF)
├── github.py           # `gh` CLI wrapper + `gh api graphql` calls (orgs need viewer.repositories with ownerAffiliations)
├── render.py           # Rich tables, status normalization, gradient/badge helpers
├── gradient.py         # per-character RGB gradient text (lipgloss-style); banner(), gradient_text(), perimeter helpers
├── themes.py           # Custom Textual themes: jg-pink (default), jg-night, jg-paper
├── tmux.py             # spawn/spawn_in_dir for AI panes; idempotent on pane title
├── notifier.py         # macOS notifications via osascript (with dedupe)
├── brainstorm.py       # build_brainstorm_prompt[_multi]: composes context (recent tickets, components, repos); _multi merges per-project sections for "All projects"
├── projectdocs.py      # read-only surface over a project's canonical artifacts (plan/research/docs/memory/confluence); defensive local-fs reads; new_research_file scaffolds dated frontmatter'd notes
├── roadmap.py          # portfolio altitude: fetch epics + batch-tally child status (progress/done/blocked); effective_jql, progress_bar
├── gates.py            # task-typed orchestration gates (declarative GateSpec/GateOption); EPIC_DECOMPOSE + build_decompose_prompt
├── progress.py         # reads/updates ~/.ai/progress.json (scaffolding level per pattern); read_level, record_use
├── workspace.py        # THE dashboard shell (WorkspaceApp): altitude ladder Inbox→Portfolio→Initiative→Task, lenses, actions, gate. `jg dashboard` launches this.
├── tui.py              # component library: GradientPanel, themes glue, all action/detail/gate modals (reused by workspace.py). ChDashboard (old 3-panel shell) is RETIRED/unreferenced here — kept only until its modals are extracted; safe to prune later.
└── commands/           # one click command per file
    ├── auth.py         # jg auth setup/login/logout/status
    ├── sprint.py       # jg sprint
    ├── view.py         # jg view <KEY>
    ├── transition.py   # jg transition <KEY> <status> (fuzzy)
    ├── assign.py       # jg assign <KEY> @me|user
    ├── comment.py      # jg comment <KEY> [text]  ('-' for stdin, omit for $EDITOR)
    ├── edit.py         # jg edit <KEY> --priority/--label/--component/--fixversion/--summary
    ├── link.py         # jg link <FROM> <type> <TO>
    ├── create.py       # jg create [-i interactive]
    ├── search.py       # jg search "<jql>"
    ├── points.py       # jg points <KEY> [VALUE]  (configurable story-points field; select-aware)
    ├── testcases.py    # jg testcases <KEY> [--edit]  (writes customfield_10186 as ADF)
    ├── pr.py           # jg pr list/view/review
    ├── project.py      # jg project [<name>]  (CLI mirror of the project workspace)
    ├── research.py     # jg research <project> [<topic>]  (scaffold dated note + open claude)
    ├── roadmap.py      # jg roadmap  (epics with child progress — portfolio altitude)
    ├── ai.py           # jg ai <KEY> | brainstorm | standup | sprint-review (tmux pane → claude)
    └── dashboard.py    # jg dashboard
```

## Run / develop

```bash
# Install globally (editable — source edits hot-update):
uv tool install --editable .

# After clone or fresh setup:
uv sync                  # install deps (incl. dev extras)
uv run jg dashboard      # run from source without installing
uv run pytest -q         # tests (32 passing — pure helpers)
```

## Dashboard — altitude workspace (`jg dashboard` → `workspace.py`)

The dashboard is an **altitude ladder**, not fixed panels. One navigable spine
with an always-on breadcrumb; the fast actions work at every altitude.

- **Home = Inbox** (cold-start): inbound work with no place in the tree —
  external review requests (GitHub) + tickets assigned to me. Enter services a
  review PR (`/review`) or descends into an assigned task.
- **Ladder**: `p` → Portfolio (all epics w/ progress) → `enter` → Initiative
  (an epic's tasks) → `enter` → Task (detail). `esc`/`h` walks back toward home.
- **Lenses** (`[` / `]`), scoped to altitude: Portfolio = Roadmap / Sprint (my
  open-sprint tasks across all initiatives); Initiative = Board / Mine.
- **Actions at any altitude** on the focused item: `t`/`a`/`c` transition/
  assign/comment, `A` claude (`/issue` on a task, project-dir on an epic,
  `/review` on an inbox PR), `o` browser. `d` on an initiative runs the
  two-stage decompose **gate** (scope → strategy, at your progress.json level).
- Model principle: **the tree holds work you own; the Inbox holds work that
  arrives at you.** A PR is a task's closing state, not a collection — reached
  by navigating to its task (or, for external reviews, from the Inbox).

**Cutover status:** this replaced the old 3-panel shell (see below). Known gaps
not yet ported: full ticket editing (`e`/`d`/`T`/`p`/`l`/`m`), PR-detail/merge
modal, Repos view, `E` editor, `/` filter, story-point chips, command palette,
the Docs/research lens (`R`), and the background macOS notifier.

## TUI dashboard structure (RETIRED — old 3-panel `ChDashboard`, no longer launched)

The app **was** a **master + dual-detail** shell: the Projects panel (master) scoped two sibling detail panes — Kanban (tickets) and Code (PRs + repos). Both detail panes own tab strips for their own sub-views; the top chrome only shows project context. (Retained in `tui.py` for the modals/GradientPanel the workspace reuses; the `ChDashboard` App class itself is unreferenced.)

- **Projects** are config-defined in `[[projects]]` blocks (TOML). Each has a `jql` filter + `repos` list + `local_path` + `repo_paths` (per-repo path overrides — solves cases where `<org>/<repo-slug>` doesn't map cleanly to your local clone dir name).
- **Selecting a project** scopes the kanban JQL + PR list + repo list.
- **Kanban tabs** (sprint / backlog / all / recent) render as a tab strip on the Kanban panel itself — bound to `1/2/3/4` and to `[ ]` when Kanban is focused.
- **Code tabs** (My PRs / Waiting / Repos) bound to `[ ]` when Code is focused (focus-aware bracket cycling).
- **Kanban columns** each have their own semantic gradient (To Do gray, Progress pink→orange, Review cyan→purple, Testing purple→hot pink, Ready green→cyan→purple, Done soft green).
- **All borders** are gradient (`GradientPanel` widget — 4 docked Statics with per-char RGB lerp; outer panels use lipgloss pink→purple→green sweep, kanban columns use their semantic palette).
- **Modals** all use the same lipgloss style: 97% opaque `#000000` backdrop (dashboard fades nearly to black behind), solid `#1c1c1e` box (matches SwiftUI `systemGray6` dark mode) with gradient border, sections inside detail modals use neutral grey/white borders.

## Responsive layout

The dashboard adapts to terminal width via three buckets, applied imperatively in `ChDashboard._apply_width_bucket()` on each width-bucket *crossing* (not every resize event — avoids thrash during drag-resize).

| Bucket | Width | Layout |
|---|---|---|
| **Wide** | ≥140 | Projects (24) + Kanban + Code (40) — all visible |
| **Medium** | 100–139 | Projects (20, compact) + one detail at a time; `Tab` swaps Kanban ⇄ Code; selecting a project auto-flips to Kanban |
| **Narrow** | <100 | Drill-down: Projects fills the screen as a picker (with per-project `★ N awaiting review` badges); selecting drills into the detail; `Tab` swaps Kanban ⇄ Code; `esc` returns to picker |

CSS classes `.-compact`, `.-detail-hidden`, `.-narrow-picker`, `.-narrow-hidden` are toggled by `_apply_width_bucket`. Reactives: `width_bucket`, `active_detail` (kanban|code), `narrow_view` (picker|detail).

## Keybindings

```
Dashboard:
  h/l ←→        cycle Projects → Kanban cols → Sidebar (skips collapsed panels)
  j/k ↑↓        navigate within column / list
  enter         open detail modal (ticket / PR / repo)
  p             project workspace (plan · research · docs & memory · work roll-up)
  g             roadmap: all epics with child progress (portfolio altitude)
  1/2/3/4       Kanban tabs: sprint / backlog / all / recent
  [/]           cycle tabs of focused detail (Kanban or Code)
  tab           swap Kanban ⇄ Code (medium/narrow widths)
  esc           narrow-mode: detail → projects picker
  t/a/c         transition / assign / comment (focused ticket)
  A             claude pane: /issue (ticket) · /review (PR) · cwd-in-repo (repo)
  E             editor pane on focused ticket / PR / repo (ticket shares A's dir cache)
  ctrl+a/e      bypass cached dir, re-open the picker
  B             brainstorm new tickets with project context (in All mode, pick one project or all via modal)
  o             open in browser
  e/s           (Repos tab) editor / shell in tmux split
  /             filter cards
  ctrl+p        command palette (incl. theme cycle)
  r             refresh · ?  help · q  quit

Detail modal:
  t a c · e summary · d description · T tests · p prio · l labels · A claude · E editor · o browser · r refresh · esc close
```

### Ticket → local-dir cache

Pressing `A` (claude) or `E` (editor) on a ticket runs through `_ai_dir_candidates()` (project's `local_path` + each repo's resolved path). If 2+ candidates exist, an `AIDirPickerModal` (fuzzy filter) prompts. The chosen dir is cached on `ChDashboard._ticket_dir_cache` (process-lifetime, not persisted) and shared between `A` and `E` so claudecode.nvim's WebSocket auto-discovery wires up — both panes land in the same cwd. `ctrl+a` / `ctrl+e` bypass the cache and re-open the picker.

For claudecode.nvim, press `E` first so nvim writes its `~/.claude/ide/*.lock` before claude scans for IDEs at launch.

## Project config (TOML)

Layout principle: connection state jg manages itself lives under `[atlassian]`
(written by `jg auth login`); everything from `[ui]` down is user-authored. A
`# don't hand-edit` banner is prepended on every save. Old flat top-level keys
(`client_id`, `default_cloud_id`, `default_cloud_url`, `default_project`) are
still read for back-compat and migrated to `[atlassian]` on next save — so
existing configs keep working. In code, `config.client_id` /
`config.default_cloud_url` / `config.default_project` remain as read/write
delegating properties over `config.atlassian.*`, so callers didn't change.

```toml
[atlassian]                       # jg-managed — written by `jg auth login`
client_id = "..."
cloud_id = "..."
cloud_url = "https://your-org.atlassian.net"
default_jira_project = "CH"       # a Jira project KEY — NOT a [[projects]] block

[ui]
theme = "jg-pink"           # or jg-night, jg-paper, plus all built-in Textual themes
repo_root = "~/DeveloperLocal"
editor_command = "nvim"
notifications = true        # macOS notifications

[ai]
claude_path = "claude"
default_command = "/issue"  # what `jg ai <KEY>` auto-runs

[fields]
# Story-points field id + type, per-instance. Empty/absent = feature off
# (sprint SP column hidden, `jg points` prints a config hint).
story_points = "customfield_10252"   # this org's "Story Points (Validated)"
story_points_type = "select"          # "select" → writes {"value":"N"}; "number" → bare float
# For a "select", allowed options (e.g. 1-4) are fetched live from the issue's
# editmeta and validated before write — an out-of-range value is rejected with
# a clear message, never sent as a raw 400.
# Surfaces when set: `jg sprint` SP column, `jg points` (read/write), TUI kanban
# card chip (◆N next to priority), and the ticket detail modal state line (N pts).

[tmux]
enabled = true
split = "horizontal"         # horizontal | vertical | window
new_session_if_outside = true
# Add `set -s focus-events on` to ~/.tmux.conf so the dashboard auto-refreshes
# when you switch back to its pane (catches Jira/GitHub mutations made
# elsewhere — e.g. Claude creating a ticket via MCP in a sibling pane).
# Without it, the dashboard falls back to a 120s background poll.

[[projects]]
name = "MyProject"
jql = "parent = PROJ-1"            # any JQL fragment, layered on top of view-mode JQL
repos = ["myorg/backend", "myorg/frontend"]
local_path = "~/code/myproject"    # primary, used for project-level e/s/A actions
# research_dir = "~/code/myproject/research"  # optional override; default is
#                                             # ~/DeveloperLocal/research/<slug(name)>

[projects.repo_paths]
"myorg/backend"  = "~/code/backend"
"myorg/frontend" = "~/code/frontend"

[projects.docs]                    # pointers to canonical artifacts jg surfaces (never owns)
plan       = "docs/strategic-plan.md"   # relative to local_path (or absolute)
dirs       = ["docs"]                    # doc dirs listed in the project workspace
memory     = ["project_myproject"]       # MEMORY.md slugs (~/.claude/.../memory/)
confluence = ["MYSPACE"]                 # space/page keys (link-only for now)

[roadmap]                          # portfolio altitude (jg roadmap / `g` in TUI)
jql = ""                           # which epics to show; empty → "project = <default> AND issuetype = Epic"
```

### Project workspace (`p` in the TUI / `jg project <name>`)

`projectdocs.py` reads a project's `[projects.docs]` pointers (all read-only,
defensive — missing paths yield empty results, never raise). `ProjectDetailModal`
(TUI, `p` key) and `commands/project.py` (`jg project <name>`) render the same
four sections: **Plan** (summary of the plan doc), **Research** (newest markdown
in the research dir, a focusable list — enter opens a note in the editor),
**Docs & memory** (doc dirs + resolved memory slugs + Confluence links), and
**Work** (live ticket counts by status via one JQL call). In the modal, `A`
opens Claude scoped to the project, `o` opens Confluence/Jira in the browser.
Memory slugs resolve by globbing `~/.claude/projects/*/memory/<slug>.md`.

### Research write-back (`R` in the workspace / `jg research`)

The mechanical/inferential split applied to research: **jg** creates a dated,
frontmatter'd note (`projectdocs.new_research_file` → `YYYY-MM-DD-<slug>.md` with
`title`/`date`/`project`, idempotent so it never clobbers findings); **Claude**
fills in the body. `R` in the workspace prompts for a topic then spawns Claude on
the file; `jg research <project> <topic>` does the same from the CLI (`-e` opens
the editor instead). Because jg owns the frontmatter, `list_research` resurfaces
every note with a clean title/date next time you open the workspace — that's the
whole write-back loop, no jg-side state.

### Roadmap (`g` in the TUI / `jg roadmap`)

`roadmap.py` is the altitude above the sprint kanban. `effective_jql` picks the
epics (config `[roadmap].jql`, else derived from the default Jira project). One
query lists them; one paginated `parent in (…)` query tallies every child's
status client-side (no N+1). Each epic gets a progress bar, `done` by
statusCategory (folds Done/Resolved/Closed), and a blocked count. `_sort_key`
floats in-progress epics to the top and sinks finished ones. `RoadmapModal` (`g`)
and `commands/roadmap.py` share this; enter/`o` opens an epic in the browser.
(Fix-versions are unused in this Jira — the epic/parent structure is the roadmap.)

### Gated orchestration (`d` on an epic in the roadmap)

jg is a **coordinator**: it never authors work, it orchestrates Claude to — but
every authoring orchestration is *gated* first. `d` on a highlighted epic runs a
**two-stage gate**, both stages blocking:

1. **Scope** (`ScopeGateModal`) — jg surfaces *facts* it already holds (the epic's
   raw child count / done — no threshold, no "container" label) and forces YOU to
   judge whether there's one bounded, sliceable outcome here or to name the real
   scope (a feature, a plan doc, or "reorg the undone tail"). Scope-validity is a
   judgment, not a heuristic — jg shows, you decide. (This stage exists because
   the first live run hit CH-36, a 30+ child master epic; the strategy question
   is meaningless until scope is settled.)
2. **Strategy** (`GateModal`) — renders `gates.EPIC_DECOMPOSE` (vertical-slice /
   risk-first / milestone / horizontal, with tradeoffs + failure modes) at your
   scaffolding level. A one-line reasoning is **required at every level** (even 0).

Only after both does jg bake scope + strategy + reasoning + the failure-mode-to-
guard into the Claude prompt (`build_decompose_prompt`) and spawn it. The gate is
deliberate friction on the *inferential* side only — the mechanical loop stays
sub-second.

The gate is rendered at your **scaffolding level** for the governing pattern,
read from `~/.ai/progress.json` via `progress.read_level` (the same file the
dialectical-coding framework uses — jg is a second client). Level 0 supplies all
options + tradeoffs + failure modes and you pick; level 1-2 withhold detail and
require typed reasoning; level 3 shows nothing and you propose the strategy
yourself. `progress.record_use` bumps uses/lastUsed on orchestration; promotion
(correct++/level++) stays with the reflection flow, the only place a decision's
correctness is actually known. Gate content is declarative (`gates.GATES`) so it
externalizes to config/file when the registry grows past the prototype's one
task type.

## Critical gotchas (caused real bugs)

1. **Atlassian deprecated `/rest/api/3/search`** (Mar 2025) — use POST `/rest/api/3/search/jql` with JSON body and `nextPageToken` pagination.
2. **Custom fields like Test Cases (`customfield_10186`)** require ADF. Plain string and `contentFormat: markdown` both fail with "Operation value must be an Atlassian Document". Build ADF directly via `jg.adf.text_to_adf` or `sections_to_adf`.
3. **`gh repo list` only shows OWNER repos** — for org repos, use `gh api graphql` with `viewer.repositories(ownerAffiliations: [OWNER, ORGANIZATION_MEMBER, COLLABORATOR])`.
4. **`gh api graphql` does NOT substitute `@me`** — only `gh search prs` does. Resolve via `gh api user --jq .login` (cached) before building search queries.
5. **`gh search prs` does NOT support `reviewDecision`** — only `gh pr list` does. We use GraphQL via `gh api graphql` to get it cross-repo.

## Textual gotchas (also caused real bugs)

1. **Don't name a method `_render`** — shadows `Widget._render`, the internal render hook. Use `_redraw`.
2. **Widget IDs must be unique within a parent** — use `classes="..."` for shared styling.
3. **Stored widget references can go stale after `TabbedContent` mounts** — assign `id="..."` and `query_one("#x")` at call time.
4. **Reserved attribute names** on `Widget` — `name`, `id`, `classes`, `styles`, `screen`, `app`, `parent`. Namespace your own (`repo_name`, not `name`).
5. **`run_worker` from `on_mount` can fire before children are query-able** — use `self.call_after_refresh(callback)` for anything that needs `query_one`.
6. **Modal screen `background: transparent` removes the dim overlay** — use `background: <color> 97%` instead for the lipgloss-style "fade behind modal" effect.
7. **Global `Screen { background: ... }` in `App.CSS` clobbers ModalScreen backdrops.** The selector matches every screen including modals, and App-level CSS wins over per-modal `DEFAULT_CSS`. Keep layout rules in the global `Screen` rule but set the dashboard's own background programmatically in `on_mount` (`self.screen.styles.background = ...`) so modals can paint their own overlays.
8. **Textual subclass `BINDINGS = []` does NOT clear inherited bindings.** Even with `inherit_bindings = False`, Textual 8.2 still merges parent BINDINGS into the per-instance map. To truly remove keys (e.g. KanbanScroll dropping HorizontalScroll's left/right scroll-by-cell), pop them from `self._bindings.key_to_bindings` in `__init__` after `super().__init__()`. Private attribute — re-verify on Textual upgrades.

## Where to extend

- New click subcommand → drop a file in `commands/` and register it in `cli.py:_register()`
- New TUI binding → add to `ChDashboard.BINDINGS` + `action_<name>()` method, update `HelpScreen.HELP`
- New ADF custom field → add to the `fields=[...]` list in `_reload()` of TicketDetailModal, render via `jg.adf.render_to_text`, write via `editJiraIssue` with `text_to_adf` or hand-built ADF
- Background notifier on a new event type → add diff logic in `_diff_and_notify_*` in `tui.py`, call `macos_notify(title, message)` from `jg.notifier`

## Tests

Pure helpers tested (`tests/test_adf.py`, `test_render.py`, `test_transition.py`, `test_edit.py`, `test_gradient.py`). Auth flow + REST client are NOT tested with respx yet — open future task.

## Known constraints

- Single Atlassian site + single GitHub identity (no multi-account)
- Cold start ~1-2 sec (Python + Textual import + first network round-trips). For all-day-running app this is fine; for one-shot CLI ops it's the floor.
- macOS-only notifications (osascript). Other platforms silently no-op.
- Token refresh failures show clear "run jg auth login" toast; CLI commands print the same.
