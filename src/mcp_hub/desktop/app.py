"""The desktop status window (view only). Logic lives in status.py, text in the locales, tokens in theme.py.

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

from .. import control, hub_settings
from . import prefs, screenshot, status, theme, usage, win32
from .hub_page import ChildrenPage, HubPage
from .i18n import LANGUAGES, Translator, system_language
from .mini import MiniBar
from .settings import AppearancePage, SettingsPanel
from .widgets import Card, Row, UsageRow, button, dot_icon, label, set_props, show_elided

# Never equal to a section key, so every section draws once after a theme switch or at start.
_NOT_DRAWN = object()
CAPTURE_POSITION = (80, 80)


def thread_worker(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="mcp-hub-status-work", daemon=True).start()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StatusWindow(QWidget):
    def __init__(
        self,
        translator: Translator,
        *,
        appearance: theme.Appearance | None = None,
        prefs_path: Path | None = None,
        fetch: Callable[[], status.HubSnapshot] = status.fetch_snapshot,
        open_dashboard: Callable[[], None] = status.open_dashboard,
        launch: Callable[[list[str]], None] = control.launch_hubctl,
        clock: Callable[[], float] = time.monotonic,
        style: Callable[[int, bool, int], bool] = win32.style_window,
        hub_store: Any = hub_settings,
        read_autostart: Callable[[], dict[str, bool]] = control.autostart_state,
        write_autostart: Callable[[str, bool], dict[str, bool]] = control.set_autostart,
        run_worker: Callable[[Callable[[], None]], None] = thread_worker,
        stream: Callable[[Callable[[tuple[str, Any]], None], threading.Event], None] = usage.stream_usage,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        super().__init__()
        self.t = translator
        self.fetch, self.open_dashboard_action, self.launch, self.clock = fetch, open_dashboard, launch, clock
        self.style_native, self.prefs_path = style, prefs_path
        # Hub settings and sign-in tasks: read and written on worker threads, shown in the Settings card.
        self.hub_store, self.read_autostart, self.write_autostart = hub_store, read_autostart, write_autostart
        self.run_worker, self.stream, self.now = run_worker, stream, now
        appearance = appearance or theme.Appearance()
        self.appearance = appearance if appearance.theme in theme.THEMES else replace(appearance, theme="light")
        self._init_state()
        self.poll_timer = QTimer(self, interval=theme.POLL_INTERVAL_MS, timeout=self.refresh)
        self.drain_timer = QTimer(self, interval=theme.QUEUE_CHECK_MS, timeout=self.process_results)
        self.save_timer = QTimer(self, singleShot=True, interval=theme.PREFS_SAVE_DELAY_MS, timeout=self.flush_prefs)
        self._setup_window()
        self._build()
        self.apply_theme()
        self.render()

    def _init_state(self) -> None:
        self.hub_settings: hub_settings.HubSettings | None = None
        self.autostart: dict[str, bool] | None = None
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
        outer.setSpacing(theme.SPACE_S)
        # The window always fits its content: no manual resize after rows come and go.
        outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self.cards: list[Card] = []
        self._build_hub_card(outer)
        self._build_settings()
        self.settings.hide()
        outer.addWidget(self.settings)
        self.children_count, self.children_list = self._list_card(outer, "desktop.section.children")
        self.clients_count, self.clients_list = self._list_card(outer, "desktop.section.clients")
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
        self.compact_button = button(lambda: self.set_compact(True), small=True)
        self.compact_button.setText(theme.COMPACT_GLYPH)
        self.hub_row.layout_.addWidget(self.compact_button)
        self.settings_button = button(self.toggle_settings, small=True)
        self.settings_button.setText(theme.SETTINGS_GLYPH)
        self.settings_button.setCheckable(True)
        self.hub_row.layout_.addWidget(self.settings_button)
        card.content.addWidget(self.hub_row)

    def _list_card(self, outer: QVBoxLayout, title_key: str) -> tuple[QLabel, QVBoxLayout]:
        card = self._card(outer)
        header = QHBoxLayout()
        title, count = label(role="section"), label(role="section")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(count)
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
        self.open_button = button(self.open_dashboard)
        self.start_button = button(self.start_hub)
        self.refresh_button = button(self.refresh, small=True)
        self.refresh_button.setText(theme.REFRESH_GLYPH)
        for widget in (self.open_button, self.start_button):
            buttons.addWidget(widget)
        buttons.addStretch(1)
        buttons.addWidget(self.refresh_button)
        card.content.addLayout(buttons)
        card.content.addSpacing(theme.SPACE_XS)
        self.footer = label(role="caption", wrap=True)
        card.content.addWidget(self.footer)
        self._texts += [(self.open_button, "desktop.button.openDashboard"), (self.start_button, "desktop.button.startHub")]

    # Appearance

    def apply_theme(self) -> None:
        """Style the native window (title bar, corners, acrylic) and apply the matching stylesheet."""
        look = self.appearance
        result = bool(self.style_native(int(self.winId()), look.theme == "dark", theme.backdrop_tint(look), look.glass,
                                        clear=look.glass_style == "clear"))
        if look.glass:
            self.glass_available = result
        self.glass = result
        self.setStyleSheet(theme.stylesheet(look, self.glass))
        self.appearance_page.sync(look, self.t.language, self.glass_available)

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
        self.appearance_page.sync(self.appearance, self.t.language, self.glass_available)
        self.save_timer.start()

    def toggle_language(self) -> None:
        self.set_language(self.t.other_language())

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
            ("children", (language, hub, snapshot.children if snapshot else ()), self._render_children),
            ("usage", (language, activity_ui, self._usage_lines(activity_ui)), self._render_usage),
            ("clients", (language, hub, activity_ui, snapshot.clients if snapshot else (),
                         snapshot.recent if snapshot else ()), self._render_clients),
            ("client_usage", (language, self._client_usage_key(activity_ui)), self._render_client_usage),
            ("actions", hub, self._render_actions),
            ("mini", (language, self._mini_state(activity_ui)), self._render_mini),
            ("settings", (language, hub, self.hub_settings, self._autostart_key(), self.restart_needed), self._render_settings),
            ("icon", snapshot.tone if snapshot else "wait", self._render_icon),
        )

    def _render_texts(self, _hub: str | None) -> None:
        for widget, key in self._texts:
            widget.setText(self.t(key))
        for widget, key in ((self.settings_button, "desktop.settings.open"), (self.compact_button, "desktop.compact.on"),
                            (self.mini.expand_button, "desktop.compact.off")):
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
        if self.snapshot is None:
            self.hub_row.show_state("wait", self.t("desktop.checking"))
            return
        detail = ""
        if hub == "running":
            detail = self.t("desktop.mode.activityUi" if self.snapshot.activity_ui else "desktop.mode.fast")
        self.hub_row.show_state(self.snapshot.tone, self.t.hub_label(self.snapshot.hub), detail)

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
        # New rows have empty usage lines: draw them again in this render.
        self._keys.pop("usage", None)
        self.children_count.setText("")
        if hub != "running":
            self._note(self.children_list, self.t("desktop.waitingForHub"))
            return
        children = self.snapshot.children
        if not children:
            self._note(self.children_list, self.t("desktop.children.empty"))
            return
        self.children_count.setText(f"{sum(child.tone == 'ok' for child in children)}/{len(children)}")
        for child in children:
            item = UsageRow()
            # A connected child needs no word: its green dot and the count say it. Other states keep theirs.
            detail = "" if child.tone == "ok" else self.t.state_label(child.state)
            item.row.show_state(child.tone, child.server_id, detail)
            self.children_list.addWidget(item)
            self.child_rows[child.server_id] = item

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

    def _autostart_key(self) -> tuple[tuple[str, bool], ...] | None:
        return tuple(sorted(self.autostart.items())) if self.autostart is not None else None

    def _render_settings(self, hub: str | None) -> None:
        self.hub_page.sync(hub, self.hub_settings, self.autostart)
        self.mcps_page.sync(self.hub_settings)
        self.settings.show_pending(self.restart_needed and hub == "running")

    def _render_actions(self, hub: str | None) -> None:
        # One primary action: Start Hub while the Hub is down, Open dashboard otherwise.
        primary, secondary = (self.open_button, self.start_button) if hub == "running" else (self.start_button, self.open_button)
        set_props(primary, primary=True)
        set_props(secondary, primary=False)
        self.open_button.setEnabled(hub == "running")
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
        self.drain_timer.start()

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
            elif kind == "usage":
                self._apply_usage(*value)
            else:
                self.message_key = value
                self._render_footer()

    def apply(self, snapshot: status.HubSnapshot) -> None:
        if snapshot.hub == "stopped" and self.clock() < self._starting_until:
            snapshot = replace(snapshot, hub="starting")
        if snapshot.hub == "running":
            self._starting_until = 0.0
        else:
            # The next start reads hub.json anyway.
            self.restart_needed = False
        self.snapshot, self.message_key = snapshot, None
        self._sync_usage_stream()
        self.render()

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
        try:
            self.launch(arguments)
        except OSError:
            self.message_key = "desktop.error.controlUnavailable"
            self._render_footer()
            return
        # A new start reads hub.json again, so nothing waits for a restart any more.
        self.restart_needed = False
        if not starting:
            self._starting_until = 0.0
            self.render()
            return
        self._starting_until = self.clock() + theme.START_GRACE_SECONDS
        self.apply(replace(self.snapshot or status.HubSnapshot(hub="stopped"), hub="starting"))

    # Hub settings (hub.json) and sign-in tasks

    def load_tab(self, name: str) -> None:
        """Read what the opened tab shows; the Appearance tab needs nothing."""
        if name == "appearance" or self.settings.isHidden():
            return
        self._run("hub_settings", self.hub_store.read_settings, error_key="desktop.error.settings")
        if name == "hub":
            self._run("autostart", self.read_autostart, error_key="desktop.error.autostart")

    def _run(self, kind: str, action: Callable[..., Any], *args: Any, error_key: str) -> None:
        """Run file or task work off the Qt thread; the result (or an error message) comes back through the queue."""
        def job() -> None:
            try:
                self.results.put((kind, action(*args)))
            except (OSError, ValueError):
                self.results.put(("message", error_key))

        self.run_worker(job)

    def _write_hub_settings(self, write: Callable[[], None]) -> hub_settings.HubSettings:
        write()
        return self.hub_store.read_settings()

    def _settings_loaded(self, settings: hub_settings.HubSettings, *, changed: bool) -> None:
        self.hub_settings = settings
        if changed and self.snapshot is not None and self.snapshot.hub == "running":
            self.restart_needed = True
        self.render()

    def set_tracking(self, enabled: bool) -> None:
        if self.hub_settings is not None and self.hub_settings.activity_ui == enabled:
            return
        write = partial(self.hub_store.set_activity_ui, enabled)
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
    guard = None if args.screenshot is not None else win32.acquire_single_instance()
    if args.screenshot is None and guard is None:
        return 0
    app = application()
    if args.screenshot is not None:
        return run_screenshots(args)
    saved = prefs.load_prefs(prefs.PREFS_PATH)
    window = StatusWindow(
        Translator(saved.get("language") or system_language()),
        appearance=appearance_from(saved),
        prefs_path=prefs.PREFS_PATH,
    )
    window.show()
    window.restore_compact(saved, lambda x, y: QGuiApplication.screenAt(QPoint(x, y)) is not None)
    window.start()
    return app.exec()
