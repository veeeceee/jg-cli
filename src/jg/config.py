"""Config loading + saving for jg.

Config file lives at ~/.config/jg/config.toml. Holds non-secret values:
OAuth client_id, scopes, default cloudId, default project, tmux preferences,
and per-project pointers to canonical artifacts (plan/docs/research/memory).
Secrets (client_secret, access_token, refresh_token) live in macOS Keychain.

Layout: connection state jg manages itself lives under `[atlassian]`; everything
from `[ui]` down is user-authored. Old flat top-level keys (client_id,
default_cloud_id, default_cloud_url, default_project) are still read for
back-compat and migrated to `[atlassian]` on the next save().
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

CONFIG_DIR = Path.home() / ".config" / "jg"
CONFIG_PATH = CONFIG_DIR / "config.toml"

# Prepended verbatim on every save(). tomli_w can't emit comments, so the
# "don't hand-edit" warning has to be written as a raw header.
CONFIG_HEADER = (
    "# jg config — https://github.com/veeeceee/jg-cli\n"
    "# [atlassian] is managed by `jg auth login`; hand-edit only if you know why.\n"
    "# Everything from [ui] down is yours to edit.\n\n"
)

DEFAULT_REDIRECT_URI = "http://localhost:9876/callback"

DEFAULT_SCOPES = [
    "read:jira-work",
    "write:jira-work",
    "read:jira-user",
    # Agile (Jira Software) — required for sprint/backlog moves via the Agile API
    "read:sprint:jira-software",
    "write:sprint:jira-software",
    "write:backlog:jira-software",
    "read:board-scope:jira-software",
    "offline_access",
]


@dataclass
class AtlassianConfig:
    """Connection state jg manages on the user's behalf (`jg auth login`)."""
    client_id: str = ""
    redirect_uri: str = DEFAULT_REDIRECT_URI
    scopes: list[str] = field(default_factory=lambda: DEFAULT_SCOPES.copy())
    cloud_id: str = ""
    cloud_url: str = ""  # e.g. https://your-org.atlassian.net
    default_jira_project: str = ""  # Jira project KEY (e.g. "CH") — not a [[projects]] block


@dataclass
class TmuxConfig:
    enabled: bool = True
    split: str = "horizontal"  # "horizontal" | "vertical" | "window"
    new_session_if_outside: bool = True


@dataclass
class AIConfig:
    claude_path: str = "claude"
    default_command: str = "/issue"  # what ch ai <KEY> auto-runs


@dataclass
class UIConfig:
    theme: str = "jg-pink"  # registered Textual theme
    repo_root: str = "~/DeveloperLocal"  # fallback search root for un-mapped repos
    editor_command: str = "nvim"  # used when opening repos in tmux
    notifications: bool = True  # macOS notifications on new PRs/tickets


@dataclass
class FieldsConfig:
    """Custom-field id mappings for an instance.

    Empty `story_points` = feature disabled (graceful no-op everywhere).
    `story_points_type` controls the write shape:
      - "number" → bare float/int (standard Jira story points)
      - "select" → {"value": "<n>"} single-select option (e.g. a validated 1-4
        points field). Allowed values for a select are fetched live from the
        issue's editmeta at write time, never hardcoded — so a re-scoped option
        list can't silently drift out of sync with the CLI.
    """
    story_points: str = ""
    story_points_type: str = "number"  # "number" | "select"


@dataclass
class ZohoConfig:
    """Zoho Desk connection + identity (support-ticket involvement).

    `client_id` is jg's own self-client; the secret + tokens live in Keychain.
    `agent_emails` are the addresses that mean "involved" (assignee / thread
    participant / body mention / @mention). Resolved to agent ids at runtime."""
    client_id: str = ""
    org_id: str = ""
    department_ids: list[str] = field(default_factory=list)
    agent_emails: list[str] = field(default_factory=list)
    accounts_url: str = "https://accounts.zoho.com"  # US DC; .eu/.in/.com.au for others
    api_base: str = "https://desk.zoho.com/api/v1"

    @property
    def is_setup(self) -> bool:
        return bool(self.client_id and self.org_id)


@dataclass
class GmailConfig:
    """Gmail ingestion via the Gmail API (OAuth, read-only).

    `client_id` is jg's own Google OAuth client (Desktop app); the secret +
    tokens live in Keychain. jg only ever requests `gmail.readonly`. `query` is
    the Gmail search that scopes what lands in the incoming pile — kept
    signal-heavy by default so it doesn't flood before triage exists."""
    client_id: str = ""
    query: str = "is:unread -category:promotions -category:social -category:forums newer_than:7d"
    max_results: int = 40

    @property
    def is_setup(self) -> bool:
        return bool(self.client_id)


