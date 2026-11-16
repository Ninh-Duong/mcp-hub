"""The live feeds of the status window: the usage and processing streams, started with the window and stopped when it closes."""

from __future__ import annotations

import threading
from typing import Any
from . import usage


class LiveFeedsMixin:
    """Usage and processing streams shown in StatusWindow (moved out of app.py)."""

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
