"""Design tokens of the desktop status app. See docs/desktop-app.md → Design keywords.

LIGHT mirrors the `:root` tokens of `dashboard/style.css`; tests/test_desktop.py fails if they drift.
DARK uses the same token names, so every widget reads colors from one palette dict.
Pixel values are at 96 DPI; the window scales them with the screen (`StatusWindow.px`).
"""
from __future__ import annotations

import sys

LIGHT = {
    "ink": "#1c2635",
    "muted": "#6b7788",
    "line": "#e4e9ef",
    "surface": "#ffffff",
    "canvas": "#f4f6f9",
    "blue": "#2563eb",
    "green": "#157347",
    "amber": "#9a6500",
    "red": "#b42318",
}
DARK = {
    "ink": "#e6ebf2",
    "muted": "#97a3b5",
    "line": "#323b49",
    "surface": "#1d232d",
    "canvas": "#151a21",
    "blue": "#3b82f6",
    "green": "#3fb950",
    "amber": "#d29922",
    "red": "#f85149",
}
PALETTES = {"light": LIGHT, "dark": DARK}
THEMES = tuple(PALETTES)
# Pixels of this exact color are see-through to the blurred backdrop. No token may use it, and no
# text is drawn on it (anti-aliased text edges would fringe), so all text sits inside cards.
GLASS_KEY = "#000000"

# The four status tones: a palette color plus a glyph, so the state never relies on color alone.
TONE_TOKENS = {"ok": "green", "wait": "amber", "off": "muted", "error": "red"}
TONE_GLYPHS = {"ok": "●", "wait": "◐", "off": "○", "error": "●"}
# The theme button shows where it switches to: a moon in light mode, a sun in dark mode.
THEME_TOGGLE_GLYPHS = {"light": "☾", "dark": "☀"}
REFRESH_GLYPH = "⟳"

# Spacing scale and sizes, in pixels at 96 DPI.
SPACE_XS, SPACE_S, SPACE_M, SPACE_L = 4, 8, 12, 16
WINDOW_WIDTH = 320
CARD_RADIUS = 12
GLYPH_COLUMN_CHARS = 2
ICON_SIZE = 32

FONT_FAMILY = "Segoe UI" if sys.platform == "win32" else "TkDefaultFont"
FONT_HEADLINE = (FONT_FAMILY, 11, "bold")
FONT_SECTION = (FONT_FAMILY, 8, "bold")
FONT_BODY = (FONT_FAMILY, 10)
FONT_CAPTION = (FONT_FAMILY, 9)

POLL_INTERVAL_MS = 5000
QUEUE_CHECK_MS = 200
# After Start Hub, show `starting` instead of `stopped` for this long while the service boots.
START_GRACE_SECONDS = 120


def tone_color(palette: dict[str, str], tone: str) -> str:
    return palette[TONE_TOKENS[tone]]


def primary_button(palette: dict[str, str]) -> dict[str, str]:
    return {"bg": palette["blue"], "fg": "#ffffff", "activebackground": palette["ink"], "activeforeground": palette["surface"]}


def secondary_button(palette: dict[str, str]) -> dict[str, str]:
    return {"bg": palette["surface"], "fg": palette["ink"], "activebackground": palette["line"], "activeforeground": palette["ink"]}
