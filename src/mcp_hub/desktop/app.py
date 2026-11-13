"""The desktop status window (view only). Logic lives in status.py, text in the locales, tokens in theme.py.

The app is a client of the Hub's HTTP API (hub_api.py) and never touches a Hub or child file; starting
the Hub and its own sign-in entry are the only things it does on the system itself (system.py).

Network work runs on daemon threads that hand results to the Qt thread through a queue, so a slow
or stopped Hub never freezes the window. Rendering is incremental: a poll that changes nothing
redraws nothing, so the window never flickers while it refreshes.
"""
from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLayout, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from . import hub_api, prefs, screenshot, status, system, theme, update, usage, win32, processing
from .hub_page import ChildrenPage, HubPage
from .i18n import LANGUAGES, Translator, system_language
from .mini import MiniBar
from .settings import AppearancePage, SettingsPanel
from .update_card import CHECKING, DOWNLOADING, FAILED, INSTALLING, READY, UPDATING, UpdateCard, takes_primary
from .widgets import Card, ProcessingLog, Row, Spinner, UsageRow, button, dot_icon, icon_button, label, set_props, show_elided

# Never equal to a section key, so every section draws once after a theme switch or at start.
_NOT_DRAWN = object()
CAPTURE_POSITION = (80, 80)
# Shown when a start or restart has not brought the Hub up within theme.START_GRACE_SECONDS.
START_TIMEOUT_KEY = "desktop.error.startTimeout"
# Errors about starting or controlling the Hub stay in the footer while the Hub is down, not just until the next poll.
STICKY_ERROR_KEYS = frozenset({START_TIMEOUT_KEY, "desktop.error.controlUnavailable"})


def thread_worker(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="mcp-hub-status-work", daemon=True).start()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


