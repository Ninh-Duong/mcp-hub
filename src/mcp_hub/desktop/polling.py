"""Polling of the Hub's status in the status window: the background refresh, the results queue, and the pace of polls."""

from __future__ import annotations

from .constants import START_TIMEOUT_KEY, STICKY_ERROR_KEYS

import queue
import threading
from dataclasses import replace
from . import status, theme


class PollingMixin:
    """Polling and snapshot handling of StatusWindow (moved out of app.py)."""

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
            elif kind in ("config_opened", "config_failed"):
                self._config_result(kind, value)
            elif kind == "control_failed":
                self._control_failed()
            elif kind in ("update_check", "update_status", "update_state"):
                self._update_changed(kind, value)
            elif kind == "usage":
                self._apply_usage(*value)
            elif kind == "processing":
                self._apply_processing(*value)
            elif kind in ("bitnet_install", "approval_done"):
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
        elif snapshot.hub == "running" and self.settings.tab == "config" and not self.settings.isHidden():
            self.load_tab("config")  # a saved file shows its state on the next read

    def _pace_polling(self, snapshot: status.HubSnapshot) -> None:
        """Read the Hub every second while BitNet installs or starts, so the percent and the dot move."""
        busy = snapshot.bitnet is not None and snapshot.bitnet.tone == "wait"
        interval = theme.BUSY_POLL_INTERVAL_MS if busy else theme.POLL_INTERVAL_MS
        if self.poll_timer.interval() != interval:
            self.poll_timer.setInterval(interval)