@dataclass
class SlackConfig:
    """Slack ingestion. The user token lives in Keychain (slack.user_token);
    `channels` are the channel IDs you follow (Slack has no first-class 'follow',
    so it's an explicit list). DMs + @mentions are found without config."""
    channels: list[str] = field(default_factory=list)


@dataclass
class TriageConfig:
    """Incoming triage rules (phase 1: deterministic floor). Substring matches on
    the From header. `my_addresses` marks direct-to-me as actionable; jg also
    auto-includes the authenticated Gmail address at runtime. All user-editable —
    the floor grows from corrections."""
    my_addresses: list[str] = field(default_factory=list)
    signal_senders: list[str] = field(default_factory=list)   # email From → actionable
    noise_senders: list[str] = field(default_factory=list)    # email From → suppressed
    signal_channels: list[str] = field(default_factory=list)  # Slack channel → actionable
    noise_channels: list[str] = field(default_factory=list)   # Slack channel → suppressed


@dataclass
class RoadmapConfig:
    """Portfolio/roadmap altitude. `jql` selects which epics appear; empty means
    derive from the default Jira project (see roadmap.effective_jql)."""
    jql: str = ""


@dataclass
class ProjectDocs:
    """Pointers to a project's canonical artifacts. jg surfaces these; it never
    owns or edits them. Paths are relative to the project's local_path (or the
    resolved repo path) unless absolute."""
    plan: str = ""                                    # north-star doc path
    dirs: list[str] = field(default_factory=list)     # doc dirs/globs to list
    memory: list[str] = field(default_factory=list)   # MEMORY.md slugs (~/.claude/.../memory/)
    confluence: list[str] = field(default_factory=list)  # space/page keys (Phase 1: links only)

    def is_empty(self) -> bool:
        return not (self.plan or self.dirs or self.memory or self.confluence)


@dataclass
class Project:
    """A logical grouping of a JQL filter + repos + a primary local path.

    Lets the dashboard show "what's happening with <project-name>" by aggregating
    matching tickets, PRs, and repos. `repo_paths` overrides the heuristic
    mapping from repo name to local clone (e.g. when myorg/my-service
    lives at ~/code/myservice, not ~/code/my-service)."""
    name: str
    jql: str = ""           # JQL fragment — joined with current view's filter
    repos: list[str] = field(default_factory=list)  # ["owner/name", ...]
    local_path: str = ""    # primary path (used by project-level e/s/A actions)
    repo_paths: dict[str, str] = field(default_factory=dict)  # {"owner/name": "/abs/path"}
    board_id: str = ""      # Jira board id (numeric, as string) — enables sprint move via `m`
    docs: ProjectDocs = field(default_factory=ProjectDocs)  # canonical-artifact pointers
    research_dir: str = ""  # override; empty = central default (see projectdocs.research_path)

    def matches_repo(self, name_with_owner: str) -> bool:
        return name_with_owner in self.repos

    def resolve_repo_path(self, name_with_owner: str) -> str | None:
        """Per-repo override > primary local_path > None."""
        if name_with_owner in self.repo_paths:
            return self.repo_paths[name_with_owner]
        # If only one repo is in this project, the primary local_path applies.
        if len(self.repos) == 1 and self.repos[0] == name_with_owner:
            return self.local_path or None
        return None


