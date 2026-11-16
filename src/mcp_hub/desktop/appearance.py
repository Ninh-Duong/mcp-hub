"""Appearance and saved preferences of the status window: theme, accent, language, and the flush of preferences."""

from __future__ import annotations
from dataclasses import replace
from PySide6.QtWidgets import QApplication
from . import prefs, theme




class AppearanceMixin:
    """Theme, language, and preference handling of StatusWindow (moved out of app.py)."""

    @property
    def theme_name(self) -> str:
        return self.appearance.theme

    def apply_theme(self) -> None:
        """Style the native window (title bar, corners, acrylic) and apply the matching stylesheet."""
        look = self.appearance
        result = bool(self.style_native(int(self.winId()), look.theme == "dark", theme.backdrop_tint(look), look.glass,
                                        clear=look.glass_style == "clear"))
        if look.glass:
            self.glass_available = result
        self.glass = result
        theme.apply_fluent(look, self.system_accent())
        self.setStyleSheet(theme.stylesheet(look, self.glass))
        self.appearance_page.sync(look, self.t.language, self.glass_available)

    @staticmethod
    def system_accent() -> str | None:
        """The Windows accent color as `#rrggbb`, or None where Qt does not report one."""
        app = QApplication.instance()
        return app.palette().accent().color().name() if app is not None else None

    def set_appearance(self, **changes: object) -> None:
        """Apply a Settings change at once and save it shortly after."""
        new = replace(self.appearance, **changes)
        if new == self.appearance or new.theme not in theme.THEMES:
            return
        theme_changed = new.theme != self.appearance.theme
        self.appearance = new
        self.apply_theme()
        if theme_changed:
            # Glyph tones follow the new stylesheet; the icon is drawn again in the new palette.
            self._keys.clear()
            self._footer_state = None
            self.render()
        self.save_timer.start()

    def set_theme(self, name: str) -> None:
        self.set_appearance(theme=name)

    def reset_appearance(self) -> None:
        """Glass on and the theme's tuned opacity and tint; the theme and language stay."""
        self.set_appearance(glass=True, card_opacity=None, tint=None, glass_style="frosted")

    def set_language(self, language: str) -> None:
        if language == self.t.language:
            return
        self.t.set_language(language)
        self.render()
        self._render_processing()
        self.appearance_page.sync(self.appearance, self.t.language, self.glass_available)
        self.save_timer.start()

    def flush_prefs(self) -> None:
        """Save the remembered choices now (also on close, so a pending change is never lost)."""
        self.save_timer.stop()
        look = self.appearance
        position = self._compact_pos
        saved = {"theme": look.theme, "language": self.t.language, "glass": look.glass,
                 "card_opacity": look.card_opacity, "tint": look.tint, "glass_style": look.glass_style,
                 "compact": self.compact, "compact_pos": [position.x(), position.y()] if position else None}
        prefs.save_prefs(self.prefs_path, {key: value for key, value in saved.items() if value is not None})
