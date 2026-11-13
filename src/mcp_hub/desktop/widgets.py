"""Building blocks of the desktop app: frosted cards, status rows, Settings pages, rolling logs, and icons.

Text and buttons come from qfluentwidgets (Fluent 2 look); the frosted cards and their colors come from the window
stylesheet (theme.stylesheet) through object names and the `role` / `tone` properties.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    InfoBarIcon, PrimaryPushButton, PushButton, SegmentedWidget, Slider, TransparentToolButton, isDarkTheme,
)

from . import theme

# The tone icons: a shape per tone, drawn by qfluentwidgets. `off` is painted (see tone_pixmap).
TONE_ICONS = {"ok": InfoBarIcon.SUCCESS, "wait": InfoBarIcon.WARNING, "error": InfoBarIcon.ERROR}
# A destructive button after its first click (Restart, Stop), same red in both themes.
ARMED_RED = "#c42b1c"


def current_palette() -> dict[str, str]:
    """The palette of the theme Fluent is using now, so painted icons match the widgets around them."""
    return theme.PALETTES["dark" if isDarkTheme() else "light"]


def set_props(widget: QWidget, **properties: object) -> None:
    """Set dynamic properties and re-apply the style so the matching stylesheet rules take effect."""
    for name, value in properties.items():
        widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def label(text: str = "", *, role: str | None = None, wrap: bool = False) -> QLabel:
    """A label whose type and color come from its `role` in the window stylesheet.

    Labels stay plain QLabel: qfluentwidgets restyles every label it owns on each role change, which made
    the window's status updates several times slower.
    """
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


def button(command: Callable[[], None], *, primary: bool = False) -> PushButton:
    """A Fluent push button; `primary` is the one blue action of a view (system accent)."""
    widget = PrimaryPushButton() if primary else PushButton()
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(lambda _checked=False: command())
    return widget


def icon_button(icon: Any, command: Callable[[], None], tooltip: str = "") -> TransparentToolButton:
    """A Fluent icon-only button; `tooltip` is the text of its locale key, so it is always readable."""
    widget = TransparentToolButton()
    widget.setIcon(icon)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setToolTip(tooltip)
    widget.clicked.connect(lambda _checked=False: command())
    return widget


class ConfirmButton(PushButton):
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
        self.setProperty("armed", True)
        self.setStyleSheet(f"PushButton {{ background: {ARMED_RED}; color: #ffffff; }}")
        self.timer.start()

    def disarm(self) -> None:
        self.timer.stop()
        self.armed = False
        self.setText(self.label_text)
        self.setProperty("armed", False)
        self.setStyleSheet("")


class Card(QFrame):
    """A rounded panel styled by the `QFrame#card` rule; frosted when the window has glass.

    Not qfluentwidgets' SimpleCardWidget: that paints an opaque background, which would hide the glass.
    """

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

    def segment_row(self, values: tuple[Any, ...], choose: Callable[[Any], None],
                    caption: QLabel | None = None) -> tuple[QLabel, SegmentedWidget]:
        """A Fluent segmented control with one segment per value (its route key is `str(value)`); `choose` gets the value.

        `caption` is the label on the left (a caption by default).
        """
        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_XS)
        caption = caption or label(role="caption")
        row.addWidget(caption)
        row.addStretch(1)
        segmented = SegmentedWidget(self)
        for value in values:
            segmented.addItem(str(value), "", onClick=lambda _checked=False, chosen=value: choose(chosen))
        row.addWidget(segmented)
        self.content.addLayout(row)
        return caption, segmented

    def slider_row(self, bounds: tuple[int, int], choose: Callable[[int], None]) -> tuple[QLabel, QLabel, Slider]:
        header = QHBoxLayout()
        caption, value = label(role="caption"), label(role="caption")
        header.addWidget(caption)
        header.addStretch(1)
        header.addWidget(value)
        slider = Slider(Qt.Orientation.Horizontal)
        slider.setRange(*bounds)
        slider.setCursor(Qt.CursorShape.PointingHandCursor)
        slider.valueChanged.connect(choose)
        self.content.addLayout(header)
        self.content.addWidget(slider)
        return caption, value, slider


def segment(segmented: SegmentedWidget, value: Any) -> Any:
    """The segment item of one value: set its text, enable it, or hover it."""
    return segmented.widget(str(value))


def select_segment(segmented: SegmentedWidget, value: Any) -> None:
    segmented.setCurrentItem(str(value))


def tone_pixmap(tone: str, size: int = theme.GLYPH_COLUMN_WIDTH) -> QPixmap:
    """The shape of a tone: a Fluent icon, or for `off` an outlined ring in the muted color."""
    if tone in TONE_ICONS:
        return TONE_ICONS[tone].icon().pixmap(size, size)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(current_palette()["muted"]), 1.5))
    painter.drawEllipse(QRectF(2, 2, size - 4, size - 4))
    painter.end()
    return pixmap


class Row(QWidget):
    """A tone icon in a fixed column, a name, and a detail (right-aligned, or right after the name)."""

    def __init__(self, *, role: str | None = None, detail_right: bool = True) -> None:
        super().__init__()
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(theme.SPACE_S)
        self.glyph = QLabel()
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
        self.glyph.setPixmap(tone_pixmap(tone))
        self.glyph.setProperty("tone", tone)
        self.text.setText(text)
        self.detail.setText(detail)


class Spinner:
    """Turns the `wait` icon a quarter each frame while something runs; a hidden label is left alone."""

    def __init__(self, glyph: QLabel) -> None:
        self.glyph = glyph
        self.frame = 0
        self.timer = QTimer(glyph, interval=theme.SPINNER_MS, timeout=self.tick)

    @property
    def running(self) -> bool:
        return self.timer.isActive()

    def start(self) -> None:
        if not self.timer.isActive():
            self.timer.start()
        self.tick()

    def stop(self) -> None:
        self.timer.stop()

    def tick(self) -> None:
        if not self.glyph.isVisible() and self.frame:  # hidden after it has run: leave it alone
            return
        self.glyph.setPixmap(arc_pixmap(self.frame))
        self.frame += 1


def arc_pixmap(frame: int, size: int = theme.GLYPH_COLUMN_WIDTH) -> QPixmap:
    """A quarter arc turned to the frame's quarter turn: the busy indicator in the `wait` color."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(current_palette()["amber"]), 2))
    painter.drawArc(QRectF(2, 2, size - 4, size - 4), (frame % 4) * 90 * 16, 90 * 16)
    painter.end()
    return pixmap


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


