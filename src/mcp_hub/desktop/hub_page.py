"""The Hub and MCPs pages of the Settings card.

Hub: start, restart, or stop the service; track AI clients (the saved activity-UI mode); the Hub's
sign-in task and the app's own sign-in entry. MCPs: each child on or off. The Hub keeps these settings
and changes them through its API, so they show only while it runs; changes apply at the next Hub start,
so the Settings card then offers a restart. Secrets (API keys, child credentials) are never shown here.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout

from . import theme
from .hub_api import HubSettings
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
        on_bitnet: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__()
        self._build_control(on_start, on_restart, on_stop)
        self.content.addSpacing(theme.SPACE_XS)
        self.tracking_label, self.tracking_buttons = self.segment_row(_ON_OFF, on_tracking)
        self.tracking_hint = label(role="caption", wrap=True)
        self.content.addWidget(self.tracking_hint)
        self.content.addSpacing(theme.SPACE_XS)
        self.bitnet_label, self.bitnet_buttons = self.segment_row(_ON_OFF, on_bitnet or (lambda _: None))
        self.bitnet_hint = label(role="caption", wrap=True)
        self.bitnet_status = label(role="caption", wrap=True)
        self.content.addWidget(self.bitnet_hint)
        self.content.addWidget(self.bitnet_status)
        self.content.addSpacing(theme.SPACE_XS)
        self.autostart_buttons: dict[str, dict[bool, QPushButton]] = {}
        self.autostart_labels = {}
        for target in ("hub", "app"):
            caption, buttons = self.segment_row(_ON_OFF, lambda value, chosen=target: on_autostart(chosen, value))
            self.autostart_labels[target], self.autostart_buttons[target] = caption, buttons
        self.app_hint = label(role="caption", wrap=True)
        self.content.addWidget(self.app_hint)
        self.loading = label(role="caption")
        self.needs_hub = label(role="caption", wrap=True)
        self.content.addWidget(self.loading)
        self.content.addWidget(self.needs_hub)
        self._bitnet_state: str | None = None
        self._t: Translator | None = None

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

    def _update_bitnet_status_text(self) -> None:
        if self._t is None:
            return
        state = self._bitnet_state or "off"
        self.bitnet_status.setText(self._t(f"desktop.bitnet.state.{state}"))

    def retranslate(self, t: Translator) -> None:
        self._t = t
        self.control_label.setText(t("desktop.settings.hub.control"))
        self.start_button.setText(t("desktop.settings.hub.start"))
        self.restart_button.set_texts(t("desktop.settings.hub.restart"), t("desktop.settings.confirm"))
        self.stop_button.set_texts(t("desktop.settings.hub.stop"), t("desktop.settings.confirm"))
        self.tracking_label.setText(t("desktop.settings.hub.tracking"))
        self.tracking_hint.setText(t("desktop.settings.hub.trackingHint"))
        self.bitnet_label.setText(t("desktop.settings.hub.bitnet"))
        self.bitnet_hint.setText(t("desktop.settings.hub.bitnetHint"))
        self._update_bitnet_status_text()
        self.autostart_labels["hub"].setText(t("desktop.settings.hub.autostartHub"))
        self.autostart_labels["app"].setText(t("desktop.settings.hub.autostartApp"))
        self.app_hint.setText(t("desktop.settings.hub.autostartAppSource"))
        self.loading.setText(t("desktop.settings.loading"))
        self.needs_hub.setText(t("desktop.settings.needsHub"))
        for buttons in (self.tracking_buttons, self.bitnet_buttons, *self.autostart_buttons.values()):
            _on_off_texts(buttons, t)

    def sync(
        self,
        hub: str | None,
        settings: HubSettings | None,
        autostart: dict[str, bool | None] | None,
        bitnet_state: str | None = None,
    ) -> None:
        """Enable what can run in this Hub state and show the saved choices (unknown ones stay disabled)."""
        self.start_button.setEnabled(hub == "stopped")
        self.restart_button.setEnabled(hub == "running")
        self.stop_button.setEnabled(hub == "running")
        tracking = settings.activity_ui if settings is not None else None
        editable = bool(settings and settings.editable)
        _sync_segment(self.tracking_buttons, tracking, editable)
        bitnet = settings.bitnet if settings is not None else None
        _sync_segment(self.bitnet_buttons, bitnet, editable)
        self._bitnet_state = bitnet_state
        self._update_bitnet_status_text()
        for target, buttons in self.autostart_buttons.items():
            _sync_segment(buttons, autostart.get(target) if autostart else None, True)
        running = hub == "running"
        # Running from source there is no installed app to open at sign-in.
        self.app_hint.setVisible(autostart is not None and autostart.get("app") is None)
        self.loading.setVisible(autostart is None or (running and settings is None))
        self.needs_hub.setVisible(hub is not None and not running)


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
        self.settings: HubSettings | None = None
        self.running = True

    def retranslate(self, t: Translator) -> None:
        self.t = t
        self._apply_texts()

    def sync(self, settings: HubSettings | None, running: bool = True) -> None:
        self.settings, self.running = settings, running
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
        if not self.running:
            note = "desktop.settings.needsHub"
        elif self.settings is None:
            note = "desktop.settings.loading"
        elif not self.settings.servers:
            note = "desktop.settings.mcps.empty"
        else:
            note = None if self.settings.editable else "desktop.settings.mcps.readOnly"
        self.note.setText(self.t(note) if note else "")
        self.note.setVisible(note is not None)
