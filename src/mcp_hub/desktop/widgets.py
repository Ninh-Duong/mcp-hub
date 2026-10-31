"""Small Qt building blocks of the desktop app: frosted cards, status rows, Settings pages, and the dot icon.

Colors and sizes come from the window stylesheet (theme.stylesheet) through object names and the
`role` / `tone` / `primary` / `small` properties, never from code here.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from . import theme


def set_props(widget: QWidget, **properties: object) -> None:
    """Set stylesheet properties and re-apply the style so the matching rules take effect."""
    for name, value in properties.items():
        widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def label(text: str = "", *, role: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


def button(command: Callable[[], None], *, small: bool = False) -> QPushButton:
    widget = QPushButton()
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setProperty("small", small)
    widget.clicked.connect(lambda _checked=False: command())
    return widget


class ConfirmButton(QPushButton):
    """Runs its command on a second click within theme.CONFIRM_MS; for actions that disconnect AI clients."""

    def __init__(self, command: Callable[[], None]) -> None:
        super().__init__()
        self.command = command
        self.armed = False
        self.label_text = self.confirm_text = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.timer = QTimer(self, singleShot=True, interval=theme.CONFIRM_MS, timeout=self.disarm)
        self.clicked.connect(lambda _checked=False: self.press())

    def set_texts(self, text: str, confirm_text: str) -> None:
        self.label_text, self.confirm_text = text, confirm_text
        self.setText(confirm_text if self.armed else text)

    def press(self) -> None:
        if self.armed:
            self.disarm()
            self.command()
            return
        self.armed = True
        self.setText(self.confirm_text)
        set_props(self, armed=True)
        self.timer.start()

    def disarm(self) -> None:
        self.timer.stop()
        self.armed = False
        self.setText(self.label_text)
        set_props(self, armed=False)


class Card(QFrame):
    """A rounded panel styled by the `QFrame#card` rule; frosted when the window has glass."""

    def __init__(self, width: int | None) -> None:
        super().__init__()
        self.setObjectName("card")
        if width is not None:  # None: as wide as its content (the mini bar)
            self.setFixedWidth(width)
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(theme.SPACE_M, theme.SPACE_M, theme.SPACE_M, theme.SPACE_M)
        self.content.setSpacing(theme.SPACE_XS)


class Page(QWidget):
    """One tab of the Settings card: a column of rows, each a caption on the left and a control on the right."""

    def __init__(self) -> None:
        super().__init__()
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(theme.SPACE_XS)

    def retranslate(self, t: Callable[..., str]) -> None:
        """Set every text in the current language; pages override it."""

    def segment_row(self, values: tuple[Any, ...], choose: Callable[[Any], None]) -> tuple[QLabel, dict[Any, QPushButton]]:
        """Checkable buttons of which one is on; `choose` gets the clicked value."""
        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_XS)
        caption = label(role="caption")
        row.addWidget(caption)
        row.addStretch(1)
        group = QButtonGroup(self)
        buttons: dict[Any, QPushButton] = {}
        for value in values:
            widget = QPushButton()
            widget.setCheckable(True)
            widget.setProperty("segment", True)
            widget.setCursor(Qt.CursorShape.PointingHandCursor)
            widget.clicked.connect(lambda _checked=False, chosen=value: choose(chosen))
            group.addButton(widget)
            row.addWidget(widget)
            buttons[value] = widget
        self.content.addLayout(row)
        return caption, buttons

    def slider_row(self, bounds: tuple[int, int], choose: Callable[[int], None]) -> tuple[QLabel, QLabel, QSlider]:
        header = QHBoxLayout()
        caption, value = label(role="caption"), label(role="caption")
        header.addWidget(caption)
        header.addStretch(1)
        header.addWidget(value)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(*bounds)
        slider.setCursor(Qt.CursorShape.PointingHandCursor)
        slider.valueChanged.connect(choose)
        self.content.addLayout(header)
        self.content.addWidget(slider)
        return caption, value, slider


class Row(QWidget):
    """A tone glyph in a fixed column, a name, and a detail (right-aligned, or right after the name)."""

    def __init__(self, *, role: str | None = None, detail_right: bool = True) -> None:
        super().__init__()
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(theme.SPACE_S)
        self.glyph = label(role=role)
        self.glyph.setFixedWidth(theme.GLYPH_COLUMN_WIDTH)
        self.glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = label(role=role)
        self.detail = label(role="caption")
        self.layout_.addWidget(self.glyph)
        self.layout_.addWidget(self.text)
        if detail_right:
            self.layout_.addStretch(1)
            self.layout_.addWidget(self.detail)
        else:
            self.layout_.addWidget(self.detail)
            self.layout_.addStretch(1)

    def show_state(self, tone: str, text: str, detail: str = "") -> None:
        self.glyph.setText(theme.TONE_GLYPHS[tone])
        set_props(self.glyph, tone=tone)
        self.text.setText(text)
        self.detail.setText(detail)


def show_elided(widget: QLabel, text: str, width: int, tooltip: str = "") -> None:
    """One line of at most `width` px, cut with an ellipsis; the tooltip holds the full detail."""
    widget.setText(widget.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width))
    widget.setToolTip(tooltip or (text if widget.text() != text else ""))


class UsageRow(QWidget):
    """A child's status Row with a short line under its name, shown only while an AI client uses it."""

    def __init__(self) -> None:
        super().__init__()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.row = Row()
        self.usage = label(role="caption")
        # Starts under the name, past the glyph column and the row spacing.
        self.usage.setContentsMargins(theme.GLYPH_COLUMN_WIDTH + theme.SPACE_S, 0, 0, 0)
        self.usage.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.usage.hide()
        column.addWidget(self.row)
        column.addWidget(self.usage)

    def show_usage(self, text: str, tooltip: str, role: str) -> None:
        """Show `text` on one line with `tooltip` as the detail; an empty text hides the line and its space."""
        if not text:
            self.usage.hide()
            return
        set_props(self.usage, role=role)
        show_elided(self.usage, text, theme.WINDOW_WIDTH - 2 * theme.SPACE_M - theme.GLYPH_COLUMN_WIDTH - theme.SPACE_S,
                    tooltip)
        self.usage.show()


def dot_icon(color: str, size: int = theme.ICON_SIZE) -> QIcon:
    """An antialiased filled circle on a transparent square."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pixmap)
