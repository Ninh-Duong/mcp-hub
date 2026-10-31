"""The Hub and MCPs pages of the Settings card.

Hub: start, restart, or stop the service; track AI clients (the saved activity-UI mode); and the two
sign-in tasks. MCPs: each child on or off. Changes to `hub.json` apply at the next Hub start, so the
Settings card then offers a restart. Secrets (API keys, child credentials) are never shown here.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout

from .. import hub_settings
from . import theme
from .i18n import Translator
from .widgets import ConfirmButton, Page, button, label

_ON_OFF = (True, False)


def _sync_segment(buttons: dict[bool, QPushButton], value: bool | None, enabled: bool) -> None:
    for state, widget in buttons.items():
        widget.setEnabled(enabled and value is not None)
        if state == value:
            widget.setChecked(True)


def _on_off_texts(buttons: dict[bool, QPushButton], t: Translator) -> None:
    for state, widget in buttons.items():
        widget.setText(t("desktop.settings.on" if state else "desktop.settings.off"))


class HubPage(Page):
    def __init__(
        self,
        *,
        on_start: Callable[[], None],
        on_restart: Callable[[], None],
        on_stop: Callable[[], None],
        on_tracking: Callable[[bool], None],
        on_autostart: Callable[[str, bool], None],
    ) -> None:
        super().__init__()
        self._build_control(on_start, on_restart, on_stop)
        self.content.addSpacing(theme.SPACE_XS)
        self.tracking_label, self.tracking_buttons = self.segment_row(_ON_OFF, on_tracking)
        self.tracking_hint = label(role="caption", wrap=True)
        self.content.addWidget(self.tracking_hint)
        self.content.addSpacing(theme.SPACE_XS)
        self.autostart_buttons: dict[str, dict[bool, QPushButton]] = {}
        self.autostart_labels = {}
        for target in ("hub", "app"):
            caption, buttons = self.segment_row(_ON_OFF, lambda value, chosen=target: on_autostart(chosen, value))
            self.autostart_labels[target], self.autostart_buttons[target] = caption, buttons
        self.loading = label(role="caption")
        self.content.addWidget(self.loading)

    def _build_control(self, on_start: Callable[[], None], on_restart: Callable[[], None], on_stop: Callable[[], None]) -> None:
        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_XS)
        self.control_label = label(role="caption")
        self.start_button = button(on_start, small=True)
        self.restart_button, self.stop_button = ConfirmButton(on_restart), ConfirmButton(on_stop)
        row.addWidget(self.control_label)
        row.addStretch(1)
        for widget in (self.start_button, self.restart_button, self.stop_button):
            widget.setProperty("small", True)
            row.addWidget(widget)
        self.content.addLayout(row)

    def retranslate(self, t: Translator) -> None:
        self.control_label.setText(t("desktop.settings.hub.control"))
        self.start_button.setText(t("desktop.settings.hub.start"))
        self.restart_button.set_texts(t("desktop.settings.hub.restart"), t("desktop.settings.confirm"))
        self.stop_button.set_texts(t("desktop.settings.hub.stop"), t("desktop.settings.confirm"))
        self.tracking_label.setText(t("desktop.settings.hub.tracking"))
        self.tracking_hint.setText(t("desktop.settings.hub.trackingHint"))
        self.autostart_labels["hub"].setText(t("desktop.settings.hub.autostartHub"))
        self.autostart_labels["app"].setText(t("desktop.settings.hub.autostartApp"))
        self.loading.setText(t("desktop.settings.loading"))
        for buttons in (self.tracking_buttons, *self.autostart_buttons.values()):
            _on_off_texts(buttons, t)

    def sync(self, hub: str | None, settings: hub_settings.HubSettings | None, autostart: dict[str, bool] | None) -> None:
        """Enable what can run in this Hub state and show the saved choices (unknown ones stay disabled)."""
        self.start_button.setEnabled(hub == "stopped")
        self.restart_button.setEnabled(hub == "running")
        self.stop_button.setEnabled(hub == "running")
        tracking = settings.activity_ui if settings is not None else None
        _sync_segment(self.tracking_buttons, tracking, bool(settings and settings.editable))
        for target, buttons in self.autostart_buttons.items():
            _sync_segment(buttons, autostart.get(target) if autostart else None, True)
        self.loading.setVisible(settings is None or autostart is None)


class ChildrenPage(Page):
    """One On/Off row per child MCP; rows are rebuilt only when the set of children changes."""

    def __init__(self, *, on_toggle: Callable[[str, bool], None]) -> None:
        super().__init__()
        self.on_toggle = on_toggle
        self.t: Translator | None = None
        self.rows = QVBoxLayout()
        self.rows.setSpacing(theme.SPACE_XS)
        self.content.addLayout(self.rows)
        self.note = label(role="caption", wrap=True)
        self.content.addWidget(self.note)
        self.switches: dict[str, tuple[dict[bool, QPushButton], object]] = {}
        self.settings: hub_settings.HubSettings | None = None

    def retranslate(self, t: Translator) -> None:
        self.t = t
        self._apply_texts()

    def sync(self, settings: hub_settings.HubSettings | None) -> None:
        self.settings = settings
        ids = tuple(switch.server_id for switch in settings.servers) if settings else ()
        if ids != tuple(self.switches):
            self._rebuild(ids)
        for switch in settings.servers if settings else ():
            buttons, reason = self.switches[switch.server_id]
            _sync_segment(buttons, switch.enabled, settings.editable)
            reason.setVisible(switch.reason is not None)
        self._apply_texts()

    def _rebuild(self, ids: tuple[str, ...]) -> None:
        while self.rows.count():
            widget = self.rows.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.switches = {}
        for server_id in ids:
            row = Page()
            caption, buttons = row.segment_row(_ON_OFF, lambda value, chosen=server_id: self.on_toggle(chosen, value))
            caption.setText(server_id)
            reason = label(role="caption", wrap=True)
            row.content.addWidget(reason)
            self.rows.addWidget(row)
            self.switches[server_id] = (buttons, reason)

    def _apply_texts(self) -> None:
        if self.t is None:
            return
        for buttons, reason in self.switches.values():
            _on_off_texts(buttons, self.t)
            reason.setText(self.t("desktop.settings.mcps.credentials"))
        if self.settings is None:
            note = "desktop.settings.loading"
        elif not self.settings.servers:
            note = "desktop.settings.mcps.empty"
        else:
            note = None if self.settings.editable else "desktop.settings.mcps.readOnly"
        self.note.setText(self.t(note) if note else "")
        self.note.setVisible(note is not None)
