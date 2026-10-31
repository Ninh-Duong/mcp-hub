"""The pinned mini bar: the whole status on one line, frameless and above other windows (compact mode).

While the Hub runs it shows one dot per child (blue while an AI client calls it), the connected
count, and the AI-client count, so the dots always match the count. Otherwise it shows the Hub's dot
and state word instead. Tooltips carry the names; dragging the bar moves the window.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel

from . import theme
from .widgets import Card, button, label, set_props

# (server ID, tone or "live", tooltip) for one child dot.
ChildDot = tuple[str, str, str]


def _glyph(tone: str) -> str:
    return theme.LIVE_GLYPH if tone == "live" else theme.TONE_GLYPHS[tone]


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
        self.expand_button = button(on_expand, small=True)
        self.expand_button.setText(theme.EXPAND_GLYPH)
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
        dot.setText(_glyph(tone))
        set_props(dot, tone=tone)
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
