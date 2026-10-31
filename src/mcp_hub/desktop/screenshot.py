"""`python -m mcp_hub.desktop --screenshot DIR`: render sample states and save one PNG each for UI review.

The window shows fixed sample data (never the live Hub), so a review never stops or restarts anything.
On Windows the capture reads the screen as DWM composes it, so the PNGs include the real backdrop
blur; elsewhere the window renders itself (`QWidget.grab`), without a backdrop.
"""
from __future__ import annotations

import ctypes
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from ..config import ServerSwitch
from ..hub_settings import HubSettings
from . import win32
from .status import ChildRow, HubSnapshot
from .usage import UsageBoard

SETTLE_SECONDS = 0.6
_SAMPLE_TIME = datetime(2026, 10, 31, 9, 30, 0)
_SAMPLE_CHILDREN = (
    ChildRow("mcp-server-logs", "connected", "ok"),
    ChildRow("mcp-jira-sync", "connected", "ok"),
    ChildRow("mcp-pr-companion", "reconnecting", "wait"),
    ChildRow("mcp-read-source", "disabled", "off"),
)
SAMPLES = {
    "running": HubSnapshot(hub="running", children=_SAMPLE_CHILDREN, checked_at=_SAMPLE_TIME),
    "ui": HubSnapshot(hub="running", activity_ui=True, children=_SAMPLE_CHILDREN,
                      clients=("Codex ×2", "Antigravity"), client_names=("Codex", "Antigravity"),
                      recent=("claude-code",), checked_at=_SAMPLE_TIME),
    "starting": HubSnapshot(hub="starting", checked_at=_SAMPLE_TIME),
    "stopped": HubSnapshot(hub="stopped", checked_at=_SAMPLE_TIME),
    "not_installed": HubSnapshot(hub="not_installed", checked_at=_SAMPLE_TIME),
    "unauthorized": HubSnapshot(hub="unauthorized", checked_at=_SAMPLE_TIME),
}
# The settings states show the running sample with one Settings tab open.
SETTINGS_TABS = {"settings": "appearance", "settings-hub": "hub", "settings-mcps": "mcps"}
# `compact` shows the `ui` sample as the pinned mini bar.
STATES = (*SAMPLES, *SETTINGS_TABS, "compact")
SAMPLE_HUB_SETTINGS = HubSettings(
    activity_ui=False,
    servers=(
        ServerSwitch("mcp-server-logs", True), ServerSwitch("mcp-jira-sync", True),
        ServerSwitch("mcp-pr-companion", True, "credentials required"), ServerSwitch("mcp-read-source", False),
    ),
    editable=True,
)
SAMPLE_AUTOSTART = {"hub": True, "app": True}


def sample_usage(now: datetime) -> UsageBoard:
    """Two clients on the logs child right now, and claude-code on Jira a moment ago (the `ui` sample)."""
    board = UsageBoard()
    for call_id, client in ((1, "Codex"), (2, "Antigravity")):
        board.apply_event({"server_id": "mcp-server-logs", "call_id": call_id, "state": "started",
                           "client": client, "tool": "query_logs"}, now)
    board.apply_event({"server_id": "mcp-jira-sync", "call_id": 3, "state": "succeeded", "client": "claude-code",
                       "tool": "jira_read_ticket", "at": (now - timedelta(seconds=20)).isoformat()}, now)
    return board


class SampleSettings:
    """Fixed Hub settings for screenshots; changes are ignored, so hub.json is never written."""

    def read_settings(self) -> HubSettings:
        return SAMPLE_HUB_SETTINGS

    def set_activity_ui(self, _enabled: bool) -> None:
        return None

    def set_server_enabled(self, _server_id: str, _enabled: bool) -> None:
        return None


def window_image(window: Any, screen_capture: bool | None = None) -> QImage:
    """The window as shown on screen (Windows, with the blur), or as it renders itself elsewhere."""
    if screen_capture is None:
        screen_capture = hasattr(ctypes, "WinDLL")
    if not screen_capture:
        return window.grab().toImage()
    width, height, pixels = win32.capture_screen(win32.window_rect(int(window.winId())))
    return QImage(pixels, width, height, width * 4, QImage.Format.Format_RGB32).copy()


def save_window_png(window: Any, path: Path) -> None:
    if not window_image(window).save(str(path), "PNG"):
        raise OSError(f"could not write {path.name}")


def _settle(_window: Any) -> None:
    """Let Qt paint and DWM compose the backdrop before the capture."""
    QApplication.processEvents()
    time.sleep(SETTLE_SECONDS)
    QApplication.processEvents()


def _show_settings(window: Any, tab: str | None) -> None:
    if tab is None:
        window.show_settings(False)
        return
    window.show_tab(tab)
    window.process_results()


def capture_all(
    window: Any,
    directory: Path,
    states: list[str],
    languages: list[str],
    themes: list[str],
    *,
    capture: Callable[[Any, Path], None] = save_window_png,
    settle: Callable[[Any], None] = _settle,
) -> list[Path]:
    """Save `<state>-<language>-<theme>.png` for every combination; returns the paths written."""
    directory.mkdir(parents=True, exist_ok=True)
    window.show_for_capture()
    saved: list[Path] = []
    for theme_name in themes:
        window.set_theme(theme_name)
        for language in languages:
            window.set_language(language)
            for state in states:
                window.set_compact(state == "compact")
                window.apply(SAMPLES["ui"] if state == "compact" else SAMPLES.get(state, SAMPLES["running"]))
                window.preview_usage(sample_usage(window.now()) if state in ("ui", "compact") else UsageBoard())
                _show_settings(window, SETTINGS_TABS.get(state))
                settle(window)
                path = directory / f"{state}-{language}-{theme_name}.png"
                capture(window, path)
                saved.append(path)
    return saved
