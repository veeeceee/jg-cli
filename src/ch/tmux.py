"""tmux integration: spawn panes/windows for ch ai sessions.

Detects whether we're already inside tmux. If so, splits the current window.
If not, opens a new tmux session and attaches.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass

from ch.config import TmuxConfig


@dataclass
class TmuxSpawnResult:
    target: str  # "pane:<id>" or "window:<id>" or "session:<name>"
    inside_tmux: bool


def in_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


def have_tmux() -> bool:
    return subprocess.run(["which", "tmux"], capture_output=True).returncode == 0


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def find_pane_by_title(title: str) -> str | None:
    """Return tmux pane id whose title matches `title`, if any."""
    if not in_tmux():
        return None
    res = _run(["tmux", "list-panes", "-a", "-F", "#{pane_id}|#{pane_title}"])
    if res.returncode != 0:
        return None
    for line in res.stdout.splitlines():
        pane_id, _, pane_title = line.partition("|")
        if pane_title == title:
            return pane_id
    return None


def select_pane(pane_id: str) -> None:
    _run(["tmux", "select-pane", "-t", pane_id])


def spawn(command: str, title: str, config: TmuxConfig) -> TmuxSpawnResult:
    """Spawn a command in tmux. Returns a target identifier."""
    if not have_tmux():
        raise RuntimeError("tmux not installed. Install with: brew install tmux")

    inside = in_tmux()

    # If a pane with this title already exists inside tmux, focus it.
    existing = find_pane_by_title(title) if inside else None
    if existing:
        select_pane(existing)
        return TmuxSpawnResult(target=f"pane:{existing}", inside_tmux=True)

    if inside:
        if config.split == "window":
            res = _run(["tmux", "new-window", "-P", "-F", "#{pane_id}", command])
        elif config.split == "vertical":
            res = _run(["tmux", "split-window", "-v", "-P", "-F", "#{pane_id}", command])
        else:  # horizontal (default)
            res = _run(["tmux", "split-window", "-h", "-P", "-F", "#{pane_id}", command])
        if res.returncode != 0:
            raise RuntimeError(f"tmux split failed: {res.stderr}")
        pane_id = res.stdout.strip()
        _run(["tmux", "select-pane", "-t", pane_id, "-T", title])
        return TmuxSpawnResult(target=f"pane:{pane_id}", inside_tmux=True)

    # Outside tmux: create or attach to a session.
    if not config.new_session_if_outside:
        # Just exec inline.
        os.execvp("/bin/sh", ["/bin/sh", "-c", command])

    session = "ch-ai"
    has_session = _run(["tmux", "has-session", "-t", session]).returncode == 0
    if not has_session:
        _run(["tmux", "new-session", "-d", "-s", session, command])
    else:
        _run(["tmux", "new-window", "-t", session, command])
    # Set title on the latest pane.
    last_pane = _run(["tmux", "display-message", "-t", session, "-p", "#{pane_id}"]).stdout.strip()
    if last_pane:
        _run(["tmux", "select-pane", "-t", last_pane, "-T", title])
    # Attach.
    os.execvp("tmux", ["tmux", "attach-session", "-t", session])


def quote_for_shell(s: str) -> str:
    return shlex.quote(s)


def spawn_in_dir(command: str, cwd: str, title: str, config: TmuxConfig) -> TmuxSpawnResult:
    """Spawn a command in tmux with a specific working directory."""
    cd_part = f"cd {shlex.quote(cwd)} && {command}"
    wrapped = f"/bin/sh -c {shlex.quote(cd_part)}"
    return spawn(wrapped, title=title, config=config)
