"""Atlassian Document Format (ADF) builders.

Jira's REST v3 returns and accepts rich text fields (description, comments,
some custom fields) as ADF JSON. This module renders ADF → plain text for
display, and builds ADF from plain text/markdown for writes.

The render side is lossy on purpose — we strip formatting that doesn't matter
in a terminal. The build side handles the most common cases (paragraphs,
bullet lists, headings, bold) and is intentionally simple.
"""

from __future__ import annotations

from typing import Any


def render_to_text(node: dict[str, Any] | None) -> str:
    """Flatten ADF document to plain text. Newlines preserved across blocks."""
    if not node:
        return ""
    return _render_node(node).rstrip()


def _render_node(node: dict[str, Any], depth: int = 0) -> str:
    t = node.get("type", "")
    content = node.get("content", []) or []
    if t == "doc":
        return "\n\n".join(_render_node(c, depth) for c in content if c).strip()
    if t == "paragraph":
        return "".join(_render_node(c, depth) for c in content)
    if t == "heading":
        level = node.get("attrs", {}).get("level", 2)
        text = "".join(_render_node(c, depth) for c in content)
        prefix = "#" * level
        return f"{prefix} {text}"
    if t == "bulletList":
        # Use markdown `-` so Textual's Markdown widget renders as a real list
        # (it treats `•` as plain text and collapses single newlines to spaces).
        return "\n".join(_render_list_item(c, "- ", depth) for c in content)
    if t == "orderedList":
        return "\n".join(_render_list_item(c, f"{i + 1}. ", depth) for i, c in enumerate(content))
    if t == "listItem":
        return "".join(_render_node(c, depth + 1) for c in content)
    if t == "codeBlock":
        body = "".join(_render_node(c, depth) for c in content)
        return f"```\n{body}\n```"
    if t == "blockquote":
        body = "\n".join(_render_node(c, depth) for c in content)
        return "\n".join(f"> {line}" for line in body.splitlines())
    if t == "rule":
        return "---"
    if t == "hardBreak":
        return "\n"
    if t == "table":
        return _render_table(content, depth)
    if t in ("tableRow", "tableCell", "tableHeader"):
        # Should be consumed by _render_table; if reached standalone, flatten.
        return " | ".join(_render_node(c, depth).strip() for c in content)
    if t == "text":
        text = node.get("text", "")
        for mark in node.get("marks") or []:
            mt = mark.get("type")
            if mt == "code":
                text = f"`{text}`"
            elif mt == "strong":
                text = f"**{text}**"
            elif mt == "em":
                text = f"*{text}*"
            elif mt == "link":
                href = mark.get("attrs", {}).get("href", "")
                text = f"[{text}]({href})"
        return text
    if t == "mention":
        return "@" + node.get("attrs", {}).get("text", "?").lstrip("@")
    if t == "inlineCard":
        return node.get("attrs", {}).get("url", "")
    return "".join(_render_node(c, depth) for c in content)


def _render_list_item(item: dict[str, Any], marker: str, depth: int) -> str:
    body = _render_node(item, depth)
    lines = body.splitlines() or [""]
    indent = "  " * depth
    out = [f"{indent}{marker}{lines[0]}"]
    for line in lines[1:]:
        out.append(f"{indent}  {line}")
    return "\n".join(out)


def _render_table(rows: list[dict[str, Any]], depth: int) -> str:
    """Render an ADF table as a markdown pipe-table."""
    if not rows:
        return ""
    parsed: list[tuple[bool, list[str]]] = []  # (is_header_row, cells)
    for row in rows:
        cells = row.get("content", []) or []
        is_header = any(c.get("type") == "tableHeader" for c in cells)
        rendered = [_render_node(c, depth).strip().replace("\n", " ").replace("|", "\\|") or " "
                    for c in cells]
        parsed.append((is_header, rendered))
    if not parsed:
        return ""
    width = max(len(r[1]) for r in parsed)
    parsed = [(h, r + [" "] * (width - len(r))) for h, r in parsed]
    out: list[str] = []
    out.append("| " + " | ".join(parsed[0][1]) + " |")
    out.append("|" + "|".join(["---"] * width) + "|")
    for _, cells in parsed[1:]:
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def text_to_adf(text: str) -> dict[str, Any]:
    """Plain text → ADF doc. Each blank-line-separated chunk is a paragraph;
    lines starting with '- ' or '* ' inside a chunk become a bullet list."""
    chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
    content: list[dict[str, Any]] = []
    for chunk in chunks:
        lines = chunk.splitlines()
        if all(line.lstrip().startswith(("- ", "* ")) for line in lines):
            items = []
            for line in lines:
                stripped = line.lstrip().removeprefix("- ").removeprefix("* ")
                items.append(
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": stripped}]}
                        ],
                    }
                )
            content.append({"type": "bulletList", "content": items})
        else:
            content.append({"type": "paragraph", "content": [{"type": "text", "text": chunk}]})
    return {"type": "doc", "version": 1, "content": content}


def sections_to_adf(sections: list[tuple[str, list[str]]]) -> dict[str, Any]:
    """Build ADF from list of (heading, bullets) tuples — the test-cases pattern.

    Used for structured custom fields like 'Test cases' where each section has
    a bold heading followed by a bullet list.
    """
    content: list[dict[str, Any]] = []
    for heading, bullets in sections:
        content.append(
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": heading, "marks": [{"type": "strong"}]}],
            }
        )
        if bullets:
            content.append(
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": b}]}
                            ],
                        }
                        for b in bullets
                    ],
                }
            )
    return {"type": "doc", "version": 1, "content": content}
