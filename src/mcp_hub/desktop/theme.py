"""Design tokens of the desktop status app. See docs/desktop-app.md → Design keywords.

LIGHT mirrors the `:root` tokens of `dashboard/style.css`; tests/test_desktop.py fails if they drift.
DARK uses the same token names, so the stylesheet reads colors from one palette dict.
Sizes are Qt device-independent pixels and points; Qt scales them with the screen.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

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

# Frosted glass per theme: the backdrop tint (the palette's canvas color at this alpha, 0-255) over the
# blurred desktop, the card fill (the palette's surface at this alpha, 0-1), and the card edge.
GLASS = {
    "light": {"tint_alpha": 0x55, "card_alpha": 0.55, "border": "rgba(255, 255, 255, 0.70)"},
    "dark": {"tint_alpha": 0x70, "card_alpha": 0.45, "border": "rgba(255, 255, 255, 0.10)"},
}
# What the Settings sliders allow, in percent: card opacity (0 leaves only the card edge) and backdrop tint.
CARD_OPACITY_RANGE = (0, 100)
TINT_RANGE = (0, 90)
# `frosted`: the acrylic blur with its luminosity and noise layer; `clear`: a light blur without that layer.
GLASS_STYLES = ("frosted", "clear")


@dataclass(frozen=True)
class Appearance:
    """The user's look: theme, glass on/off, and overrides (None means the theme's tuned default)."""

    theme: str = "light"
    glass: bool = True
    card_opacity: int | None = None
    tint: int | None = None
    glass_style: str = "frosted"

# The four status tones: a palette color plus a glyph, so the state never relies on color alone.
TONE_TOKENS = {"ok": "green", "wait": "amber", "off": "muted", "error": "red"}
TONE_GLYPHS = {"ok": "●", "wait": "◐", "off": "○", "error": "●"}
SETTINGS_GLYPH = "⚙"
# Shrink to the pinned mini bar, and grow back to the full window.
COMPACT_GLYPH = "⤡"
# A child dot in the mini bar while an AI client calls it (`live`, blue).
LIVE_GLYPH = "◉"
EXPAND_GLYPH = "⤢"
# Leads the line under a child that says which AI client uses it.
USAGE_GLYPH = "↳"
REFRESH_GLYPH = "⟳"

# Spacing scale and sizes, in device-independent pixels.
SPACE_XS, SPACE_S, SPACE_M, SPACE_L = 4, 8, 12, 16
WINDOW_WIDTH = 288
CARD_RADIUS = 12
BUTTON_RADIUS = 8
GLYPH_COLUMN_WIDTH = 16
ICON_SIZE = 32

FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI"] if sys.platform == "win32" else []
# The tone glyphs come from a symbol font, so all four render at the same size.
SYMBOL_FONT = "Segoe UI Symbol" if sys.platform == "win32" else ""
FONT_POINTS = {"headline": 11, "section": 8, "body": 10, "caption": 9}

POLL_INTERVAL_MS = 5000
QUEUE_CHECK_MS = 200
# After Start Hub, show `starting` instead of `stopped` for this long while the service boots.
START_GRACE_SECONDS = 120
# Settings changes are saved this long after the last one, so dragging a slider writes the file once.
PREFS_SAVE_DELAY_MS = 400
# Restart and Stop disconnect AI clients, so they ask for a second click within this time.
CONFIRM_MS = 4000
# A child's "used by" line stays this long after its last call; the activity stream retries up to this delay.
RECENT_SECONDS = 120
STREAM_RETRY_MAX_SECONDS = 30


def tone_color(palette: dict[str, str], tone: str) -> str:
    return palette[TONE_TOKENS[tone]]


def rgba(color: str, alpha: float) -> str:
    """`#rrggbb` at an opacity from 0 to 1, as a Qt stylesheet color."""
    red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    return f"rgba({red}, {green}, {blue}, {alpha:g})"


def default_card_opacity(theme_name: str) -> int:
    return round(GLASS[theme_name]["card_alpha"] * 100)


def default_tint(theme_name: str) -> int:
    return round(GLASS[theme_name]["tint_alpha"] * 100 / 255)


def card_alpha(appearance: Appearance) -> float:
    opacity = appearance.card_opacity if appearance.card_opacity is not None else default_card_opacity(appearance.theme)
    return opacity / 100


def tint_alpha(appearance: Appearance) -> int:
    tint = appearance.tint if appearance.tint is not None else default_tint(appearance.theme)
    return round(tint * 255 / 100)


def backdrop_tint(appearance: Appearance) -> int:
    """The acrylic tint (the theme's canvas color) as the AABBGGRR integer the Windows accent policy expects."""
    color = PALETTES[appearance.theme]["canvas"]
    red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    return (tint_alpha(appearance) << 24) | (blue << 16) | (green << 8) | red


def stylesheet(appearance: Appearance, glass_active: bool) -> str:
    """One Qt stylesheet for the whole window. With glass, the window is see-through and cards are frosted."""
    palette, frost = PALETTES[appearance.theme], GLASS[appearance.theme]
    glass = appearance.glass and glass_active
    window_bg = "transparent" if glass else palette["canvas"]
    card_bg = rgba(palette["surface"], card_alpha(appearance)) if glass else palette["surface"]
    card_border = frost["border"] if glass else palette["line"]
    return _base_rules(palette, window_bg, card_bg, card_border) + _button_rules(palette) + _slider_rules(palette)


def _base_rules(palette: dict[str, str], window_bg: str, card_bg: str, card_border: str) -> str:
    points = FONT_POINTS
    return f"""
QWidget#window {{ background: {window_bg}; }}
QFrame#card {{ background: {card_bg}; border: 1px solid {card_border}; border-radius: {CARD_RADIUS}px; }}
QLabel {{ background: transparent; color: {palette["ink"]}; font-size: {points["body"]}pt; }}
QLabel[role="headline"] {{ font-size: {points["headline"]}pt; font-weight: 600; }}
QLabel[role="section"] {{ color: {palette["muted"]}; font-size: {points["section"]}pt; font-weight: 700; }}
QLabel[role="caption"] {{ color: {palette["muted"]}; font-size: {points["caption"]}pt; }}
QLabel[role="error"] {{ color: {palette["red"]}; font-size: {points["caption"]}pt; }}
QLabel[role="live"] {{ color: {palette["blue"]}; font-size: {points["caption"]}pt; }}
""" + "".join(
        f'QLabel[tone="{tone}"] {{ color: {palette[token]};{_symbol_font()} }}\n'
        for tone, token in (*TONE_TOKENS.items(), ("live", "blue"))
    )


def _symbol_font() -> str:
    return f' font-family: "{SYMBOL_FONT}";' if SYMBOL_FONT else ""


def _button_rules(palette: dict[str, str]) -> str:
    ink, blue, red = palette["ink"], palette["blue"], palette["red"]
    return f"""
QPushButton {{ background: {rgba(ink, 0.07)}; color: {ink}; border: none; border-radius: {BUTTON_RADIUS}px;
    padding: {SPACE_XS + 1}px {SPACE_M}px; font-size: {FONT_POINTS["body"]}pt; }}
QPushButton:hover {{ background: {rgba(ink, 0.13)}; }}
QPushButton:pressed {{ background: {rgba(ink, 0.19)}; }}
QPushButton:disabled {{ background: {rgba(ink, 0.03)}; color: {palette["muted"]}; }}
QPushButton[primary="true"] {{ background: {blue}; color: #ffffff; }}
QPushButton[primary="true"]:hover {{ background: {rgba(blue, 0.85)}; }}
QPushButton[primary="true"]:pressed {{ background: {rgba(blue, 0.70)}; }}
QPushButton[primary="true"]:disabled {{ background: {rgba(blue, 0.40)}; color: {rgba("#ffffff", 0.75)}; }}
QPushButton[small="true"] {{ padding: 2px {SPACE_S}px; font-size: {FONT_POINTS["caption"]}pt; }}
QPushButton:checked {{ background: {blue}; color: #ffffff; }}
QPushButton[segment="true"] {{ padding: 2px {SPACE_M}px; font-size: {FONT_POINTS["caption"]}pt; }}
QPushButton[armed="true"] {{ background: {red}; color: #ffffff; }}
"""


def _slider_rules(palette: dict[str, str]) -> str:
    ink, blue = palette["ink"], palette["blue"]
    return f"""
QSlider::groove:horizontal {{ height: 4px; background: {rgba(ink, 0.15)}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {blue}; border-radius: 2px; }}
QSlider::sub-page:horizontal:disabled {{ background: {rgba(ink, 0.25)}; }}
QSlider::handle:horizontal {{ background: {palette["surface"]}; border: 1px solid {palette["line"]};
    width: 14px; height: 14px; margin: -6px 0; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ border-color: {blue}; }}
"""
