"""Config loading + saving for ch.

Config file lives at ~/.config/ch/config.toml. Holds non-secret values:
OAuth client_id, scopes, default cloudId, default project, tmux preferences.
Secrets (client_secret, access_token, refresh_token) live in macOS Keychain.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

CONFIG_DIR = Path.home() / ".config" / "ch"
CONFIG_PATH = CONFIG_DIR / "config.toml"

DEFAULT_SCOPES = [
    "read:jira-work",
    "write:jira-work",
    "read:jira-user",
    "offline_access",
]


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
    theme: str = "ch-pink"  # registered Textual theme
    repo_root: str = "~/DeveloperLocal"  # fallback search root for un-mapped repos
    editor_command: str = "nvim"  # used when opening repos in tmux
    notifications: bool = True  # macOS notifications on new PRs/tickets


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
    client_id: str = ""
    redirect_uri: str = "http://localhost:9876/callback"
    scopes: list[str] = field(default_factory=lambda: DEFAULT_SCOPES.copy())
    default_cloud_id: str = ""
    default_cloud_url: str = ""  # e.g. https://your-org.atlassian.net
    default_project: str = ""
    tmux: TmuxConfig = field(default_factory=TmuxConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    projects: list[Project] = field(default_factory=list)

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
        return bool(self.client_id)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scopes": self.scopes,
            "default_cloud_id": self.default_cloud_id,
            "default_cloud_url": self.default_cloud_url,
            "default_project": self.default_project,
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
        }
        if self.projects:
            data["projects"] = [
                {
                    "name": p.name,
                    "jql": p.jql,
                    "repos": p.repos,
                    "local_path": p.local_path,
                    "repo_paths": p.repo_paths,
                }
                for p in self.projects
            ]
        with open(CONFIG_PATH, "wb") as f:
            tomli_w.dump(data, f)

    @classmethod
    def load(cls) -> Config:
        if not CONFIG_PATH.exists():
            return cls()
        with open(CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
        tmux_raw = data.get("tmux", {})
        ai_raw = data.get("ai", {})
        ui_raw = data.get("ui", {})
        return cls(
            client_id=data.get("client_id", ""),
            redirect_uri=data.get("redirect_uri", "http://localhost:9876/callback"),
            scopes=data.get("scopes", DEFAULT_SCOPES.copy()),
            default_cloud_id=data.get("default_cloud_id", ""),
            default_cloud_url=data.get("default_cloud_url", ""),
            default_project=data.get("default_project", ""),
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
                theme=ui_raw.get("theme", "ch-pink"),
                repo_root=ui_raw.get("repo_root", "~/DeveloperLocal"),
                editor_command=ui_raw.get("editor_command", "nvim"),
                notifications=ui_raw.get("notifications", True),
            ),
            projects=[
                Project(
                    name=p.get("name", "?"),
                    jql=p.get("jql", ""),
                    repos=list(p.get("repos") or []),
                    local_path=p.get("local_path", ""),
                    repo_paths=dict(p.get("repo_paths") or {}),
                )
                for p in (data.get("projects") or [])
            ],
        )