class StatusWindow(QWidget):
    def __init__(
        self,
        translator: Translator,
        *,
        appearance: theme.Appearance | None = None,
        prefs_path: Path | None = None,
        fetch: Callable[[], status.HubSnapshot] = status.fetch_snapshot,
        open_dashboard: Callable[[], None] = status.open_dashboard,
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
        self.fetch, self.open_dashboard_action, self.launch, self.clock = fetch, open_dashboard, launch, clock
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

    @property
    def theme_name(self) -> str:
        return self.appearance.theme

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
        pages = {"appearance": self.appearance_page, "hub": self.hub_page, "mcps": self.mcps_page}
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
        # Only one of the two is shown at a time (see _render_actions). Open dashboard has a plain twin for
        # the moments when an available update takes the one primary action.
        self.open_button = button(self.open_dashboard, primary=True)
        self.open_plain_button = button(self.open_dashboard)
        self.start_button = button(self.start_hub, primary=True)
        for widget in (self.open_button, self.open_plain_button, self.start_button):
            buttons.addWidget(widget, 1)
        card.content.addLayout(buttons)
        card.content.addSpacing(theme.SPACE_XS)
        self.footer = label(role="caption", wrap=True)
        card.content.addWidget(self.footer)
        self._texts += [(self.open_button, "desktop.button.openDashboard"),
                        (self.open_plain_button, "desktop.button.openDashboard"),
                        (self.start_button, "desktop.button.startHub")]

    # Appearance

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

    def flush_prefs(self) -> None:
        """Save the remembered choices now (also on close, so a pending change is never lost)."""
        self.save_timer.stop()
        look = self.appearance
        position = self._compact_pos
        saved = {"theme": look.theme, "language": self.t.language, "glass": look.glass,
                 "card_opacity": look.card_opacity, "tint": look.tint, "glass_style": look.glass_style,
                 "compact": self.compact, "compact_pos": [position.x(), position.y()] if position else None}
        prefs.save_prefs(self.prefs_path, {key: value for key, value in saved.items() if value is not None})

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if self.save_timer.isActive():
            self.flush_prefs()
        self._stop_usage_stream()
        self._stop_processing_stream()
        super().closeEvent(event)

    # Rendering

    def render(self) -> None:
        """Redraw only the sections whose content changed since the last render."""
        hub = self.snapshot.hub if self.snapshot else None
        for name, key, draw in self._sections(hub):
            if self._keys.get(name, _NOT_DRAWN) != key:
                draw(hub)
                self._keys[name] = key
        self._render_footer()

    def _sections(self, hub: str | None) -> tuple[tuple[str, object, Callable[[str | None], None]], ...]:
        snapshot, language = self.snapshot, self.t.language
        activity_ui = bool(snapshot and snapshot.activity_ui)
        return (
            ("texts", language, self._render_texts),
            ("hub", (language, hub, activity_ui), self._render_hub),
            ("children", (language, hub, snapshot.children if snapshot else (), snapshot.bitnet if snapshot else None),
             self._render_children),
            ("usage", (language, activity_ui, self._usage_lines(activity_ui)), self._render_usage),
            ("clients", (language, hub, activity_ui, snapshot.clients if snapshot else (),
                         snapshot.recent if snapshot else ()), self._render_clients),
            ("client_usage", (language, self._client_usage_key(activity_ui)), self._render_client_usage),
            ("update", (language, self.update_release, self.update_state, self.update_status, hub == "running"),
             self._render_update),
            ("actions", (hub, self._update_takes_primary(hub)), self._render_actions),
            ("mini", (language, self._mini_state(activity_ui)), self._render_mini),
            ("settings", (language, hub, self.hub_settings, self._autostart_key(), self.restart_needed,
                          snapshot.bitnet_state if snapshot else None, snapshot.bitnet if snapshot else None),
             self._render_settings),
            ("icon", snapshot.tone if snapshot else "wait", self._render_icon),
        )

    def _render_texts(self, _hub: str | None) -> None:
        for widget, key in self._texts:
            widget.setText(self.t(key))
        for widget, key in ((self.settings_button, "desktop.settings.open"), (self.compact_button, "desktop.compact.on"),
                            (self.mini.expand_button, "desktop.compact.off"), (self.refresh_button, "desktop.refresh")):
            widget.setToolTip(self.t(key))
            widget.setAccessibleName(self.t(key))
        self.settings.retranslate(self.t)

    def _render_icon(self, _hub: str | None) -> None:
        # The taskbar icon is a dot in the Hub's tone, so the state is visible without opening the window.
        tone = self.snapshot.tone if self.snapshot else "wait"
        key = (self.theme_name, tone)
        if key not in self._icons:
            self._icons[key] = dot_icon(theme.tone_color(theme.PALETTES[self.theme_name], tone))
        self.setWindowIcon(self._icons[key])

    def _render_hub(self, hub: str | None) -> None:
        mode = ""
        if self.snapshot is None:
            self.hub_row.show_state("wait", self.t("desktop.checking"))
        else:
            self.hub_row.show_state(self.snapshot.tone, self.t.hub_label(self.snapshot.hub))
            if hub == "running":
                mode = self.t("desktop.mode.activityUi" if self.snapshot.activity_ui else "desktop.mode.fast")
        self.mode_label.setText(mode)
        self.mode_label.setVisible(bool(mode))

    def clear_rows(self, rows: QVBoxLayout) -> None:
        while rows.count():
            widget = rows.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _note(self, rows: QVBoxLayout, text: str) -> None:
        rows.addWidget(label(text, role="caption", wrap=True))

    def _render_children(self, hub: str | None) -> None:
        self.clear_rows(self.children_list)
        self.child_rows = {}
        self.bitnet_row = None
        self.processing_log = None
        # New rows have empty usage lines: draw them again in this render.
        self._keys.pop("usage", None)
        self.children_count.setText("")
        if hub != "running":
            self._note(self.children_list, self.t("desktop.waitingForHub"))
            return
        children = self.snapshot.children
        if not children:
            self._note(self.children_list, self.t("desktop.children.empty"))
        else:
            self.children_count.setText(f"{sum(child.tone == 'ok' for child in children)}/{len(children)}")
        for child in children:
            item = UsageRow()
            # A connected child needs no word: its green dot and the count say it. Other states keep theirs.
            detail = "" if child.tone == "ok" else self.t.state_label(child.state)
            item.row.show_state(child.tone, child.server_id, detail)
            self.children_list.addWidget(item)
            self.child_rows[child.server_id] = item
        self._render_bitnet_row(self.snapshot.bitnet)

    def _render_bitnet_row(self, info: status.BitnetInfo | None) -> None:
        """BitNet under the children, not counted with them: green only when llama-server really answers."""
        if info is None:  # a Hub too old to report BitNet
            return
        row = Row()
        detail = "" if info.tone == "ok" else self._bitnet_detail(info)
        row.show_state(info.tone, self.t("desktop.bitnet.name"), detail)
        self.children_list.addWidget(row)
        self.bitnet_row = row
        self.processing_log = ProcessingLog()
        self.children_list.addWidget(self.processing_log)
        self._render_processing()
        if info.installing:
            Spinner(row.glyph).start()

    def _bitnet_detail(self, info: status.BitnetInfo) -> str:
        if info.installing:
            return f"{info.progress}%" if info.step in ("runtime", "model") and info.progress is not None \
                else self.t("desktop.bitnet.state.installing")
        if info.installed and not info.enabled:
            return self.t("desktop.bitnet.state.off")
        return self.t(f"desktop.bitnet.state.{info.state}")

    def _usage_lines(self, activity_ui: bool) -> tuple[tuple[str, usage.ChildUsage | None], ...]:
        if not activity_ui:
            return ()
        now = self.now()
        return tuple((server_id, self.usage_board.usage(server_id, now)) for server_id in self.child_rows)

    def _render_usage(self, _hub: str | None) -> None:
        """Set the line under each child; rows are never rebuilt for it, so calls never flicker the list."""
        lines = dict(self._usage_lines(bool(self.snapshot and self.snapshot.activity_ui)))
        for server_id, item in self.child_rows.items():
            line = lines.get(server_id)
            if line is None:
                item.show_usage("", "", "caption")
                continue
            clients = ", ".join(line.clients)
            when = self._when(line)
            text = f"{theme.USAGE_GLYPH} {self.t('desktop.usage.line', clients=clients, when=when)}"
            detail = self.t("desktop.usage.detail", clients=clients, tool=line.tool, when=when) if line.tool else ""
            item.show_usage(text, detail, "live" if line.kind == "active" else "caption")

    def _when(self, line: usage.ChildUsage) -> str:
        if line.kind == "active":
            return self.t("desktop.usage.now")
        if line.minutes_ago == 0:
            return self.t("desktop.usage.justNow")
        return self.t("desktop.usage.minutesAgo", count=line.minutes_ago)

    def _render_clients(self, hub: str | None) -> None:
        self.clear_rows(self.clients_list)
        self.client_rows = {}
        # New rows show their base detail: draw the usage details again in this render.
        self._keys.pop("client_usage", None)
        self.clients_count.setText("")
        if hub != "running":
            self._note(self.clients_list, self.t("desktop.waitingForHub"))
            return
        if not self.snapshot.activity_ui:
            self._note(self.clients_list, self.t("desktop.clients.hint"))
            return
        if not self.snapshot.clients and not self.snapshot.recent:
            self._note(self.clients_list, self.t("clients.empty"))
            return
        self.clients_count.setText(str(len(self.snapshot.clients)))
        live = self.snapshot.clients
        names = self.snapshot.client_names if len(self.snapshot.client_names) == len(live) else live
        for name, text in zip(names, live):
            self._client_row(name, "ok", text, "")
        for name in self.snapshot.recent:
            self._client_row(name, "off", name, self.t("clients.recent"))

    def _client_row(self, name: str, tone: str, text: str, base_detail: str) -> None:
        row = Row()
        row.show_state(tone, text, base_detail)
        self.clients_list.addWidget(row)
        self.client_rows[name] = (row, base_detail)

    def _client_usage_key(self, activity_ui: bool) -> tuple[tuple[str, tuple[str, tuple[str, ...]]], ...]:
        if not activity_ui:
            return ()
        return tuple(sorted(self.usage_board.client_usage(self.now()).items()))

    def _render_client_usage(self, _hub: str | None) -> None:
        """Show on each AI client row the children it uses now (blue) or used a moment ago; rows stay as they are."""
        uses = dict(self._client_usage_key(bool(self.snapshot and self.snapshot.activity_ui)))
        width = (theme.WINDOW_WIDTH - 2 * theme.SPACE_M) // 2
        for name, (row, base_detail) in self.client_rows.items():
            kind, children = uses.get(name, ("", ()))
            set_props(row.detail, role="live" if kind == "active" else "caption")
            if children:
                show_elided(row.detail, ", ".join(children), width)
            else:
                row.detail.setText(base_detail)
                row.detail.setToolTip("")

    def _autostart_key(self) -> tuple[tuple[str, bool | None], ...] | None:
        return tuple(sorted(self.autostart.items())) if self.autostart is not None else None

    def _render_settings(self, hub: str | None) -> None:
        # Settings live in the Hub, so they can be read and changed only while it runs.
        settings = self.hub_settings if hub == "running" else None
        bitnet_state = self.snapshot.bitnet_state if self.snapshot else None
        bitnet_info = self.snapshot.bitnet if self.snapshot else None
        self.hub_page.sync(hub, settings, self.autostart, bitnet_state, bitnet_info)
        self.mcps_page.sync(settings, hub == "running")
        self.settings.show_pending(self.restart_needed and hub == "running")

    def _render_update(self, hub: str | None) -> None:
        release = self.update_release
        self.update_card.setVisible(not self.compact)
        self.update_card.sync(self.t, release.label if release else None, self.updater.current if self.updater else "",
                              self.update_state, self.update_status, hub == "running")

    def _update_takes_primary(self, hub: str | None) -> bool:
        release = self.update_release
        return takes_primary(release.label if release else None, self.update_state, self.update_status, hub == "running")

    def _render_actions(self, hub: str | None) -> None:
        # Start Hub while the Hub is not running (it is only enabled when stopped); Open dashboard while it runs,
        # plain while an update takes the one primary action. Exactly one of the two is shown.
        running = hub == "running"
        primary_open = running and not self._update_takes_primary(hub)
        self.open_button.setVisible(primary_open)
        self.open_plain_button.setVisible(running and not primary_open)
        for open_widget in (self.open_button, self.open_plain_button):
            open_widget.setEnabled(running)
        self.start_button.setVisible(not running)
        self.start_button.setEnabled(hub == "stopped")

    def _render_footer(self) -> None:
        if self.message_key:
            state = (self.t(self.message_key), "error")
        elif self.snapshot is not None:
            state = (self.t("desktop.updated", time=self.snapshot.checked_at.strftime("%H:%M:%S")), "caption")
        else:
            state = ("", "caption")
        if state != self._footer_state:
            self.footer.setText(state[0])
            set_props(self.footer, role=state[1])
            self._footer_state = state

    # Behavior

    def start(self) -> None:
        """Poll the Hub now and every POLL_INTERVAL_MS, and drain worker results on the Qt thread."""
        self.refresh()
        self.poll_timer.start()
        self.update_timer.start()
        self.update_check_timer.start()
        self.drain_timer.start()
        self.check_updates()

    def refresh(self) -> None:
        if self._polling:
            return
        self._polling = True
        threading.Thread(target=self.poll_once, name="mcp-hub-status-poll", daemon=True).start()

    def poll_once(self) -> None:
        """Worker: read one snapshot (fetch never raises) and queue it for the Qt thread."""
        self.results.put(("snapshot", self.fetch()))

    def process_results(self) -> None:
        while True:
            try:
                kind, value = self.results.get_nowait()
            except queue.Empty:
                return
            if kind == "snapshot":
                self._polling = False
                self.apply(value)
            elif kind in ("hub_settings", "hub_changed"):
                self._settings_loaded(value, changed=kind == "hub_changed")
            elif kind == "autostart":
                self.autostart = value
                self.render()
            elif kind == "control_failed":
                self._control_failed()
            elif kind in ("update_check", "update_status", "update_state"):
                self._update_changed(kind, value)
            elif kind == "usage":
                self._apply_usage(*value)
            elif kind == "processing":
                self._apply_processing(*value)
            elif kind == "bitnet_install":
                self.refresh()
            else:
                self.message_key = value
                self._render_footer()

    def apply(self, snapshot: status.HubSnapshot) -> None:
        # A start or restart that never brought the Hub up says so instead of quietly showing Stopped again.
        timed_out = snapshot.hub == "stopped" and 0 < self._starting_until <= self.clock()
        if snapshot.hub == "stopped" and self.clock() < self._starting_until:
            snapshot = replace(snapshot, hub="starting")
        if snapshot.hub == "running" or timed_out:
            self._starting_until = 0.0
        if snapshot.hub != "running":
            # The next start reads hub.json anyway.
            self.restart_needed = False
        was_running = self.snapshot is not None and self.snapshot.hub == "running"
        kept = self.message_key if self.message_key in STICKY_ERROR_KEYS and snapshot.hub == "stopped" else None
        self.snapshot, self.message_key = snapshot, START_TIMEOUT_KEY if timed_out else kept
        self._sync_usage_stream()
        self._sync_processing_stream()
        self._pace_polling(snapshot)
        self.render()
        if snapshot.hub == "running" and not was_running and not self.settings.isHidden():
            self.load_tab(self.settings.tab)  # the Hub can answer now

    def _pace_polling(self, snapshot: status.HubSnapshot) -> None:
        """Read the Hub every second while BitNet installs or starts, so the percent and the dot move."""
        busy = snapshot.bitnet is not None and snapshot.bitnet.tone == "wait"
        interval = theme.BUSY_POLL_INTERVAL_MS if busy else theme.POLL_INTERVAL_MS
        if self.poll_timer.interval() != interval:
            self.poll_timer.setInterval(interval)

    def install_bitnet(self) -> None:
        """Ask the Hub to install BitNet; the progress shows with the next status reads."""
        self._run("bitnet_install", self.hub_store.install_bitnet, error_key="desktop.bitnet.error.request")

    # Updates of the app itself
    # Whole-system updates

    def check_updates(self) -> None:
        if self.update_state in {CHECKING, UPDATING, DOWNLOADING, INSTALLING}:
            return
        self.update_state = CHECKING
        self.render()

        def job() -> None:
            release = self.updater.check() if self.updater else None
            try:
                status_value = self.hub_store.check_updates()
            except (OSError, ValueError):
                status_value = self.hub_store.read_saved_update()
            self.results.put(("update_check", (release, status_value)))

        self.run_worker(job)

    def refresh_update(self) -> None:
        """Refresh durable status while a run is active, including after Hub restarts."""
        if self._update_polling or not (self.update_status and self.update_status.state == "running"):
            return
        self._update_polling = True

        def job() -> None:
            try:
                current = self.hub_store.read_update()
            except (OSError, ValueError):
                current = self.hub_store.read_saved_update() or self.update_status
            self.results.put(("update_status", current))

        self.run_worker(job)

    def _update_changed(self, kind: str, value: Any) -> None:
        if kind == "update_check":
            self.update_release, self.update_status = value
            self.update_state = READY
        elif kind == "update_status":
            self._update_polling = False
            self.update_status = value
            if value is not None and value.state in {"complete", "partial"}:
                self.update_state = FAILED if value.state == "partial" else READY
                if self._user_update:
                    self._user_update = False
                    self._install_desktop_update()
            else:
                self.update_state = UPDATING
        else:
            self.update_state = value
        self.render()
        if self.update_state == INSTALLING:
            self.close()

    def _start_system_update(self, retry: bool = False) -> None:
        self.update_state = UPDATING
        self._user_update = True
        self.render()

        def job() -> None:
            try:
                if not self.snapshot or self.snapshot.hub != "running":
                    system.start_hub()
                    deadline = time.monotonic() + system.START_TIMEOUT_SECONDS
                    while time.monotonic() < deadline:
                        try:
                            self.hub_store.read_update()
                            break
                        except (OSError, ValueError):
                            time.sleep(0.5)
                current = self.update_status
                failed = tuple(item.get("id") for item in (current.components if current else ())
                               if item.get("state") == "failed" and isinstance(item.get("id"), str))
                result = self.hub_store.retry_update(failed) if retry else self.hub_store.start_update()
                self.results.put(("update_status", result))
            except (OSError, ValueError):
                self._user_update = False  # no run started, so no later status may install
                self.results.put(("update_state", FAILED))
                if self.update_release:
                    self.run_worker(self._install_desktop_update)

        self.run_worker(job)

    def install_update(self) -> None:
        if self.update_state not in {READY, FAILED}:
            return
        if self.update_state == FAILED and self.update_status and self.update_status.state == "complete":
            self._install_desktop_update()
            return
        self._start_system_update()

    def retry_update(self) -> None:
        if self.update_state != FAILED:
            return
        if self.update_status and self.update_status.state == "complete":
            self._install_desktop_update()
            return
        self._start_system_update(retry=True)

    def _install_desktop_update(self) -> None:
        release, updater = self.update_release, self.updater
        if release is None or updater is None:
            return
        self.update_state = DOWNLOADING
        self.render()

        def job() -> None:
            try:
                updater.install(release)
            except (OSError, RuntimeError):
                self.results.put(("update_state", FAILED))
            else:
                self.results.put(("update_state", INSTALLING))

        self.run_worker(job)

    def preview_update(self, release: update.Release | None) -> None:
        """Screenshot mode: show (or hide) the update card for a sample release."""
        self.update_release, self.update_state = release, READY
        self.render()

    def show_update_notes(self) -> None:
        if self.update_release is not None and self.updater is not None:
            self.run_worker(partial(self.updater.open_notes, self.update_release))

    # BitNet processing stream, independent of Track AI clients.

    def _sync_processing_stream(self) -> None:
        snapshot = self.snapshot
        wanted = bool(snapshot and snapshot.hub == "running" and snapshot.bitnet and snapshot.bitnet.enabled)
        if not wanted:
            self._stop_processing_stream()
        elif self._processing_stop is None:
            self._processing_generation += 1
            generation, stop = self._processing_generation, threading.Event()
            self._processing_stop = stop

            def put(message: tuple[str, Any]) -> None:
                self.results.put(("processing", (generation, message)))

            threading.Thread(target=self.processing_stream, args=(put, stop),
                             name="mcp-hub-status-processing", daemon=True).start()

    def _stop_processing_stream(self) -> None:
        if self._processing_stop is not None:
            self._processing_stop.set()
            self._processing_stop = None
            self._processing_generation += 1
        self.processing_board.clear()
        self._render_processing()

    def _apply_processing(self, generation: int, message: tuple[str, Any]) -> None:
        if generation != self._processing_generation or self._processing_stop is None:
            return
        kind, value = message
        if kind == "processing_unavailable":
            self.processing_board.clear()
            self._render_processing()
        elif self.processing_board.apply(value):
            self._render_processing()

    def _render_processing(self) -> None:
        if self.processing_log is not None:
            self.processing_log.show_events(self.processing_board.events, self.t)

    # Activity stream: who uses each child

    def _sync_usage_stream(self) -> None:
        """Listen to the activity stream while the Hub runs and tracks AI clients; stop otherwise."""
        wanted = self.snapshot is not None and self.snapshot.hub == "running" and self.snapshot.activity_ui
        if wanted and self._stream_stop is None:
            self._stream_generation += 1
            generation, stop = self._stream_generation, threading.Event()
            self._stream_stop = stop

            def put(message: tuple[str, Any]) -> None:
                self.results.put(("usage", (generation, message)))

            threading.Thread(target=self.stream, args=(put, stop), name="mcp-hub-status-activity", daemon=True).start()
        elif not wanted:
            self._stop_usage_stream()

    def _stop_usage_stream(self) -> None:
        if self._stream_stop is not None:
            self._stream_stop.set()
            self._stream_stop = None
            # Messages the stopped stream already queued are dropped.
            self._stream_generation += 1
        self.usage_board.clear()

    def _apply_usage(self, generation: int, message: tuple[str, Any]) -> None:
        if generation != self._stream_generation:
            return
        kind, value = message
        if kind == "usage_snapshot":
            self.usage_board.load_snapshot(value)
        else:
            self.usage_board.apply_event(value, self.now())
        self.render()

    def preview_usage(self, board: usage.UsageBoard) -> None:
        """Screenshot mode: show fixed sample usage instead of a live stream."""
        self.usage_board = board
        self.render()

    def open_dashboard(self) -> None:
        threading.Thread(target=self.open_dashboard_once, name="mcp-hub-status-open", daemon=True).start()

    def open_dashboard_once(self) -> None:
        try:
            self.open_dashboard_action()
        except status.DesktopError:
            self.results.put(("message", "desktop.error.dashboard"))

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

    def load_tab(self, name: str) -> None:
        """Read what the opened tab shows; the Appearance tab needs nothing."""
        if name == "appearance" or self.settings.isHidden():
            return
        if self.snapshot is not None and self.snapshot.hub == "running":
            self._run("hub_settings", self.hub_store.read_settings, error_key="desktop.error.settings")
        if name == "hub":
            self._run("autostart", self.read_autostart, error_key="desktop.error.autostart")

    def _run(self, kind: str, action: Callable[..., Any], *args: Any, error_key: str) -> None:
        """Run Hub calls and system work off the Qt thread; the result (or an error message) comes back through the queue."""
        def job() -> None:
            try:
                self.results.put((kind, action(*args)))
            except (OSError, ValueError):
                self.results.put(("message", error_key))

        self.run_worker(job)

    def _write_hub_settings(self, write: Callable[[], None]) -> hub_api.HubSettings:
        write()
        return self.hub_store.read_settings()

    def _settings_loaded(self, settings: hub_api.HubSettings, *, changed: bool) -> None:
        self.hub_settings = settings
        if changed and self.snapshot is not None and self.snapshot.hub == "running":
            self.restart_needed = True
        self.render()

    def set_tracking(self, enabled: bool) -> None:
        if self.hub_settings is not None and self.hub_settings.activity_ui == enabled:
            return
        write = partial(self.hub_store.set_activity_ui, enabled)
        self._run("hub_changed", self._write_hub_settings, write, error_key="desktop.error.settings")

    def set_bitnet(self, enabled: bool) -> None:
        if self.hub_settings is not None and self.hub_settings.bitnet == enabled:
            return
        write = partial(self.hub_store.set_bitnet, enabled)
        self._run("hub_changed", self._write_hub_settings, write, error_key="desktop.error.settings")

    def set_child(self, server_id: str, enabled: bool) -> None:
        write = partial(self.hub_store.set_server_enabled, server_id, enabled)
        self._run("hub_changed", self._write_hub_settings, write, error_key="desktop.error.settings")

    def set_autostart(self, target: str, enabled: bool) -> None:
        if self.autostart is not None and self.autostart.get(target) == enabled:
            return
        self._run("autostart", self.write_autostart, target, enabled, error_key="desktop.error.autostart")

    # Compact mode: the pinned mini bar

    def set_compact(self, on: bool) -> None:
        """Shrink to the frameless mini bar above other windows, or grow back to the full window."""
        if on == self.compact:
            return
        if on:
            self._full_pos = self.pos()
            self.show_settings(False)
        # Read before show(): showing the recreated window reports a move that would overwrite it.
        target = self._compact_pos if on else self._full_pos
        self.compact = on
        for card in (*self.cards, self.settings):
            card.setVisible(not on and (card is not self.settings or self.settings_button.isChecked()))
        self.update_card.setVisible(not on and self.update_release is not None)
        self.mini.setVisible(on)
        margin = 0 if on else theme.SPACE_S
        self.outer.setContentsMargins(margin, margin, margin, margin)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, on)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on or self._capture_on_top)
        self.show()  # a flag change recreates the native window
        if target is not None:
            self.move(target)
        self.apply_theme()
        self.save_timer.start()

    def restore_compact(self, saved: dict[str, Any], on_screen: Callable[[int, int], bool]) -> None:
        """Reopen as the mini bar when it was left that way, at its last place if that is still on a screen."""
        position = saved.get("compact_pos")
        if position and on_screen(*position):
            self._compact_pos = QPoint(*position)
        if saved.get("compact"):
            self.set_compact(True)

    def moveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if self.compact:
            self._compact_pos = self.pos()
            self.save_timer.start()
        super().moveEvent(event)

    def _mini_state(self, activity_ui: bool) -> tuple:
        """What the mini bar shows: the Hub tone, each child's tone (or `live`), and the counts."""
        snapshot = self.snapshot
        if snapshot is None or snapshot.hub != "running":
            return (snapshot.tone if snapshot else "wait", snapshot.hub if snapshot else None, (), "", 0)
        now = self.now()
        children = tuple(
            (child.server_id, child.tone, child.state, self.usage_board.usage(child.server_id, now) if activity_ui else None)
            for child in snapshot.children
        )
        ok = sum(child.tone == "ok" for child in snapshot.children)
        clients = len(snapshot.clients) if activity_ui else -1
        return (snapshot.tone, snapshot.hub, children, f"{ok}/{len(snapshot.children)}", clients)

    def _render_mini(self, _hub: str | None) -> None:
        tone, hub, children, count, clients = self._mini_state(bool(self.snapshot and self.snapshot.activity_ui))
        hub_text = self.t.hub_label(hub) if hub else self.t("desktop.checking")
        dots = []
        for server_id, child_tone, state, line in children:
            if line is not None and line.kind == "active":
                detail = self.t("desktop.usage.line", clients=", ".join(line.clients), when=self.t("desktop.usage.now"))
                dots.append((server_id, "live", self.t("desktop.mini.dot", name=server_id, detail=detail)))
            else:
                dots.append((server_id, child_tone, self.t("desktop.mini.dot", name=server_id, detail=self.t.state_label(state))))
        client_text = self.t("desktop.mini.clients", count=clients) if clients >= 0 and hub == "running" else ""
        self.mini.show_state(None if hub == "running" else (tone, hub_text), dots, count, client_text)

    def show_for_capture(self) -> None:
        """Screenshot mode: on top of other windows at a fixed spot, styled again for the new native window."""
        self._capture_on_top = True
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.move(*CAPTURE_POSITION)
        self.show()
        self.apply_theme()
        self.raise_()
        self.activateWindow()


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m mcp_hub.desktop", description="mcp-hub desktop status app")
    parser.add_argument("--version", action="store_true", help="print the app's version, then exit")
    parser.add_argument("--screenshot", metavar="DIR", help="save sample-state PNGs for UI review, then exit")
    parser.add_argument("--states", default=",".join(screenshot.STATES), help="comma list: " + ",".join(screenshot.STATES))
    parser.add_argument("--lang", default=",".join(LANGUAGES), help="comma list: " + ",".join(LANGUAGES))
    parser.add_argument("--theme", default=",".join(theme.THEMES), help="comma list: " + ",".join(theme.THEMES))
    args = parser.parse_args(argv)
    for name, allowed in (("states", screenshot.STATES), ("lang", LANGUAGES), ("theme", theme.THEMES)):
        values = [value.strip() for value in getattr(args, name).split(",") if value.strip()]
        unknown = sorted(set(values) - set(allowed))
        if unknown or not values:
            parser.error(f"--{name}: unknown or empty value {', '.join(unknown)} (choose from {', '.join(allowed)})")
        setattr(args, name, values)
    return args


