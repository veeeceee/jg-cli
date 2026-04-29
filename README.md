# ch — Jira + GitHub TUI dashboard

Fast, mechanical Jira + GitHub operations from the terminal — with a Textual TUI dashboard and a Claude Code bridge for inferential work.

## Why

The MCP-server-via-Claude approach is great for inference (drafting test cases, summarizing tickets, picking transitions) but slow for mechanical operations. `ch` splits the layers:

- **Mechanical** (sub-second, no LLM) — list, transition, assign, comment, search, dashboard
- **Inferential** (LLM, latency justified) — `ch ai <KEY>` opens a tmux pane with a Claude Code session pre-loaded with ticket context; `ch ai brainstorm` for new-ticket ideation; the Claude MCP server excels at drafting detailed, well-structured tickets from a brief description

Same Atlassian OAuth as the Claude MCP server — no API token needed. GitHub via `gh` CLI.

## Features

**TUI dashboard** (`ch dashboard`)
- Kanban view (sprint / backlog / all / recent tabs) with per-column semantic gradients
- Code panel: My PRs, Waiting on Review, Repos list
- Projects panel scopes both panels by JQL filter + repo list
- Responsive: 3-tier layout (wide / medium / narrow drill-down) adapts to terminal width
- `A` key on any ticket/PR/repo opens a Claude Code tmux pane with context pre-loaded
- `E` key opens an editor (nvim by default) in a tmux split at the repo's local path
- Focus-driven refresh: switching back to the dashboard pane auto-refreshes tickets
- macOS notifications for new PRs and incoming review requests

**CLI commands**
| Command | What it does |
|---|---|
| `ch sprint` | Rich table of current sprint tickets |
| `ch view <KEY>` | Full ticket detail |
| `ch transition <KEY> <status>` | Fuzzy-match transition |
| `ch assign <KEY> @me\|user` | Assign ticket |
| `ch comment <KEY>` | Add comment (`-` for stdin, omit for `$EDITOR`) |
| `ch edit <KEY>` | Edit priority, labels, components, fix-version, summary |
| `ch link <FROM> <type> <TO>` | Create issue link |
| `ch create [-i]` | Create ticket (interactive mode with `-i`) |
| `ch search "<jql>"` | Run a JQL search |
| `ch testcases <KEY>` | View/edit test cases (ADF custom field) |
| `ch pr list/view/review` | List / view / review PRs via `gh` |
| `ch ai <KEY>` | Open Claude Code tmux pane for a ticket |
| `ch ai brainstorm` | Open brainstorm session with project context |
| `ch ai standup` | Generate standup summary |
| `ch auth setup/login/logout/status` | Atlassian OAuth management |

## Requirements

- Python 3.13+
- [`uv`](https://github.com/astral-sh/uv)
- [`gh`](https://cli.github.com/) (GitHub CLI, authenticated)
- tmux (for AI pane features)
- macOS (notifications use `osascript`; other platforms silently no-op)

## Setup

### 1. Install

```bash
# Editable install — source edits take effect immediately
uv tool install --editable /path/to/ch-cli
```

### 2. Register an Atlassian OAuth app

1. Go to [developer.atlassian.com](https://developer.atlassian.com) → Create app
2. Add OAuth 2.0 (3LO) with callback URL `http://localhost:9876/callback`
3. Add scopes: `read:jira-work`, `write:jira-work`, `read:jira-user`, `offline_access`
4. Copy the Client ID

### 3. Configure

```bash
ch auth setup    # walks through OAuth app registration + first login
```

This writes `~/.config/ch/config.toml`. Secrets (tokens) go to macOS Keychain.

### 4. Add projects (optional but recommended)

Edit `~/.config/ch/config.toml`:

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
ch dashboard          # open TUI
uv run ch dashboard   # run from source without installing
```

## Dashboard keybindings

```
h/l ←→        cycle Projects → Kanban → Code (or use arrow keys)
j/k ↑↓        navigate within list
enter         open detail modal
1/2/3/4       Kanban tabs: sprint / backlog / all / recent
[/]           cycle tabs of focused panel (Kanban or Code)
tab           swap Kanban ⇄ Code (medium/narrow widths)
A             Claude Code pane: /issue (ticket) · /review (PR) · cwd (repo)
E             Editor pane at repo/ticket directory
B             Brainstorm new tickets with project context
t / a / c     Transition / assign / comment focused ticket
o             Open in browser
/             Filter cards
r             Refresh
?             Help
q             Quit
ctrl+p        Command palette (includes theme cycle)
```

**Ticket detail modal:** `t a c · e summary · d description · T tests · p priority · l labels · o A E`

## Development

```bash
uv sync                          # install deps including dev extras
uv run pytest -q                 # run tests
uv run ruff check src/           # lint
uv run textual run src/ch/tui.py # run TUI with Textual devtools
```

## Known constraints

- Single Atlassian site + single GitHub identity (no multi-account)
- Cold start ~1–2 s (Python + Textual import + first network round-trips)
- macOS-only notifications (`osascript`); other platforms silently no-op
- Requires `set -s focus-events on` in tmux for auto-refresh on pane switch

## License

MIT — see [LICENSE](LICENSE).
