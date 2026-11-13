"""Design tokens of the desktop status app. See docs/desktop-app.md → Design keywords.

LIGHT mirrors the `:root` tokens of `dashboard/style.css`; tests/test_desktop.py fails if they drift.
DARK uses the same token names, so the stylesheet reads colors from one palette dict.
Sizes are Qt device-independent pixels and points; Qt scales them with the screen.
Buttons, sliders, switches, and icons are qfluentwidgets widgets; apply_fluent sets their theme and accent.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

LIGHT = {
    "ink": "#1c2635",
    "muted": "#3e454e",
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
    "dark": {"tint_alpha": 0x70, "card_alpha": 0.82, "border": "rgba(255, 255, 255, 0.10)"},
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


# The four status tones: a palette color and a shape (widgets.tone_icon), so the state never relies on color alone.
TONE_TOKENS = {"ok": "green", "wait": "amber", "off": "muted", "error": "red"}
# The usage line under a child keeps its text arrow; every other glyph is an icon.
USAGE_GLYPH = "↳"
SPINNER_MS = 150
# A rolling log (BitNet processing, update progress): this many rows, newest on top, each older row fainter.
LOG_FADE = (1.0, .65, .4)
LOG_ROWS = len(LOG_FADE)

# Spacing scale and sizes, in device-independent pixels.
SPACE_XS, SPACE_S, SPACE_M, SPACE_L = 4, 8, 12, 16
WINDOW_WIDTH = 320
CARD_RADIUS = 8
BUTTON_RADIUS = 4
GLYPH_COLUMN_WIDTH = 16
ICON_SIZE = 32

FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI"] if sys.platform == "win32" else []
FONT_POINTS = {"headline": 12, "section": 8, "body": 10, "caption": 9}

POLL_INTERVAL_MS = 5000
# While BitNet installs, the Hub is read this often so the percent moves.
BUSY_POLL_INTERVAL_MS = 1000
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
# The accent used when Windows does not report one (or on a platform without it).
FALLBACK_ACCENT = "blue"


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


def contrast(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two `#rrggbb` colors: 1 for no contrast, 21 for black on white."""
    def luminance(color: str) -> float:
        def linear(channel: int) -> float:
            value = channel / 255
            return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

        red, green, blue = (linear(int(color[index:index + 2], 16)) for index in (1, 3, 5))
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _contrast_with_white(color: str) -> float:
    """WCAG contrast ratio between `#rrggbb` and white (primary buttons put white text on the accent)."""
    return contrast(color, "#ffffff")


def accent_hex(system_accent: str | None) -> str:
    """The Windows accent as `#rrggbb` when white text on it is readable (3:1), else the fallback.

    A pale accent (for example a light pink) would make the primary button's white text unreadable.
    """
    if system_accent and len(system_accent) == 7 and system_accent.startswith("#"):
        try:
            if _contrast_with_white(system_accent) >= 3:
                return system_accent
        except ValueError:
            pass
    return PALETTES["light"][FALLBACK_ACCENT]


# The last (theme, accent) given to Fluent: setting it restyles every Fluent widget, so it is set only on a change.
_fluent_applied: list[tuple[str, str]] = []


def apply_fluent(appearance: Appearance, system_accent: str | None) -> None:
    """Theme the Fluent widgets: their light or dark theme, and the Windows accent for primary buttons and switches."""
    from qfluentwidgets import Theme, setTheme, setThemeColor  # imported here: the palettes stay usable without Qt

    state = (appearance.theme, accent_hex(system_accent))
    if _fluent_applied and _fluent_applied[-1] == state:
        return
    _fluent_applied[:] = [state]
    setTheme(Theme.DARK if appearance.theme == "dark" else Theme.LIGHT)
    setThemeColor(state[1])


def stylesheet(appearance: Appearance, glass_active: bool) -> str:
    """One Qt stylesheet for the window and its cards. With glass, the window is see-through and cards are frosted."""
    palette, frost = PALETTES[appearance.theme], GLASS[appearance.theme]
    glass = appearance.glass and glass_active
    window_bg = "transparent" if glass else palette["canvas"]
    card_bg = rgba(palette["surface"], card_alpha(appearance)) if glass else palette["surface"]
    card_border = frost["border"] if glass else palette["line"]
    return _base_rules(palette, window_bg, card_bg, card_border)


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
"""