class FadingLog(QWidget):
    """A rolling log of at most theme.LOG_ROWS one-line rows: newest on top, older rows slide down and fade.

    Rows are positioned by hand, so native movement cannot fight a layout. The height stays that of
    LOG_ROWS rows, so a card does not grow or shrink as lines arrive.
    """

    def __init__(self, indent: int = 0) -> None:
        super().__init__()
        self.indent = indent
        self.keys: tuple = ()
        self.rows: list[QLabel] = []
        self.animations: list[QPropertyAnimation] = []
        self._shown: tuple | None = None
        self.setFixedHeight(self.row_height() * theme.LOG_ROWS)
        self.hide()

    def row_height(self) -> int:
        return self.fontMetrics().height() + theme.SPACE_XS

    def show_lines(self, lines: tuple[tuple[Any, str], ...]) -> None:
        """`lines` are `(key, text)`, newest first; a key that moves keeps its row and slides it."""
        lines = tuple(lines)[:theme.LOG_ROWS]
        if lines == self._shown:
            return
        self._shown = lines
        positions = dict(zip(self.keys, (row.pos() for row in self.rows)))
        self._clear()
        self.keys = tuple(key for key, _text in lines)
        height = self.row_height()
        for index, (key, text) in enumerate(lines):
            row = self._row(text, index, height)
            if key in positions and positions[key] != row.pos():
                self._slide(row, positions[key])
            self.rows.append(row)
        self.setVisible(bool(lines))

    def _clear(self) -> None:
        for animation in self.animations:
            animation.stop()
            animation.deleteLater()
        self.animations = []
        for row in self.rows:
            row.hide()
            row.deleteLater()
        self.rows = []

    def _slide(self, row: QLabel, start: Any) -> None:
        animation = QPropertyAnimation(row, b"pos", self)
        animation.setDuration(theme.SPINNER_MS)
        animation.setStartValue(start)
        animation.setEndValue(row.pos())
        animation.start()
        self.animations.append(animation)

    def _row(self, text: str, index: int, height: int) -> QLabel:
        row = label(role="caption")
        row.setParent(self)
        width = theme.WINDOW_WIDTH - 2 * theme.SPACE_M - self.indent
        row.setGeometry(self.indent, index * height, width, height)
        show_elided(row, text, width)
        opacity = QGraphicsOpacityEffect(row)
        opacity.setOpacity(theme.LOG_FADE[index])
        row.setGraphicsEffect(opacity)
        row.show()
        return row


class ProcessingLog(FadingLog):
    """BitNet's processing events as a FadingLog, indented under the BitNet row."""

    def __init__(self) -> None:
        super().__init__(indent=theme.GLYPH_COLUMN_WIDTH + theme.SPACE_S)
        self.events: tuple = ()

    def show_events(self, events: tuple, t: Callable[..., str]) -> None:
        self.events = tuple(events)[:theme.LOG_ROWS]
        self.show_lines(tuple((event.sequence, self._event_text(event, t)) for event in self.events))

    @staticmethod
    def _event_text(event: Any, t: Callable[..., str]) -> str:
        text = t("desktop.processing.line", operation=t(f"desktop.processing.operation.{event.operation}"),
                 state=t(f"desktop.processing.state.{event.state}"))
        if event.source:
            text = t("desktop.processing.sourceLine", text=text, source=t(f"desktop.processing.source.{event.source}"))
        if event.server_id:
            text += " · " + event.server_id
        if event.tool_name:
            text += " · " + event.tool_name
        return text


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
