"""The Config tab: each child's required settings, and a button that opens its private config file.

The app never reads that file. The Hub creates it from the template when it is missing and returns only its
path; the app then opens it in the user's editor. Pages only report presses through callbacks: StatusWindow
runs the work off the Qt thread and passes the outcome back through `sync`.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from PySide6.QtWidgets import QVBoxLayout
from qfluentwidgets import FluentIcon, TransparentToolButton

from . import theme
from .hub_api import HubSettings, ServerSwitch
from .i18n import Translator
from .widgets import Page, Row, icon_button, label

# The outcome of the last press for one child: a tone and a locale key.
Feedback = tuple[str, str]
_EMPTY: Mapping[str, Feedback] = {}


@dataclass(frozen=True)
class ConfigStatus:
    tone: str
    key: str
    values: tuple[tuple[str, str], ...] = ()


def config_status(item: ServerSwitch, loaded_gap: bool, feedback: Feedback | None) -> ConfigStatus:
    """What the row says. A press outcome wins until the next read; then the file's own state shows."""
    if feedback is not None:
        return ConfigStatus(feedback[0], feedback[1])
    if item.requirements == "invalid":
        return ConfigStatus("error", "desktop.settings.config.invalid")
    if item.missing:
        return ConfigStatus("wait", "desktop.settings.config.missing", (("keys", ", ".join(item.missing)),))
    if loaded_gap:
        # The file is complete now, but the running Hub still has the gap until it restarts.
        return ConfigStatus("wait", "desktop.settings.config.readyRestart")
    if item.requirements == "none":
        return ConfigStatus("off", "desktop.settings.config.none")
    return ConfigStatus("ok", "desktop.settings.config.ready")


class ConfigPage(Page):
    """One row per child MCP; rows are rebuilt only when the set of children changes."""

    def __init__(self, *, on_open: Callable[[str], None]) -> None:
        super().__init__()
        self.on_open = on_open
        self.t: Translator | None = None
        self.rows = QVBoxLayout()
        self.rows.setSpacing(theme.SPACE_XS)
        self.content.addLayout(self.rows)
        self.note = label(role="caption", wrap=True)
        self.content.addWidget(self.note)
        self.items: dict[str, tuple[Row, TransparentToolButton]] = {}
        self.settings: HubSettings | None = None
        self.running = True
        # A child whose row should take the keyboard focus once it exists (set by the Child MCPs warning).
        self.pending_focus: str | None = None

    def focus_row(self, server_id: str) -> None:
        """Move the keyboard focus to the child's edit button, now if its row exists, else once it is built."""
        self.pending_focus = server_id
        self._apply_focus()

    def _apply_focus(self) -> None:
        if self.pending_focus in self.items:
            self.items[self.pending_focus][1].setFocus()
            self.pending_focus = None

    def retranslate(self, t: Translator) -> None:
        self.t = t
        self._apply_texts()

    def sync(
        self,
        settings: HubSettings | None,
        running: bool = True,
        *,
        busy: frozenset[str] = frozenset(),
        feedback: Mapping[str, Feedback] = _EMPTY,
        loaded_gaps: frozenset[str] = frozenset(),
    ) -> None:
        self.settings, self.running = settings, running
        ids = tuple(switch.server_id for switch in settings.servers) if settings else ()
        if ids != tuple(self.items):
            self._rebuild(ids)
        for switch in settings.servers if settings else ():
            row, button = self.items[switch.server_id]
            status = config_status(switch, switch.server_id in loaded_gaps, feedback.get(switch.server_id))
            row.show_state(status.tone, switch.server_id, self._caption(status))
            button.setEnabled(running and settings is not None and settings.editable and switch.server_id not in busy)
            button.setToolTip(self._tooltip(switch.server_id))
        self._apply_focus()
        self._apply_texts()

    def _caption(self, status: ConfigStatus) -> str:
        if self.t is None:
            return ""
        return self.t(status.key, **dict(status.values))

    def _tooltip(self, server_id: str) -> str:
        if self.t is None:
            return ""
        if not self.running:
            return self.t("desktop.settings.config.disabled.needsHub")
        if self.settings is not None and not self.settings.editable:
            return self.t("desktop.settings.config.disabled.readOnly")
        return self.t("desktop.settings.config.open", id=server_id)

    def _rebuild(self, ids: tuple[str, ...]) -> None:
        while self.rows.count():
            widget = self.rows.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.items = {}
        for server_id in ids:
            row = Row(detail_right=True)
            button = icon_button(FluentIcon.EDIT, lambda chosen=server_id: self.on_open(chosen))
            button.setAccessibleName(server_id)
            row.layout_.addWidget(button)
            self.rows.addWidget(row)
            self.items[server_id] = (row, button)

    def _apply_texts(self) -> None:
        if self.t is None:
            return
        for server_id, (_row, button) in self.items.items():
            button.setAccessibleName(self.t("desktop.settings.config.open", id=server_id))
        if not self.running:
            note = "desktop.settings.needsHub"
        elif self.settings is None:
            note = "desktop.settings.loading"
        elif not self.settings.servers:
            note = "desktop.settings.mcps.empty"
        elif not self.settings.editable:
            note = "desktop.settings.mcps.readOnly"
        else:
            note = "desktop.settings.config.note"
        self.note.setText(self.t(note))
