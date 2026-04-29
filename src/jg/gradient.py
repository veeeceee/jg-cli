"""Per-character RGB gradient helpers — the lipgloss/charm aesthetic.

Rich supports arbitrary per-character styling, so we can emulate Lipgloss's
gradient text by interpolating between hex colors across the characters of
a string. Used in column titles, the app banner, status pulses.
"""

from __future__ import annotations

from rich.text import Text


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def gradient_text(
    text: str,
    *stops: str,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
) -> Text:
    """Interpolate `stops` (hex colors) across `text`.

    Two stops → linear gradient. More stops → multi-stop spectrum
    (e.g. pink → orange → green). Whitespace keeps its position but
    inherits the color of its neighbor.
    """
    if not stops:
        return Text(text)
    if len(stops) == 1:
        style = stops[0] + (" bold" if bold else "") + (" italic" if italic else "") + (" underline" if underline else "")
        return Text(text, style=style.strip())

    rgb_stops = [hex_to_rgb(s) for s in stops]
    out = Text()
    n = max(len(text) - 1, 1)
    seg_count = len(rgb_stops) - 1
    for i, ch in enumerate(text):
        t = i / n
        seg = min(int(t * seg_count), seg_count - 1)
        local_t = (t * seg_count) - seg
        a = rgb_stops[seg]
        b = rgb_stops[seg + 1]
        r = lerp(a[0], b[0], local_t)
        g = lerp(a[1], b[1], local_t)
        bl = lerp(a[2], b[2], local_t)
        style_parts = [rgb_to_hex(r, g, bl)]
        if bold:
            style_parts.append("bold")
        if italic:
            style_parts.append("italic")
        if underline:
            style_parts.append("underline")
        out.append(ch, style=" ".join(style_parts))
    return out


def banner(text: str, palette: str = "charm") -> Text:
    """Big-typography banner — used for headers and modal titles."""
    palettes = {
        "charm": ("#ff5bc0", "#bd93f9", "#8be9fd"),       # pink → purple → cyan
        "fire":  ("#ff5555", "#ffb86c", "#f1fa8c"),       # red → orange → yellow
        "leaf":  ("#50fa7b", "#8be9fd", "#bd93f9"),       # green → cyan → purple
        "dawn":  ("#ff79c6", "#ffb86c", "#f1fa8c"),       # pink → orange → yellow
        "night": ("#7aa2f7", "#bb9af7", "#ff79c6"),       # blue → purple → pink
        "mono":  ("#f8f8f2", "#9893a8"),                  # light gray → mid gray
    }
    stops = palettes.get(palette, palettes["charm"])
    return gradient_text(text, *stops, bold=True)


def hatch_block(width: int, height: int, *, alpha_color: str = "#3d3d52") -> Text:
    """Diagonal-slash hatched fill used as a background layer behind modals.

    Pattern: groups of `///` separated by gaps, on every line, offset for
    visual rhythm. Color is dim by design (it's chrome, not content)."""
    out = Text()
    pattern = "///   " * ((width // 6) + 2)
    for row in range(height):
        offset = (row * 2) % 6
        line = (pattern[offset : offset + width]).ljust(width)
        out.append(line, style=alpha_color)
        if row < height - 1:
            out.append("\n")
    return out


def perimeter_color(idx: int, perimeter: int, stops: list[tuple[int, int, int]]) -> str:
    """Color for character at perimeter position `idx` (0..perimeter-1)."""
    if perimeter <= 1 or len(stops) == 1:
        r, g, b = stops[0]
        return rgb_to_hex(r, g, b)
    t = idx / max(perimeter - 1, 1)
    seg_count = len(stops) - 1
    seg = min(int(t * seg_count), seg_count - 1)
    local_t = (t * seg_count) - seg
    a = stops[seg]
    b = stops[seg + 1]
    return rgb_to_hex(lerp(a[0], b[0], local_t), lerp(a[1], b[1], local_t), lerp(a[2], b[2], local_t))


def gradient_edge(length: int, start_idx: int, perimeter: int, stops: list[tuple[int, int, int]],
                  *, char: str = "─", left_cap: str = "", right_cap: str = "",
                  left_cap_idx: int | None = None, right_cap_idx: int | None = None) -> Text:
    """Render a horizontal edge segment with per-character gradient.

    Used for top/bottom edges. Set `left_cap`/`right_cap` to corner chars and
    pass their perimeter indices to color them in sweep position."""
    out = Text()
    if left_cap:
        idx = left_cap_idx if left_cap_idx is not None else start_idx
        out.append(left_cap, style=f"{perimeter_color(idx, perimeter, stops)} bold")
    for i in range(length):
        out.append(char, style=f"{perimeter_color(start_idx + i, perimeter, stops)} bold")
    if right_cap:
        idx = right_cap_idx if right_cap_idx is not None else start_idx + length - 1
        out.append(right_cap, style=f"{perimeter_color(idx, perimeter, stops)} bold")
    return out


def gradient_vline(height: int, indices: list[int], perimeter: int, stops: list[tuple[int, int, int]]) -> Text:
    """Render a vertical edge segment (one '│' per row), each colored by perimeter index."""
    out = Text()
    for i, idx in enumerate(indices[:height]):
        out.append("│", style=f"{perimeter_color(idx, perimeter, stops)} bold")
        if i < min(height, len(indices)) - 1:
            out.append("\n")
    return out


def gradient_box(width: int, height: int, *stops: str) -> Text:
    """Render a `width x height` empty box with a gradient-colored border.

    Returns a Text with each border character individually styled by interpolating
    `stops` clockwise around the perimeter. Inner area is spaces (transparent
    when rendered against a transparent-bg widget). Caller is responsible for
    positioning content widgets on top via Textual layers."""
    if width < 2 or height < 2:
        return Text("")
    perimeter = 2 * (width - 1) + 2 * (height - 1)
    rgb_stops = [hex_to_rgb(s) for s in stops] if stops else [(255, 255, 255)]

    def color_for(idx: int) -> str:
        if perimeter <= 1 or len(rgb_stops) == 1:
            r, g, b = rgb_stops[0]
            return rgb_to_hex(r, g, b)
        t = idx / perimeter
        seg_count = len(rgb_stops) - 1
        seg = min(int(t * seg_count), seg_count - 1)
        local_t = (t * seg_count) - seg
        a = rgb_stops[seg]
        b = rgb_stops[seg + 1]
        return rgb_to_hex(lerp(a[0], b[0], local_t), lerp(a[1], b[1], local_t), lerp(a[2], b[2], local_t))

    out = Text()
    # Top edge: ╭───╮
    pos = 0
    out.append("╭", style=f"{color_for(pos)} bold")
    pos += 1
    for _ in range(width - 2):
        out.append("─", style=f"{color_for(pos)} bold")
        pos += 1
    out.append("╮", style=f"{color_for(pos)} bold")
    pos += 1
    out.append("\n")
    # Sides
    for _ in range(height - 2):
        # Left edge
        out.append("│", style=f"{color_for(pos)} bold")
        # Inner: spaces (no fill — caller can layer content above)
        out.append(" " * (width - 2))
        # Right edge — track the right-side index by mirroring
        out.append("│", style=f"{color_for(perimeter - pos - 1)} bold")
        pos += 1
        out.append("\n")
    # Bottom edge: ╰───╯
    out.append("╰", style=f"{color_for(perimeter - pos - 1)} bold")
    for i in range(width - 2):
        out.append("─", style=f"{color_for(perimeter - pos - 2 - i)} bold")
    out.append("╯", style=f"{color_for(0)} bold")
    return out
