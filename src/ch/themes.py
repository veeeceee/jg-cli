"""Charm-inspired Textual themes.

Two custom themes, both with the soft-pastel + rounded-borders aesthetic that
the Charm/lipgloss/bubbletea ecosystem popularized:

- ch-pink — Charm hot-pink accent on a near-black background. Default.
- ch-night — Tokyo-night flavored, cooler purple/blue accents.

Themes are registered with the App via App.register_theme() at startup.
"""

from __future__ import annotations

from textual.theme import Theme

CH_PINK = Theme(
    name="ch-pink",
    primary="#ff5bc0",       # Charm hot pink
    secondary="#bd93f9",     # soft purple
    accent="#ff79c6",
    foreground="#f8f8f2",
    background="#0f0f14",
    success="#7be77b",
    warning="#ffd866",
    error="#ff6188",
    surface="#16161e",
    panel="#1f1f29",
    boost="#252535",
    dark=True,
    variables={
        "block-cursor-foreground": "#0f0f14",
        "block-cursor-background": "#ff5bc0",
        "border": "#3d3d52",
        "border-blurred": "#2a2a3a",
        "footer-key-foreground": "#ff5bc0",
        "footer-description-foreground": "#9893a8",
        "scrollbar": "#3d3d52",
        "scrollbar-hover": "#ff5bc0",
        "scrollbar-active": "#ff79c6",
    },
)

CH_NIGHT = Theme(
    name="ch-night",
    primary="#7aa2f7",
    secondary="#bb9af7",
    accent="#7dcfff",
    foreground="#c0caf5",
    background="#1a1b26",
    success="#9ece6a",
    warning="#e0af68",
    error="#f7768e",
    surface="#1f2335",
    panel="#24283b",
    boost="#2a2e44",
    dark=True,
    variables={
        "block-cursor-foreground": "#1a1b26",
        "block-cursor-background": "#7aa2f7",
        "border": "#3b4261",
        "border-blurred": "#2a2f45",
        "footer-key-foreground": "#7aa2f7",
        "footer-description-foreground": "#737aa2",
        "scrollbar": "#3b4261",
        "scrollbar-hover": "#7aa2f7",
        "scrollbar-active": "#bb9af7",
    },
)

CH_PAPER = Theme(
    name="ch-paper",
    primary="#d33682",
    secondary="#6c71c4",
    accent="#cb4b16",
    foreground="#586e75",
    background="#fdf6e3",
    success="#859900",
    warning="#b58900",
    error="#dc322f",
    surface="#eee8d5",
    panel="#e8e2cd",
    boost="#dad4be",
    dark=False,
    variables={
        "border": "#93a1a1",
        "border-blurred": "#cdc8b0",
        "footer-key-foreground": "#d33682",
        "footer-description-foreground": "#657b83",
    },
)

ALL_THEMES = [CH_PINK, CH_NIGHT, CH_PAPER]
