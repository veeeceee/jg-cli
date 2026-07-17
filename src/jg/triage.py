"""Incoming triage — separate actionable signal from noise (phase 1: the floor).

The deterministic floor handles the bulk of the noise; the LLM (next iteration)
will judge only the ambiguous middle. Governed by asymmetric error cost: a false
positive (a newsletter slips through) is mildly annoying; a false negative (a
colleague's real ask buried) is catastrophic — so the rule is **when unsure,
surface**. The friction goes on suppression.

Pure and fully tested — no I/O. Applied to email only; PRs and Zoho tickets are
pre-filtered signal and bypass triage entirely. See docs/work-model.md →
"Incoming triage".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Verdict(StrEnum):
    ACTIONABLE = "actionable"  # surface
    SUPPRESSED = "suppressed"  # collapse into the expandable "N filtered" line
    UNSURE = "unsure"          # surface for now; the LLM judges these later


@dataclass
class TriageResult:
    verdict: Verdict
    reason: str

    @property
    def surfaced(self) -> bool:
        # "when unsure, surface" — only a hard SUPPRESSED verdict hides an item.
        return self.verdict is not Verdict.SUPPRESSED


# Code-forge notifications are bulk-headered but often real signal (a reply, a
# review comment) — never hard-suppressed; handed to the LLM to judge by content.
_FORGE = ("github.com", "gitlab.com", "bitbucket.org")
# Automated local-parts that (absent a direct-to-me signal) mean machine mail.
_AUTOMATED = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "bounce", "notifications")


def _addr(raw_from: str) -> str:
    """The bare address out of a From header, lowercased."""
    m = re.search(r"<([^>]+)>", raw_from or "")
    return (m.group(1) if m else (raw_from or "")).strip().lower()


def classify(
    *,
    sender: str,
    to_cc: str,
    is_bulk: bool,
    my_addresses: list[str],
    noise_senders: list[str],
    signal_senders: list[str],
) -> TriageResult:
    """Classify one message from its headers + user rules. Order matters: the
    strong-signal overrides come first so a colleague's bulk-headered mail is
    never suppressed."""
    frm = (sender or "").lower()
    addr = _addr(sender)
    haystack = (to_cc or "").lower()

    # 1. A sender you've marked signal → actionable (explicit override, even bulk).
    if any(s.lower() in frm for s in signal_senders if s):
        return TriageResult(Verdict.ACTIONABLE, "signal sender")
    # 2. A sender you've marked noise → suppress.
    if any(n.lower() in frm for n in noise_senders if n):
        return TriageResult(Verdict.SUPPRESSED, "noise sender")
    # 3. Code-forge notification → ambiguous (reply vs star); let the LLM read it.
    if any(f in addr for f in _FORGE):
        return TriageResult(Verdict.UNSURE, "code-forge notification")
    # 4. Bulk / automated → suppress (newsletters, alerts, marketing). This MUST
    #    come before the addressed-to-me check: bulk mail is sent To: me too, so
    #    "my address in To/Cc" is not a signal on its own.
    if is_bulk or any(a in addr for a in _AUTOMATED):
        return TriageResult(Verdict.SUPPRESSED, "bulk / automated")
    # 5. A non-bulk message addressed to me → actionable (a real direct message).
    if my_addresses and any(a.lower() in haystack for a in my_addresses if a):
        return TriageResult(Verdict.ACTIONABLE, "addressed to you")
    # 6. Human-looking message, no clear marker → the ambiguous middle; surface.
    return TriageResult(Verdict.UNSURE, "no clear marker")
