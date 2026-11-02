"""The update card: shown above the actions while a newer app version is available (docs/versioning.md).

A failed update shows its one fixed sentence here and lets the user try again."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QHBoxLayout

from . import theme
from .widgets import Card, button, label, set_props

READY, DOWNLOADING, INSTALLING, FAILED = "ready", "downloading", "installing", "failed"
STATE_NOTES = {DOWNLOADING: "desktop.update.downloading", INSTALLING: "desktop.update.installing",
               FAILED: "desktop.update.failed"}


class UpdateCard(Card):
    def __init__(self, on_update: Callable[[], None], on_notes: Callable[[], None]) -> None:
        super().__init__(theme.WINDOW_WIDTH)
        self.title = label(role="headline", wrap=True)
        self.note = label(role="caption", wrap=True)
        self.update_button = button(on_update)
        self.notes_button = button(on_notes, small=True)
        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SPACE_S)
        buttons.addWidget(self.update_button)
        buttons.addStretch(1)
        buttons.addWidget(self.notes_button)
        for widget in (self.title, self.note):
            self.content.addWidget(widget)
        self.content.addSpacing(theme.SPACE_XS)
        self.content.addLayout(buttons)
        self.hide()

    def sync(self, t: Callable[..., str], version: str, current: str, state: str, blocked: tuple[str, ...],
             hub_running: bool) -> None:
        """Texts and what can be pressed: Update waits while it works or while a child fails the health gate."""
        self.title.setText(t("desktop.update.available", version=version))
        self.update_button.setText(t("desktop.update.button"))
        self.notes_button.setText(t("desktop.update.whatsNew"))
        if state in STATE_NOTES:
            note = t(STATE_NOTES[state])
        elif blocked:
            note = t("desktop.update.blocked", children=", ".join(blocked))
        else:
            note = " ".join(filter(None, (t("desktop.update.current", current=current),
                                          "" if hub_running else t("desktop.update.hubNotChecked"))))
        self.note.setText(note)
        set_props(self.note, role="error" if state == FAILED else "caption")
        self.update_button.setEnabled(state in (READY, FAILED) and not blocked)
