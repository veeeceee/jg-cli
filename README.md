# jg — Jira + GitHub TUI dashboard

Fast, mechanical Jira + GitHub operations from the terminal — with a Textual TUI dashboard and a Claude Code bridge for inferential work.

## Why

The MCP-server-via-Claude approach is great for inference (drafting test cases, summarizing tickets, picking transitions) but slow for mechanical operations. `jg` splits the layers:

- **Mechanical** (sub-second, no LLM) — list, transition, assign, comment, search, dashboard
- **Inferential** (LLM, latency justified) — `jg ai <KEY>` opens a tmux pane with a Claude Code session pre-loaded with ticket context; `jg ai brainstorm` for new-ticket ideation; the Claude MCP server excels at drafting detailed, well-structured tickets from a brief description

Same Atlassian OAuth as the Claude MCP server — no API token needed. GitHub via `gh` CLI.

## Features

**TUI dashboard** (`jg dashboard`) — an altitude workspace, not fixed panels
- One navigable ladder with an always-on breadcrumb: **Inbox → Portfolio → Initiative → Task**
- Cold-start **Inbox**: inbound work with no home in your tree — external review requests + tickets assigned to you
- **Lenses** (`[` / `]`) per altitude: Portfolio = Roadmap / Sprint (your sprint tasks across all initiatives); Initiative = Board / Mine
- **Actions at any altitude**: `t`/`a`/`c` transition/assign/comment, `A` claude (`/issue`, project dir, or `/review`), `o` browser
- `d` on an initiative runs the two-stage **decompose gate** (scope → strategy, rendered at your mastery level)
- Model: the tree holds work you own; the Inbox holds work that arrives at you; a PR is a task's closing state

**CLI commands**
| Command | What it does |
|---|---|
| `jg sprint` | Rich table of current sprint tickets |
| `jg view <KEY>` | Full ticket detail |
| `jg transition <KEY> <status>` | Fuzzy-match transition |
| `jg assign <KEY> @me\|user` | Assign ticket |
| `jg comment <KEY>` | Add comment (`-` for stdin, omit for `$EDITOR`) |
| `jg edit <KEY>` | Edit priority, labels, components, fix-version, summary |
| `jg link <FROM> <type> <TO>` | Create issue link |
| `jg create [-i]` | Create ticket (interactive mode with `-i`) |
| `jg search "<jql>"` | Run a JQL search |
| `jg testcases <KEY>` | View/edit test cases (ADF custom field) |
| `jg pr list/view/review` | List / view / review PRs via `gh` |
| `jg project [<name>]` | Project workspace: plan, research, docs & memory, work roll-up (lists projects with no name) |
| `jg research <project> [<topic>]` | Scaffold a dated research note + open Claude to fill it (no topic → list existing) |
| `jg roadmap` | Portfolio altitude: all epics with child progress, blocked flags |
| `jg ai <KEY>` | Open Claude Code tmux pane for a ticket |
| `jg ai brainstorm` | Open brainstorm session with project context |
| `jg ai standup` | Generate standup summary |
| `jg auth setup/login/logout/status` | Atlassian OAuth management |

## Requirements

- Python 3.13+
- [`uv`](https://github.com/astral-sh/uv)
- [`gh`](https://cli.github.com/) (GitHub CLI, authenticated)
- tmux (for AI pane features)
- macOS (notifications use `osascript`; other platforms silently no-op)

## Setup

### 1. Install

```bash
# Install from GitHub (just want to use it)
uv tool install git+https://github.com/veeeceee/jg-cli

# Or, if you're modifying jg-cli itself:
git clone https://github.com/veeeceee/jg-cli
cd jg-cli
uv tool install --editable .
```

### 2. Register an Atlassian OAuth app

1. Go to [developer.atlassian.com](https://developer.atlassian.com) → Create app
2. Add OAuth 2.0 (3LO) with callback URL `http://localhost:9876/callback`
3. Add scopes: `read:jira-work`, `write:jira-work`, `read:jira-user`, `offline_access`
4. Copy the Client ID

### 3. Configure

```bash
jg auth setup    # walks through OAuth app registration + first login
```

This writes `~/.config/jg/config.toml`. Secrets (tokens) go to macOS Keychain.

### 4. Add projects (optional but recommended)

Edit `~/.config/jg/config.toml`:

```toml
[[projects]]
name = "MyProject"
jql = "project = MP"
repos = ["myorg/backend", "myorg/frontend"]
local_path = "~/code/myproject"

[projects.repo_paths]
"myorg/backend" = "~/code/backend"
```

### 5. tmux focus events (for auto-refresh)

Add to `~/.tmux.conf`:

```
set -s focus-events on
```

This lets the dashboard detect when you switch back to it and refresh tickets automatically.

## Running

```bash
jg dashboard          # open TUI
uv run jg dashboard   # run from source without installing
```

## Dashboard keybindings (altitude workspace)

```
enter / l →   descend an altitude (Portfolio → Initiative → Task)
esc / h ←     ascend, walking back toward home (Inbox)
j/k ↑↓        navigate within the current list
i             go to Inbox (home)        p   go to Portfolio (the tree)
[ / ]         cycle lenses at this altitude
t / a / c     transition / assign / comment the focused task
A             claude: /issue (task) · project dir (epic) · /review (inbox PR)
d             decompose an initiative into tasks (two-stage gate)
o             open the focused item in the browser
r             refresh          q   quit
```

Not yet ported from the retired 3-panel dashboard: full ticket editing
(`e`/`d`/`T`/`p`/`l`/`m`), PR-detail/merge modal, Repos view, `E` editor,
`/` filter, story-point chips, command palette, Docs/research lens, and the
background macOS notifier.

## Development

```bash
uv sync                          # install deps including dev extras
uv run pytest -q                 # run tests
uv run ruff check src/           # lint
uv run textual run src/jg/tui.py # run TUI with Textual devtools
```

## Known constraints

- Single Atlassian site + single GitHub identity (no multi-account)
- Cold start ~1–2 s (Python + Textual import + first network round-trips)
- macOS-only notifications (`osascript`); other platforms silently no-op
- Requires `set -s focus-events on` in tmux for auto-refresh on pane switch

## License

MIT — see [LICENSE](LICENSE).