def application() -> QApplication:
    """The one QApplication, with the app font."""
    app = QApplication.instance() or QApplication([sys.argv[0]])
    font = QFont(app.font())
    if theme.FONT_FAMILIES:
        font.setFamilies(theme.FONT_FAMILIES)
    font.setPointSize(theme.FONT_POINTS["body"])
    app.setFont(font)
    return app


def run_screenshots(args: argparse.Namespace) -> int:
    # Sample data only: a review never reads the live Hub, changes hub.json, or touches the sign-in tasks.
    window = StatusWindow(
        Translator("en"),
        fetch=lambda: status.HubSnapshot(hub="stopped"),
        launch=lambda _arguments: None,
        hub_store=screenshot.SampleSettings(),
        read_autostart=lambda: dict(screenshot.SAMPLE_AUTOSTART),
        write_autostart=lambda _target, _enabled: dict(screenshot.SAMPLE_AUTOSTART),
        run_worker=lambda job: job(),
        stream=lambda _put, _stop: None,
        processing_stream=lambda _put, _stop: None,
        updater=update.Updater(screenshot.SAMPLE_CURRENT, lambda: None),
    )
    try:
        saved = screenshot.capture_all(window, Path(args.screenshot), args.states, args.lang, args.theme)
    except OSError as exc:
        print(f"Screenshots could not be saved ({type(exc).__name__})")
        return 1
    finally:
        window.close()
    print("\n".join(str(path) for path in saved))
    return 0


def appearance_from(saved: dict[str, Any]) -> theme.Appearance:
    """The remembered look; without a saved theme, the Windows app theme."""
    return theme.Appearance(
        theme=saved.get("theme") or win32.system_theme(),
        glass=saved.get("glass", True),
        glass_style=saved.get("glass_style", "frosted"),
        card_opacity=saved.get("card_opacity"),
        tint=saved.get("tint"),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.version:
        print(update.app_version() or "unknown")
        return 0
    guard = None if args.screenshot is not None else win32.acquire_single_instance()
    if args.screenshot is None and guard is None:
        return 0
    win32.set_app_id()
    app = application()
    if args.screenshot is not None:
        return run_screenshots(args)
    saved = prefs.load_prefs(prefs.PREFS_PATH)
    window = StatusWindow(
        Translator(saved.get("language") or system_language()),
        appearance=appearance_from(saved),
        prefs_path=prefs.PREFS_PATH,
        updater=update.default_updater(),
    )
    window.show()
    window.restore_compact(saved, lambda x, y: QGuiApplication.screenAt(QPoint(x, y)) is not None)
    window.start()
    return app.exec()