@dataclass
class Config:
    atlassian: AtlassianConfig = field(default_factory=AtlassianConfig)
    tmux: TmuxConfig = field(default_factory=TmuxConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    fields: FieldsConfig = field(default_factory=FieldsConfig)
    roadmap: RoadmapConfig = field(default_factory=RoadmapConfig)
    zoho: ZohoConfig = field(default_factory=ZohoConfig)
    gmail: GmailConfig = field(default_factory=GmailConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    triage: TriageConfig = field(default_factory=TriageConfig)
    projects: list[Project] = field(default_factory=list)

    # ── Back-compat delegating properties ──────────────────────────────────
    # Connection keys moved under [atlassian], but callers (auth/api/tui/commands)
    # still read/write them flat. These keep every caller untouched.
    @property
    def client_id(self) -> str:
        return self.atlassian.client_id

    @client_id.setter
    def client_id(self, v: str) -> None:
        self.atlassian.client_id = v

    @property
    def redirect_uri(self) -> str:
        return self.atlassian.redirect_uri

    @redirect_uri.setter
    def redirect_uri(self, v: str) -> None:
        self.atlassian.redirect_uri = v

    @property
    def scopes(self) -> list[str]:
        return self.atlassian.scopes

    @scopes.setter
    def scopes(self, v: list[str]) -> None:
        self.atlassian.scopes = v

    @property
    def default_cloud_id(self) -> str:
        return self.atlassian.cloud_id

    @default_cloud_id.setter
    def default_cloud_id(self, v: str) -> None:
        self.atlassian.cloud_id = v

    @property
    def default_cloud_url(self) -> str:
        return self.atlassian.cloud_url

    @default_cloud_url.setter
    def default_cloud_url(self, v: str) -> None:
        self.atlassian.cloud_url = v

    @property
    def default_project(self) -> str:
        return self.atlassian.default_jira_project

    @default_project.setter
    def default_project(self, v: str) -> None:
        self.atlassian.default_jira_project = v

    # ── Lookups ────────────────────────────────────────────────────────────
    def project_by_name(self, name: str) -> Project | None:
        for p in self.projects:
            if p.name.lower() == name.lower():
                return p
        return None

    def project_for_repo(self, name_with_owner: str) -> Project | None:
        for p in self.projects:
            if p.matches_repo(name_with_owner):
                return p
        return None

    def resolve_repo_path(self, name_with_owner: str) -> str | None:
        for p in self.projects:
            path = p.resolve_repo_path(name_with_owner)
            if path:
                return path
        return None

    @property
    def is_setup(self) -> bool:
        return bool(self.atlassian.client_id)

    # ── Persistence ──────────────────────────────────────────────────────────
    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data: dict = {
            "atlassian": {
                "client_id": self.atlassian.client_id,
                "redirect_uri": self.atlassian.redirect_uri,
                "scopes": self.atlassian.scopes,
                "cloud_id": self.atlassian.cloud_id,
                "cloud_url": self.atlassian.cloud_url,
                "default_jira_project": self.atlassian.default_jira_project,
            },
            "tmux": {
                "enabled": self.tmux.enabled,
                "split": self.tmux.split,
                "new_session_if_outside": self.tmux.new_session_if_outside,
            },
            "ai": {
                "claude_path": self.ai.claude_path,
                "default_command": self.ai.default_command,
            },
            "ui": {
                "theme": self.ui.theme,
                "repo_root": self.ui.repo_root,
                "editor_command": self.ui.editor_command,
                "notifications": self.ui.notifications,
            },
            "fields": {
                "story_points": self.fields.story_points,
                "story_points_type": self.fields.story_points_type,
            },
            "roadmap": {
                "jql": self.roadmap.jql,
            },
            "zoho": {
                "client_id": self.zoho.client_id,
                "org_id": self.zoho.org_id,
                "department_ids": self.zoho.department_ids,
                "agent_emails": self.zoho.agent_emails,
                "accounts_url": self.zoho.accounts_url,
                "api_base": self.zoho.api_base,
            },
            "gmail": {
                "client_id": self.gmail.client_id,
                "query": self.gmail.query,
                "max_results": self.gmail.max_results,
            },
            "slack": {
                "channels": self.slack.channels,
            },
            "triage": {
                "my_addresses": self.triage.my_addresses,
                "signal_senders": self.triage.signal_senders,
                "noise_senders": self.triage.noise_senders,
                "signal_channels": self.triage.signal_channels,
                "noise_channels": self.triage.noise_channels,
            },
        }
        if self.projects:
            data["projects"] = [self._project_to_dict(p) for p in self.projects]
        with open(CONFIG_PATH, "wb") as f:
            f.write(CONFIG_HEADER.encode())
            tomli_w.dump(data, f)

    @staticmethod
    def _project_to_dict(p: Project) -> dict:
        """Serialize a Project. Empty `docs`/`research_dir` are omitted so the
        file stays lean — no clutter of empty sub-tables."""
        d: dict = {
            "name": p.name,
            "jql": p.jql,
            "repos": p.repos,
            "local_path": p.local_path,
            "repo_paths": p.repo_paths,
            "board_id": p.board_id,
        }
        if p.research_dir:
            d["research_dir"] = p.research_dir
        if not p.docs.is_empty():
            docs: dict = {}
            if p.docs.plan:
                docs["plan"] = p.docs.plan
            if p.docs.dirs:
                docs["dirs"] = p.docs.dirs
            if p.docs.memory:
                docs["memory"] = p.docs.memory
            if p.docs.confluence:
                docs["confluence"] = p.docs.confluence
            d["docs"] = docs
        return d

    @classmethod
    def load(cls) -> Config:
        if not CONFIG_PATH.exists():
            return cls()
        with open(CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)

        # [atlassian] with fallback to legacy flat top-level keys.
        atl = data.get("atlassian", {})
        atlassian = AtlassianConfig(
            client_id=atl.get("client_id", data.get("client_id", "")),
            redirect_uri=atl.get("redirect_uri", data.get("redirect_uri", DEFAULT_REDIRECT_URI)),
            scopes=atl.get("scopes", data.get("scopes", DEFAULT_SCOPES.copy())),
            cloud_id=atl.get("cloud_id", data.get("default_cloud_id", "")),
            cloud_url=atl.get("cloud_url", data.get("default_cloud_url", "")),
            default_jira_project=atl.get("default_jira_project", data.get("default_project", "")),
        )

        tmux_raw = data.get("tmux", {})
        ai_raw = data.get("ai", {})
        ui_raw = data.get("ui", {})
        fields_raw = data.get("fields", {})
        roadmap_raw = data.get("roadmap", {})
        zoho_raw = data.get("zoho", {})
        gmail_raw = data.get("gmail", {})
        slack_raw = data.get("slack", {})
        triage_raw = data.get("triage", {})
        return cls(
            atlassian=atlassian,
            tmux=TmuxConfig(
                enabled=tmux_raw.get("enabled", True),
                split=tmux_raw.get("split", "horizontal"),
                new_session_if_outside=tmux_raw.get("new_session_if_outside", True),
            ),
            ai=AIConfig(
                claude_path=ai_raw.get("claude_path", "claude"),
                default_command=ai_raw.get("default_command", "/issue"),
            ),
            ui=UIConfig(
                theme=ui_raw.get("theme", "jg-pink"),
                repo_root=ui_raw.get("repo_root", "~/DeveloperLocal"),
                editor_command=ui_raw.get("editor_command", "nvim"),
                notifications=ui_raw.get("notifications", True),
            ),
            fields=FieldsConfig(
                story_points=fields_raw.get("story_points", ""),
                story_points_type=fields_raw.get("story_points_type", "number"),
            ),
            roadmap=RoadmapConfig(jql=roadmap_raw.get("jql", "")),
            zoho=ZohoConfig(
                client_id=zoho_raw.get("client_id", ""),
                org_id=zoho_raw.get("org_id", ""),
                department_ids=list(zoho_raw.get("department_ids") or []),
                agent_emails=list(zoho_raw.get("agent_emails") or []),
                accounts_url=zoho_raw.get("accounts_url", "https://accounts.zoho.com"),
                api_base=zoho_raw.get("api_base", "https://desk.zoho.com/api/v1"),
            ),
            gmail=GmailConfig(
                client_id=gmail_raw.get("client_id", ""),
                query=gmail_raw.get("query", GmailConfig().query),
                max_results=int(gmail_raw.get("max_results", 40)),
            ),
            slack=SlackConfig(channels=list(slack_raw.get("channels") or [])),
            triage=TriageConfig(
                my_addresses=list(triage_raw.get("my_addresses") or []),
                signal_senders=list(triage_raw.get("signal_senders") or []),
                noise_senders=list(triage_raw.get("noise_senders") or []),
                signal_channels=list(triage_raw.get("signal_channels") or []),
                noise_channels=list(triage_raw.get("noise_channels") or []),
            ),
            projects=[cls._project_from_dict(p) for p in (data.get("projects") or [])],
        )

    @staticmethod
    def _project_from_dict(p: dict) -> Project:
        docs_raw = p.get("docs") or {}
        return Project(
            name=p.get("name", "?"),
            jql=p.get("jql", ""),
            repos=list(p.get("repos") or []),
            local_path=p.get("local_path", ""),
            repo_paths=dict(p.get("repo_paths") or {}),
            board_id=str(p.get("board_id") or ""),
            docs=ProjectDocs(
                plan=docs_raw.get("plan", ""),
                dirs=list(docs_raw.get("dirs") or []),
                memory=list(docs_raw.get("memory") or []),
                confluence=list(docs_raw.get("confluence") or []),
            ),
            research_dir=p.get("research_dir", ""),
        )
