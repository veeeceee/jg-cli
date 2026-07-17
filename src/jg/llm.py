"""Shared headless-claude helper for jg's LLM calls (clustering, triage).

jg's LLM path is a `claude -p --output-format json` subprocess — reuses the
existing Claude CLI auth (no API key), async and fail-soft at the call sites.
Kept tiny and dependency-free so the logic layer stays portable.
"""

from __future__ import annotations

import asyncio
import json
import re


class LLMError(Exception):
    pass


async def run_claude(prompt: str, claude_path: str = "claude") -> str:
    """Run `claude -p --output-format json` (prompt on stdin), return the
    assistant's result text out of the JSON envelope. Raises on failure."""
    proc = await asyncio.create_subprocess_exec(
        claude_path, "-p", "--output-format", "json",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(prompt.encode())
    if proc.returncode != 0:
        raise LLMError(f"claude -p exited {proc.returncode}: {err.decode()[:200]}")
    try:
        envelope = json.loads(out.decode())
    except json.JSONDecodeError as e:
        raise LLMError(f"claude -p returned non-JSON: {e}") from e
    return envelope.get("result", "") if isinstance(envelope, dict) else ""


def extract_json_array(text: str) -> list[dict]:
    """Pull a JSON array out of the model's text (tolerating ``` fences / prose)."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    i, j = t.find("["), t.rfind("]")
    if i == -1 or j == -1 or j < i:
        return []
    try:
        data = json.loads(t[i : j + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
