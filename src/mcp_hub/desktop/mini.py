"""The pinned mini bar: the whole status on one line, frameless and above other windows (compact mode).

While the Hub runs it shows one dot per child (blue while an AI client calls it), the connected
count, and the AI-client count, so the dots always match the count. Otherwise it shows the Hub's dot
and state word instead. Tooltips carry the names; dragging the bar moves the window.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel
from . import theme
from .widgets import Card, dot_icon, icon_button, label
from collections.abc import Callable
from typing import Any
from PySide6.QtGui import QPixmap
from qfluentwidgets import FluentIcon
from .widgets import current_palette, tone_pixmap


ChildDot = tuple[str, str, str]


def _pixmap(tone: str) -> QPixmap:
    """A tone's shape; `live` (an AI client calls the child) is a filled blue dot."""
    if tone == "live":
        return dot_icon(current_palette()["blue"], theme.GLYPH_COLUMN_WIDTH).pixmap(theme.GLYPH_COLUMN_WIDTH)
    return tone_pixmap(tone)


class MiniBar(Card):
    def __init__(self, *, on_expand: Callable[[], None]) -> None:
        super().__init__(None)
        self.content.setContentsMargins(theme.SPACE_M, theme.SPACE_XS, theme.SPACE_XS, theme.SPACE_XS)
        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_S)
        self.hub_dot = self._dot()
        self.hub_word = label(role="caption")
        self.dots_row = QHBoxLayout()
        self.dots_row.setSpacing(theme.SPACE_XS)
        self.count, self.clients = label(role="caption"), label(role="caption")
        self.expand_button = icon_button(FluentIcon.FULL_SCREEN, on_expand)
        row.addWidget(self.hub_dot)
        row.addWidget(self.hub_word)
        row.addLayout(self.dots_row)
        for widget in (self.count, self.clients, self.expand_button):
            row.addWidget(widget)
        self.content.addLayout(row)
        self.dots: dict[str, QLabel] = {}
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def _dot(self) -> QLabel:
        dot = label()
        dot.setFixedWidth(theme.GLYPH_COLUMN_WIDTH)
        dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return dot

    def show_state(self, hub: tuple[str, str] | None, children: list[ChildDot], count: str, clients: str) -> None:
        """`hub` is (tone, state word) while the Hub is not running, else None (the child dots stand for it).

        Dots are updated in place and rebuilt only when the set of children changes.
        """
        self.hub_dot.setVisible(hub is not None)
        self.hub_word.setVisible(hub is not None)
        if hub is not None:
            self._paint(self.hub_dot, *hub)
            self.hub_word.setText(hub[1])
        ids = tuple(server_id for server_id, _tone, _tip in children)
        if ids != tuple(self.dots):
            self._rebuild(ids)
        for server_id, tone, tooltip in children:
            self._paint(self.dots[server_id], tone, tooltip)
        self.count.setText(count)
        self.clients.setText(clients)
        self.clients.setVisible(bool(clients))

    def _paint(self, dot: QLabel, tone: str, tooltip: str) -> None:
        dot.setPixmap(_pixmap(tone))
        dot.setProperty("tone", tone)
        dot.setToolTip(tooltip)

    def _rebuild(self, ids: tuple[str, ...]) -> None:
        for dot in self.dots.values():
            dot.setParent(None)
            dot.deleteLater()
        self.dots = {}
        for server_id in ids:
            self.dots[server_id] = self._dot()
            self.dots_row.addWidget(self.dots[server_id])

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        """Dragging the bar moves the frameless window (the native move, so it snaps like a title bar)."""
        handle = self.window().windowHandle()
        if event.button() == Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)


class MiniMixin:
    """Compact (mini bar) mode of StatusWindow (moved out of app.py)."""

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
