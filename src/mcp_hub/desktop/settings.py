"""The Settings card: tabs for the look of the app, the Hub, the child MCPs, and their config files.

Pages only report choices through callbacks; StatusWindow applies them, saves them, and calls each
page's `sync` so the controls always show the current state. This module holds the card and the
Appearance page; the Hub and MCPs pages live in hub_page.py, and the Config page in config_page.py.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSignalBlocker, Qt
from qfluentwidgets import SegmentedWidget

from . import theme
from .i18n import LANGUAGES, Translator
from .widgets import Card, ConfirmButton, Page, button, label, segment, select_segment

TABS = ("appearance", "hub", "mcps", "config")
_SEGMENT_KEYS = {
    "theme": {"light": "desktop.settings.themeLight", "dark": "desktop.settings.themeDark"},
    "glass": {True: "desktop.settings.on", False: "desktop.settings.off"},
    "glass_style": {"frosted": "desktop.settings.frosted", "clear": "desktop.settings.clear"},
}
_GLASS = (True, False)


def _set_enabled(segmented: SegmentedWidget, values: tuple, enabled: bool) -> None:
    for value in values:
        segment(segmented, value).setEnabled(enabled)


class AppearancePage(Page):
    """Theme, language, frosted glass, card opacity, and background tint."""

    def __init__(
        self,
        *,
        on_change: Callable[..., None],
        on_language: Callable[[str], None],
        on_reset: Callable[[], None],
    ) -> None:
        super().__init__()
        self.theme_label, self.theme_segments = self.segment_row(theme.THEMES, lambda value: on_change(theme=value))
        self.language_label, self.language_segments = self.segment_row(LANGUAGES, on_language)
        self.glass_label, self.glass_segments = self.segment_row(_GLASS, lambda value: on_change(glass=value))
        self.style_label, self.style_segments = self.segment_row(
            theme.GLASS_STYLES, lambda value: on_change(glass_style=value))
        self.unavailable = label(role="caption", wrap=True)
        self.content.addWidget(self.unavailable)
        self.opacity_label, self.opacity_value, self.opacity = self.slider_row(
            theme.CARD_OPACITY_RANGE, lambda value: on_change(card_opacity=value))
        self.tint_label, self.tint_value, self.tint = self.slider_row(theme.TINT_RANGE, lambda value: on_change(tint=value))
        self.reset_button = button(on_reset)
        self.content.addSpacing(theme.SPACE_XS)
        self.content.addWidget(self.reset_button, alignment=Qt.AlignmentFlag.AlignLeft)

    def retranslate(self, t: Translator) -> None:
        for widget, key in (
            (self.theme_label, "desktop.settings.theme"), (self.language_label, "desktop.settings.language"),
            (self.glass_label, "desktop.settings.glass"), (self.style_label, "desktop.settings.glassStyle"),
            (self.opacity_label, "desktop.settings.cardOpacity"),
            (self.tint_label, "desktop.settings.tint"), (self.reset_button, "desktop.settings.reset"),
            (self.unavailable, "desktop.settings.glassUnavailable"),
        ):
            widget.setText(t(key))
        for name, segmented, values in (("theme", self.theme_segments, theme.THEMES),
                                        ("glass", self.glass_segments, _GLASS),
                                        ("glass_style", self.style_segments, theme.GLASS_STYLES)):
            for value in values:
                segment(segmented, value).setText(t(_SEGMENT_KEYS[name][value]))
        for code in LANGUAGES:
            segment(self.language_segments, code).setText(code.upper())

    def sync(self, appearance: theme.Appearance, language: str, glass_available: bool) -> None:
        """Show the current choices without reporting them back as new changes."""
        select_segment(self.theme_segments, appearance.theme)
        select_segment(self.language_segments, language)
        select_segment(self.glass_segments, appearance.glass)
        _set_enabled(self.glass_segments, _GLASS, glass_available)
        self.unavailable.setVisible(not glass_available)
        sliders_on = glass_available and appearance.glass
        select_segment(self.style_segments, appearance.glass_style)
        _set_enabled(self.style_segments, theme.GLASS_STYLES, sliders_on)
        opacity = round(theme.card_alpha(appearance) * 100)
        tint = round(theme.tint_alpha(appearance) * 100 / 255)
        for slider, value_label, value in ((self.opacity, self.opacity_value, opacity), (self.tint, self.tint_value, tint)):
            with QSignalBlocker(slider):
                slider.setValue(value)
            slider.setEnabled(sliders_on)
            value_label.setText(f"{value}%")


class SettingsPanel(Card):
    """A title, a tab row, one visible page, and a restart notice when Hub changes wait for a restart."""

    def __init__(
        self,
        width: int,
        pages: dict[str, Page],
        *,
        on_tab: Callable[[str], None],
        on_restart: Callable[[], None],
    ) -> None:
        super().__init__(width)
        self.pages, self.on_tab, self.tab = pages, on_tab, TABS[0]
        self.title = label(role="section")
        self.content.addWidget(self.title)
        # The tab row has a line of its own: four tabs do not fit beside the title in the window's width.
        tabs = Page()
        _caption, self.tab_segments = tabs.segment_row(TABS, self.show_tab)
        self.content.addWidget(tabs)
        self.content.addSpacing(theme.SPACE_XS)
        for page in pages.values():
            self.content.addWidget(page)
        self.pending = label(role="caption", wrap=True)
        self.restart_button = ConfirmButton(on_restart)
        self.content.addSpacing(theme.SPACE_XS)
        self.content.addWidget(self.pending)
        self.content.addWidget(self.restart_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.show_pending(False)
        self.show_tab(TABS[0])

    def show_tab(self, name: str) -> None:
        self.tab = name
        select_segment(self.tab_segments, name)
        for key, page in self.pages.items():
            page.setVisible(key == name)
        self.on_tab(name)

    def show_pending(self, visible: bool) -> None:
        self.pending.setVisible(visible)
        self.restart_button.setVisible(visible)

    def retranslate(self, t: Translator) -> None:
        self.title.setText(t("desktop.settings.title"))
        for name in TABS:
            segment(self.tab_segments, name).setText(t(f"desktop.settings.tab.{name}"))
        self.pending.setText(t("desktop.settings.pending"))
        self.restart_button.set_texts(t("desktop.settings.restartNow"), t("desktop.settings.confirm"))
        for page in self.pages.values():
            page.retranslate(t)
