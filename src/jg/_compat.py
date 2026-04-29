"""Runtime patches for upstream library quirks.

Imported for side effects only — call ``apply()`` once, before ``App.run()``.

Patches:

1. **Textual ``LinuxDriver`` UTF-8 input decoder** — Textual 8.2.4's
   ``run_input_thread`` constructs the stdin decoder with the default
   ``errors='strict'``, so any single non-UTF-8 byte arriving on stdin
   (mis-encoded paste, weird tmux escape, broken terminal sequence) raises
   ``UnicodeDecodeError``, kills the input thread, and crashes the app.
   Swap in a lenient decoder (``errors='replace'``) so bad bytes become
   U+FFFD instead of taking the whole TUI down. Re-verify on Textual upgrades.
"""

from __future__ import annotations

import codecs

_applied = False


def apply() -> None:
    global _applied
    if _applied:
        return
    _applied = True

    try:
        from textual.drivers import linux_driver
    except Exception:
        return  # non-Linux/macOS driver path or Textual missing — nothing to patch

    _orig = codecs.getincrementaldecoder

    def _lenient(encoding: str):
        cls = _orig(encoding)

        def factory(errors: str = "replace", **kw):
            return cls(errors=errors, **kw)

        return factory

    linux_driver.getincrementaldecoder = _lenient
