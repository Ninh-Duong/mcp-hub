"""Desktop widgets: a status Row elides its state and detail, and a Settings switch row keeps its value and enabled state."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QLabel
    from qfluentwidgets import SwitchButton

    from mcp_hub.desktop import app, i18n, theme, widgets
    QT_MISSING = None
except ImportError as exc:  # Qt's system libraries are missing (for example a bare Linux host)
    QT_MISSING = f"Qt is unavailable: {exc}"

ELLIPSIS = "…"
# A Vietnamese state and detail long enough to be cut at the window width in any font.
LONG_STATE = "Running but not answering client requests for a long time " * 2
LONG_DETAIL = "fast mode, queue full, retry in a few minutes, see the log for details " * 2


def setUpModule() -> None:
    if QT_MISSING is None:
        app.application()


def room() -> int:
    """The width a Row's text and detail share: the window's inner width less the glyph column and its spacing."""
    return theme.WINDOW_WIDTH - 2 * theme.SPACE_M - theme.GLYPH_COLUMN_WIDTH - theme.SPACE_S


@unittest.skipIf(QT_MISSING, QT_MISSING)
class RowShowStateTest(unittest.TestCase):
    def test_long_translated_text_is_cut_and_never_overflows_the_row(self) -> None:
        hint = i18n.Translator("vi")("desktop.clients.hint")  # a real Vietnamese string from the locale file
        row = widgets.Row()
        row.show_state("wait", hint, hint)
        shown = row.text.text()
        self.assertTrue(shown == hint or shown.endswith(ELLIPSIS), shown)
        self.assertLessEqual(row.text.fontMetrics().horizontalAdvance(shown), room())

    def test_long_vietnamese_state_and_detail_are_cut_with_full_text_in_tooltips(self) -> None:
        row = widgets.Row()
        row.show_state("wait", LONG_STATE, LONG_DETAIL)
        self.assertTrue(row.text.text().endswith(ELLIPSIS))
        self.assertTrue(row.detail.text().endswith(ELLIPSIS))
        self.assertLessEqual(row.text.fontMetrics().horizontalAdvance(row.text.text()), room())
        self.assertLessEqual(row.detail.fontMetrics().horizontalAdvance(row.detail.text()), room())
        self.assertEqual(row.text.toolTip(), LONG_STATE)
        self.assertEqual(row.detail.toolTip(), LONG_DETAIL)

    def test_short_state_and_detail_are_whole_without_tooltips(self) -> None:
        row = widgets.Row()
        row.show_state("ok", "Run", "2 clients")
        self.assertEqual(row.text.text(), "Run")
        self.assertEqual(row.detail.text(), "2 clients")
        self.assertEqual(row.text.toolTip(), "")
        self.assertEqual(row.detail.toolTip(), "")

    def test_short_state_stays_whole_when_the_detail_is_cut(self) -> None:
        row = widgets.Row()
        row.show_state("ok", "Run", LONG_DETAIL)
        self.assertEqual(row.text.text(), "Run")
        self.assertEqual(row.text.toolTip(), "")
        self.assertTrue(row.detail.text().endswith(ELLIPSIS))
        self.assertEqual(row.detail.toolTip(), LONG_DETAIL)

    def test_empty_detail_clears_the_previous_one(self) -> None:
        row = widgets.Row()
        row.show_state("error", "Failed", LONG_DETAIL)
        row.show_state("error", "Failed")
        self.assertEqual(row.detail.text(), "")
        self.assertEqual(row.detail.toolTip(), "")

    def test_glyph_accessible_name_is_the_state_word(self) -> None:
        row = widgets.Row()
        row.show_state("ok", "Running", "2 clients")
        self.assertEqual(row.glyph.accessibleName(), "Running")
        self.assertEqual(row.glyph.property("tone"), "ok")
        row.show_state("off", "Stopped")
        self.assertEqual(row.glyph.accessibleName(), "Stopped")


@unittest.skipIf(QT_MISSING, QT_MISSING)
class SwitchRowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.page = widgets.Page()
        self.toggled: list[bool] = []
        self.caption, self.switch = self.page.switch_row(self.toggled.append)

    def test_switch_row_returns_a_caption_and_a_fluent_switch(self) -> None:
        self.assertIsInstance(self.caption, QLabel)
        self.assertIsInstance(self.switch, SwitchButton)
        self.assertTrue(self.switch.isEnabled())
        self.assertFalse(self.switch.isChecked())

    def test_none_shows_off_and_disabled(self) -> None:
        widgets.set_switch(self.switch, True, True)
        widgets.set_switch(self.switch, None, enabled=True)
        self.assertFalse(self.switch.isChecked())
        self.assertFalse(self.switch.isEnabled())

    def test_value_sets_checked_and_enabled_by_the_flag(self) -> None:
        widgets.set_switch(self.switch, True, True)
        self.assertTrue(self.switch.isChecked())
        self.assertTrue(self.switch.isEnabled())
        widgets.set_switch(self.switch, False, False)
        self.assertFalse(self.switch.isChecked())
        self.assertFalse(self.switch.isEnabled())

    def test_programmatic_set_does_not_call_on_toggle(self) -> None:
        widgets.set_switch(self.switch, True, True)
        widgets.set_switch(self.switch, None, True)
        self.assertEqual(self.toggled, [])

    def test_user_change_calls_on_toggle_with_the_new_value(self) -> None:
        self.switch.setChecked(True)
        self.assertEqual(self.toggled, [True])


if __name__ == "__main__":
    unittest.main()
