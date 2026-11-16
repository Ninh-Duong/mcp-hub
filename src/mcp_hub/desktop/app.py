"""The desktop status window (view only). Logic lives in status.py, text in the locales, tokens in theme.py.

The app is a client of the Hub's HTTP API (hub_api.py) and never touches a Hub or child file; starting
the Hub and its own sign-in entry are the only things it does on the system itself (system.py).

Network work runs on daemon threads that hand results to the Qt thread through a queue, so a slow
or stopped Hub never freezes the window. Rendering is incremental: a poll that changes nothing
redraws nothing, so the window never flickers while it refreshes.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLayout, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon
from . import hub_api, status, system, theme, update, usage, win32, processing
from .config_page import ConfigPage, Feedback
from .hub_page import ChildrenPage, HubPage
from .i18n import Translator
from .mini import MiniBar
from .settings import AppearancePage, SettingsPanel
from .update_card import READY, UpdateCard
from .widgets import Card, ProcessingLog, Row, UsageRow, button, icon_button, label


from .appearance import AppearanceMixin
from .actions import ActionsMixin
from .live_feeds import LiveFeedsMixin
from .polling import PollingMixin
from .constants import CAPTURE_POSITION
from .render import RenderMixin
from .update_controller import UpdateMixin
from .mini import MiniMixin

# Never equal to a section key, so every section draws once after a theme switch or at start.
# Shown when a start or restart has not brought the Hub up within theme.START_GRACE_SECONDS.
# Errors about starting or controlling the Hub stay in the footer while the Hub is down, not just until the next poll.


def thread_worker(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="mcp-hub-status-work", daemon=True).start()


def utc_now() -> datetime:
    return datetime.now(UTC)


HUB = hub_api.HubClient()


def control_hub(arguments: list[str]) -> None:
    """Start runs the Hub's sign-in task; restart and stop go through the Hub's API."""
    if arguments[0] == "start":
        system.start_hub()
    else:
        HUB.control(arguments[0])


def autostart_state() -> dict[str, bool | None]:
    """The Hub's sign-in task (None while the Hub cannot answer) and the app's own sign-in entry."""
    try:
        hub: bool | None = HUB.read_hub_autostart()
    except (OSError, ValueError):
        hub = None
    return {"hub": hub, "app": system.app_autostart()}


def set_autostart(target: str, enabled: bool) -> dict[str, bool | None]:
    if target == "hub":
        HUB.set_hub_autostart(enabled)
    elif target == "app":
        system.set_app_autostart(enabled)
    else:
        raise ValueError(f"Unknown sign-in target: {target}")
    return autostart_state()


class StatusWindow(AppearanceMixin, ActionsMixin, LiveFeedsMixin, PollingMixin, RenderMixin, UpdateMixin, MiniMixin, QWidget):
    def __init__(
        self,
        translator: Translator,
        *,
        appearance: theme.Appearance | None = None,
        prefs_path: Path | None = None,
        fetch: Callable[[], status.HubSnapshot] = status.fetch_snapshot,
        launch: Callable[[list[str]], None] = control_hub,
        clock: Callable[[], float] = time.monotonic,
        style: Callable[[int, bool, int], bool] = win32.style_window,
        hub_store: Any = HUB,
        read_autostart: Callable[[], dict[str, bool | None]] = autostart_state,
        write_autostart: Callable[[str, bool], dict[str, bool | None]] = set_autostart,
        run_worker: Callable[[Callable[[], None]], None] = thread_worker,
        stream: Callable[[Callable[[tuple[str, Any]], None], threading.Event], None] = usage.stream_usage,
        processing_stream: Callable = processing.stream_processing,
        now: Callable[[], datetime] = utc_now,
        updater: update.Updater | None = None,
    ) -> None:
        super().__init__()
        self.t = translator
        self.fetch, self.launch, self.clock = fetch, launch, clock
        self.style_native, self.prefs_path = style, prefs_path
        # Hub settings (through the Hub's API) and sign-in entries: read and written on worker threads.
        self.hub_store, self.read_autostart, self.write_autostart = hub_store, read_autostart, write_autostart
        self.run_worker, self.stream, self.now = run_worker, stream, now
        self.processing_stream = processing_stream
        # Offers a newer app version (checked at start and hourly); None runs without it (from source, in tests).
        self.updater = updater
        appearance = appearance or theme.Appearance()
        self.appearance = appearance if appearance.theme in theme.THEMES else replace(appearance, theme="light")
        self._init_state()
        self.poll_timer = QTimer(self, interval=theme.POLL_INTERVAL_MS, timeout=self.refresh)
        self.update_timer = QTimer(self, interval=theme.POLL_INTERVAL_MS, timeout=self.refresh_update)
        self.update_check_timer = QTimer(self, interval=60 * 60 * 1000, timeout=self.check_updates)
        self.drain_timer = QTimer(self, interval=theme.QUEUE_CHECK_MS, timeout=self.process_results)
        self.save_timer = QTimer(self, singleShot=True, interval=theme.PREFS_SAVE_DELAY_MS, timeout=self.flush_prefs)
        self._setup_window()
        self._build()
        self.apply_theme()
        self.render()

    def _init_state(self) -> None:
        self.hub_settings: hub_api.HubSettings | None = None
        self.update_release: update.Release | None = None
        self.update_state = READY
        self.update_status: hub_api.UpdateStatus | None = hub_api.HubClient.read_saved_update()
        self._update_polling = False
        # Set only by Update/Retry: the app replaces itself after a run the user started, never after a check.
        self._user_update = False
        self._update_started_at = 0.0
        self.autostart: dict[str, bool | None] | None = None
        self.restart_needed = False
        # Config tab: children whose file is being opened, and the outcome of the last press for each.
        self.config_busy: set[str] = set()
        self.config_feedback: dict[str, Feedback] = {}
        self.results: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.snapshot: status.HubSnapshot | None = None
        self.message_key: str | None = None
        self.glass = False
        # Known after the first try with glass on; until then assume Windows offers it.
        self.glass_available = sys.platform == "win32"
        self._polling = False
        self._starting_until = 0.0
        self._keys: dict[str, object] = {}
        self._footer_state: tuple[str, str] | None = None
        self._icons: dict[tuple[str, str], Any] = {}
        self._texts: list[tuple[Any, str]] = []
        # Who uses each child, from the activity stream (only while the Hub tracks AI clients).
        self.usage_board = usage.UsageBoard()
        self.child_rows: dict[str, UsageRow] = {}
        self.bitnet_row: Row | None = None
        self.processing_log: ProcessingLog | None = None
        self.processing_board = processing.ProcessingBoard()
        self._processing_stop: threading.Event | None = None
        self._processing_generation = 0
        # Compact mode: the pinned mini bar, and where the full window and the bar were last placed.
        self.compact = False
        self._full_pos: Any = None
        self._compact_pos: Any = None
        self._capture_on_top = False
        self.client_rows: dict[str, tuple[Row, str]] = {}
        self._stream_stop: threading.Event | None = None
        self._stream_generation = 0


    def _setup_window(self) -> None:
        self.setObjectName("window")
        self.setWindowTitle("mcp-hub")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # The window fits its content, so it cannot be maximized.
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        if sys.platform == "win32":
            # Lets the acrylic backdrop show through wherever the stylesheet paints nothing.
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    # Layout

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SPACE_S, theme.SPACE_S, theme.SPACE_S, theme.SPACE_S)
        outer.setSpacing(theme.SPACE_M)
        # The window always fits its content: no manual resize after rows come and go.
        outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self.cards: list[Card] = []
        self._build_hub_card(outer)
        self._build_settings()
        self.settings.hide()
        outer.addWidget(self.settings)
        self.children_count, self.children_list = self._list_card(outer, "desktop.section.children", refresh=True)
        self.clients_count, self.clients_list = self._list_card(outer, "desktop.section.clients")
        # Always present in the full window so the user can check Hub/child updates too.
        self.update_card = UpdateCard(self.check_updates, self.install_update, self.retry_update, self.show_update_notes)
        outer.addWidget(self.update_card)
        self._build_action_card(outer)
        self.outer = outer
        self.mini = MiniBar(on_expand=lambda: self.set_compact(False))
        self.mini.hide()
        outer.addWidget(self.mini)

    def _build_settings(self) -> None:
        self.appearance_page = AppearancePage(
            on_change=self.set_appearance, on_language=self.set_language, on_reset=self.reset_appearance,
        )
        self.hub_page = HubPage(
            on_start=self.start_hub, on_restart=self.restart_hub, on_stop=self.stop_hub,
            on_tracking=self.set_tracking, on_autostart=self.set_autostart,
            on_bitnet=self.set_bitnet, on_install_bitnet=self.install_bitnet,
        )
        self.mcps_page = ChildrenPage(on_toggle=self.set_child)
        self.config_page = ConfigPage(on_open=self.open_child_config)
        pages = {"appearance": self.appearance_page, "hub": self.hub_page, "mcps": self.mcps_page,
                 "config": self.config_page}
        self.settings = SettingsPanel(theme.WINDOW_WIDTH, pages, on_tab=self.load_tab, on_restart=self.restart_hub)

    def _card(self, outer: QVBoxLayout) -> Card:
        card = Card(theme.WINDOW_WIDTH)
        outer.addWidget(card)
        self.cards.append(card)
        return card

    def _build_hub_card(self, outer: QVBoxLayout) -> None:
        card = self._card(outer)
        # The mode follows the state word; the settings button takes the right edge.
        self.hub_row = Row(role="headline", detail_right=False)
        self.compact_button = icon_button(FluentIcon.MINIMIZE, lambda: self.set_compact(True))
        self.hub_row.layout_.addWidget(self.compact_button)
        self.settings_button = icon_button(FluentIcon.SETTING, self.toggle_settings)
        self.settings_button.setCheckable(True)
        self.hub_row.layout_.addWidget(self.settings_button)
        card.content.addWidget(self.hub_row)
        # The mode caption sits on its own line under the state word, so a long mode never cuts the state.
        mode = QHBoxLayout()
        mode.addSpacing(theme.GLYPH_COLUMN_WIDTH + theme.SPACE_S)
        self.mode_label = label(role="caption", wrap=True)
        mode.addWidget(self.mode_label, 1)
        card.content.addLayout(mode)

    def _list_card(self, outer: QVBoxLayout, title_key: str, *, refresh: bool = False) -> tuple[QLabel, QVBoxLayout]:
        card = self._card(outer)
        header = QHBoxLayout()
        title, count = label(role="section"), label(role="section")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(count)
        if refresh:  # re-reads the Hub and its children; beside the count, where the children are listed
            self.refresh_button = icon_button(FluentIcon.SYNC, self.refresh)
            header.addWidget(self.refresh_button)
        card.content.addLayout(header)
        rows = QVBoxLayout()
        rows.setSpacing(theme.SPACE_XS)
        card.content.addLayout(rows)
        self._texts.append((title, title_key))
        return count, rows

    def _build_action_card(self, outer: QVBoxLayout) -> None:
        card = self._card(outer)
        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SPACE_S)
        self.start_button = button(self.start_hub, primary=True)
        buttons.addWidget(self.start_button, 1)
        card.content.addLayout(buttons)
        card.content.addSpacing(theme.SPACE_XS)
        self.footer = label(role="caption", wrap=True)
        card.content.addWidget(self.footer)
        self._texts.append((self.start_button, "desktop.button.startHub"))

    # Appearance


    def toggle_settings(self) -> None:
        self.show_settings(self.settings.isHidden())

    def show_settings(self, visible: bool) -> None:
        self.settings.setVisible(visible)
        self.settings_button.setChecked(visible)
        if visible:
            self.load_tab(self.settings.tab)

    def show_tab(self, name: str) -> None:
        self.show_settings(True)
        self.settings.show_tab(name)


    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if self.save_timer.isActive():
            self.flush_prefs()
        self._stop_usage_stream()
        self._stop_processing_stream()
        super().closeEvent(event)

    # Rendering


    # Behavior


    def install_bitnet(self) -> None:
        """Ask the Hub to install BitNet; the progress shows with the next status reads."""
        self._run("bitnet_install", self.hub_store.install_bitnet, error_key="desktop.bitnet.error.request")

    # Updates of the app itself
    # Whole-system updates


    # BitNet processing stream, independent of Track AI clients.


    # Activity stream: who uses each child


    def start_hub(self) -> None:
        self._control(["start"], starting=True)

    def restart_hub(self) -> None:
        """Restart with the saved settings; AI clients are disconnected for a moment."""
        self._control(["restart"], starting=True)

    def stop_hub(self) -> None:
        self._control(["stop"], starting=False)

    def _control(self, arguments: list[str], *, starting: bool) -> None:
        """Show the expected state at once; the task or HTTP call runs on a worker and reports a failure."""
        def job() -> None:
            try:
                self.launch(arguments)
            except (OSError, ValueError):
                self.results.put(("control_failed", None))

        # A new start reads hub.json again, so nothing waits for a restart any more.
        self.restart_needed = False
        if starting:
            self._starting_until = self.clock() + theme.START_GRACE_SECONDS
            self.apply(replace(self.snapshot or status.HubSnapshot(hub="stopped"), hub="starting"))
        else:
            self._starting_until = 0.0
            self._stop_processing_stream()
            self.render()
        self.run_worker(job)

    def _control_failed(self) -> None:
        """Drop the optimistic state, show why, and read the real state again."""
        self._starting_until = 0.0
        if self.snapshot is not None and self.snapshot.hub == "starting":
            self.snapshot = replace(self.snapshot, hub="stopped")
            self.render()
        self.message_key = "desktop.error.controlUnavailable"
        self._render_footer()
        self.refresh()

    # Hub settings (through the Hub's API) and sign-in entries


    def set_autostart(self, target: str, enabled: bool) -> None:
        if self.autostart is not None and self.autostart.get(target) == enabled:
            return
        self._run("autostart", self.write_autostart, target, enabled, error_key="desktop.error.autostart")

    # Compact mode: the pinned mini bar


    def show_for_capture(self) -> None:
        """Screenshot mode: on top of other windows at a fixed spot, styled again for the new native window."""
        self._capture_on_top = True
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.move(*CAPTURE_POSITION)
        self.show()
        self.apply_theme()
        self.raise_()
        self.activateWindow()


def application() -> QApplication:
    """The one QApplication, with the app font."""
    app = QApplication.instance() or QApplication([sys.argv[0]])
    font = QFont(app.font())
    if theme.FONT_FAMILIES:
        font.setFamilies(theme.FONT_FAMILIES)
    font.setPointSize(theme.FONT_POINTS["body"])
    app.setFont(font)
    return app
