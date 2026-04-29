"""macOS notification helper.

Uses `osascript` (always present on macOS) to emit native notifications.
No-op on non-macOS systems. Notifications are coalesced lightly — if the
same `(title, message)` pair is emitted within `dedupe_seconds`, the second
one is suppressed.

Design choice: we don't use terminal-notifier or pync because they add
external dependencies. osascript is a standard macOS subprocess and is
sufficient for our needs (no clickable actions, just popups).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections import deque

_RECENT: deque[tuple[float, str, str]] = deque(maxlen=64)
DEDUPE_SECONDS = 30


def _osascript_available() -> bool:
    return shutil.which("osascript") is not None


def _esc(s: str) -> str:
    """Escape a string for safe embedding in an osascript double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def notify(title: str, message: str, subtitle: str = "") -> None:
    """Send a macOS notification. Silent fallback if osascript isn't available
    or the same message was sent within DEDUPE_SECONDS."""
    if not _osascript_available():
        return
    now = time.time()
    # Dedupe identical recent notifications.
    while _RECENT and now - _RECENT[0][0] > DEDUPE_SECONDS:
        _RECENT.popleft()
    sig = (title, message)
    for _ts, t, m in _RECENT:
        if (t, m) == sig:
            return
    _RECENT.append((now, title, message))

    parts = [f'display notification "{_esc(message)}"', f'with title "{_esc(title)}"']
    if subtitle:
        parts.append(f'subtitle "{_esc(subtitle)}"')
    script = " ".join(parts)
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass
