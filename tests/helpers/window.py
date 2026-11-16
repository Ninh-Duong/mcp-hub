"""Shared setup for the desktop window tests: Qt on the offscreen platform, the app object, and helpers."""
from __future__ import annotations
import os
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from mcp_hub.desktop import cli, app, hub_page, mini, screenshot, settings, system, update_card, widgets
    from qfluentwidgets import HyperlinkButton, PrimaryPushButton, TransparentToolButton
    QT_MISSING = None
except ImportError as exc:  # Qt's system libraries are missing (for example a bare Linux host)
    QT_MISSING = f"Qt is unavailable: {exc}"

from mcp_hub.desktop.hub_api import HubSettings, ServerSwitch, UpdateStatus
from mcp_hub.desktop.status import ChildRow, HubSnapshot

__all__ = [
    "ChildRow",
    "WindowFakeStore",
    "HubSettings",
    "HubSnapshot",
    "HyperlinkButton",
    "Path",
    "PrimaryPushButton",
    "QApplication",
    "QTest",
    "Qt",
    "ServerSwitch",
    "TransparentToolButton",
    "UpdateStatus",
    "__all__",
    "app",
    "assert_rows_fit",
    "checked",
    "cli",
    "datetime",
    "elided",
    "enabled",
    "fake_switch",
    "full_text",
    "hub_page",
    "mini",
    "os",
    "pick",
    "qt_app",
    "running",
    "screenshot",
    "settings",
    "system",
    "texts",
    "unittest",
    "update_card",
    "widgets",
]




def qt_app() -> None:
    if QT_MISSING is None:
        app.application()


def running(*children: ChildRow, activity_ui: bool = False, clients: tuple[str, ...] = (),
            recent: tuple[str, ...] = ()) -> HubSnapshot:
    return HubSnapshot(hub="running", activity_ui=activity_ui, children=children, clients=clients, recent=recent,
                       checked_at=datetime(2026, 10, 30, 10, 42, 5))


def fake_switch(server_id: str, enabled: bool) -> ServerSwitch:
    """mcp-server-logs needs credentials and a key; the other child needs nothing."""
    if server_id == "mcp-server-logs":
        return ServerSwitch(server_id, enabled, "credentials required", missing=("openobserve.dev.token",),
                            requirements="declared", config_file_exists=True)
    return ServerSwitch(server_id, enabled, None)


def elided(widget, text: str, width: int | None = None) -> str:
    """`text` as show_elided cuts it for this one-line label (fonts differ by platform)."""
    return widget.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width or widget.width())


def full_text(label) -> str:
    """A label's whole text: show_elided keeps it in the tooltip when the label is cut."""
    return label.toolTip() or label.text()


def assert_rows_fit(test: unittest.TestCase, rows) -> None:
    """Every Row's shown text and detail fit the row's width, whatever the font: cut text never overflows."""
    for index in range(rows.count()):
        widget = rows.itemAt(index).widget()
        row = widget.row if isinstance(widget, widgets.UsageRow) else widget
        if isinstance(row, widgets.Row):
            used = sum(part.fontMetrics().horizontalAdvance(part.text()) for part in (row.text, row.detail))
            test.assertLessEqual(used, widgets.row_room() + widgets.theme.SPACE_S, row.text.text())


def texts(rows, *, whole: bool = False) -> list[str]:
    """Visible text of each item in a rows layout: `tone name detail` for a Row, else the label text.

    With `whole`, each label's full text instead of the elided one.
    """
    read = full_text if whole else (lambda label: label.text())
    found: list[str] = []
    for index in range(rows.count()):
        widget = rows.itemAt(index).widget()
        if isinstance(widget, widgets.UsageRow):
            widget = widget.row
        if isinstance(widget, widgets.Row):
            parts = (widget.glyph.property("tone") or "", read(widget.text), read(widget.detail))
            found.append(" ".join(part for part in parts if part))
        elif widget is not None and not isinstance(widget, widgets.ProcessingLog):
            found.append(read(widget))
    return found


def checked(segmented, value) -> bool:
    """Whether `value` is the selected segment of a SegmentedWidget."""
    return segmented.currentRouteKey() == str(value)


def pick(segmented, value) -> None:
    """Click one segment, as the user does."""
    widgets.segment(segmented, value).click()


def enabled(segmented, value) -> bool:
    return widgets.segment(segmented, value).isEnabled()


class WindowFakeStore:
    """HubClient stand-in: keeps the saved choices in memory."""

    def __init__(self) -> None:
        self.activity_ui, self.editable, self.fail = False, True, False
        self.bitnet = False
        self.reads = 0
        self.enabled = {"mcp-server-logs": True, "mcp-jira-sync": False}
        self.update = UpdateStatus("", "idle", (), ())
        self.retries: list[tuple[str, ...]] = []
        self.prepared: list[str] = []
        self.prepare_error: Exception | None = None
        self.config_file = Path("/private/mcps/mcp-server-logs.json")

    def check_updates(self):
        return self.update

    def read_update(self):
        return self.update

    def read_saved_update(self):
        return self.update

    def start_update(self):
        self.update = UpdateStatus("run-1", "complete", (), ())
        return self.update

    def retry_update(self, components):
        self.retries.append(components)
        self.update = UpdateStatus("run-1", "complete", (), ())
        return self.update

    def read_settings(self) -> HubSettings:
        self.reads += 1
        if self.fail:
            raise OSError("locked")
        servers = tuple(fake_switch(server_id, enabled) for server_id, enabled in self.enabled.items())
        return HubSettings(self.activity_ui, servers, self.editable, bitnet=self.bitnet)

    def prepare_child_config(self, server_id: str) -> str:
        if self.prepare_error is not None:
            raise self.prepare_error
        self.prepared.append(server_id)
        return str(self.config_file)

    def set_activity_ui(self, enabled: bool) -> None:
        self.activity_ui = enabled

    def set_bitnet(self, enabled: bool) -> None:
        self.bitnet = enabled

    installs = 0
    fail_install = False

    def install_bitnet(self) -> None:
        if self.fail_install:
            raise OSError("refused")
        self.installs += 1

    def set_server_enabled(self, server_id: str, enabled: bool) -> None:
        self.enabled[server_id] = enabled
