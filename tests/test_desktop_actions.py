"""Desktop status window actions and copy: one primary action per Hub state, section titles, spacing,
the sample release, the hint text, and the mode caption. Offline, on Qt's offscreen platform."""
from __future__ import annotations

import os
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QLabel
    from qfluentwidgets import PrimaryPushButton

    from mcp_hub.desktop import app, screenshot, theme
    QT_MISSING = None
except ImportError as exc:  # Qt's system libraries are missing (for example a bare Linux host)
    QT_MISSING = f"Qt is unavailable: {exc}"

from mcp_hub.desktop import i18n
from mcp_hub.desktop.status import HubSnapshot


def setUpModule() -> None:
    if QT_MISSING is None:
        app.application()


@unittest.skipIf(QT_MISSING is not None, QT_MISSING or "")
class HubStateActionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # A saved update run on this machine would show Retry; the window must start from no saved run.
        with patch.object(app.hub_api.HubClient, "read_saved_update", return_value=None):
            self.window = app.StatusWindow(
                i18n.Translator("en"), fetch=lambda: HubSnapshot(hub="stopped"), open_dashboard=lambda: None,
                launch=lambda _arguments: None, style=lambda *_args, **_kwargs: False,
                prefs_path=Path(self.tmp.name) / "desktop.json", hub_store=screenshot.SampleSettings(),
                read_autostart=lambda: {"hub": True, "app": True},
                write_autostart=lambda _target, _enabled: {"hub": True, "app": True},
                run_worker=lambda job: job(), stream=lambda _put, _stop: None,
                processing_stream=lambda _put, _stop: None,
            )
        self.addCleanup(self.window.close)
        self.en = i18n.Translator("en")

    def visible_primaries(self) -> list:
        """Every primary push button that is shown and enabled in the window."""
        return [widget for widget in self.window.findChildren(PrimaryPushButton)
                if widget.isVisibleTo(self.window) and widget.isEnabled()]

    def open_shown(self) -> list:
        return [widget for widget in (self.window.open_button, self.window.open_plain_button)
                if widget.isVisibleTo(self.window)]

    def label_with_text(self, text: str) -> QLabel:
        return next(widget for widget in self.window.findChildren(QLabel) if widget.text() == text)

    def test_stopped_hub_has_exactly_one_primary_action_start(self) -> None:
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertEqual(self.visible_primaries(), [self.window.start_button])

    def test_running_hub_has_exactly_one_primary_action_open_dashboard(self) -> None:
        self.window.apply(HubSnapshot(hub="running"))
        self.assertEqual(self.visible_primaries(), [self.window.open_button])

    def test_update_available_while_running_makes_update_the_only_primary(self) -> None:
        self.window.apply(HubSnapshot(hub="running"))
        self.window.preview_update(screenshot.SAMPLE_RELEASE)
        self.assertEqual(self.visible_primaries(), [self.window.update_card.update_primary])
        self.assertEqual(len(self.open_shown()), 1)
        self.assertFalse(isinstance(self.open_shown()[0], PrimaryPushButton))

    def test_update_available_while_stopped_keeps_start_as_the_only_primary(self) -> None:
        self.window.apply(HubSnapshot(hub="stopped"))
        self.window.preview_update(screenshot.SAMPLE_RELEASE)
        self.assertEqual(self.visible_primaries(), [self.window.start_button])
        self.assertTrue(self.window.update_card.update_button.isVisibleTo(self.window))

    def test_start_or_open_is_shown_by_hub_state(self) -> None:
        for hub, start_shown in (("stopped", True), ("starting", True), ("running", False)):
            with self.subTest(hub=hub):
                self.window.apply(HubSnapshot(hub=hub))
                self.assertEqual(self.window.start_button.isVisibleTo(self.window), start_shown)
                self.assertEqual(len(self.open_shown()), 0 if start_shown else 1)

    def test_card_titles_share_the_section_role(self) -> None:
        self.window.apply(HubSnapshot(hub="running"))
        self.window.preview_update(screenshot.SAMPLE_RELEASE)
        for key in ("desktop.section.children", "desktop.section.clients", "desktop.update.title"):
            with self.subTest(key=key):
                self.assertEqual(self.label_with_text(self.en(key)).property("role"), "section")

    def test_update_version_line_is_body_text(self) -> None:
        self.window.preview_update(screenshot.SAMPLE_RELEASE)
        line = self.label_with_text(self.en("desktop.update.available", version=screenshot.SAMPLE_RELEASE.label))
        self.assertTrue(line.isVisibleTo(self.window))
        self.assertFalse(line.property("role"))

    def test_cards_are_separated_by_space_m(self) -> None:
        self.assertEqual(self.window.outer.spacing(), theme.SPACE_M)

    def test_sample_release_is_newer_than_the_sample_running_version(self) -> None:
        running = tuple(int(part) for part in screenshot.SAMPLE_CURRENT.split("."))
        self.assertGreater(screenshot.SAMPLE_RELEASE.version, running)

    def test_hint_has_no_emoji_in_either_language(self) -> None:
        for language in ("en", "vi"):
            text = i18n.Translator(language)("desktop.clients.hint")
            with self.subTest(language=language):
                self.assertFalse([char for char in text if unicodedata.category(char) == "So" or char == "️"])
        self.assertIn("Settings → Hub → Track AI clients", self.en("desktop.clients.hint"))
        hint = i18n.Translator("vi")("desktop.clients.hint")
        self.assertIn("→", hint)
        self.assertNotIn("⚙", hint)  # the emoji gear is gone from both locales

    def test_mode_caption_is_its_own_line_and_the_state_word_stays_whole(self) -> None:
        self.window.show()
        QApplication.processEvents()
        for language in ("en", "vi"):
            with self.subTest(language=language):
                self.window.set_language(language)
                self.window.apply(HubSnapshot(hub="running", activity_ui=True))
                QApplication.processEvents()
                self.assertEqual(self.window.hub_row.text.text(), self.window.t.hub_label("running"))
                self.assertEqual(self.window.mode_label.text(), self.window.t("desktop.mode.activityUi"))
                self.assertTrue(self.window.mode_label.isVisibleTo(self.window))
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertFalse(self.window.mode_label.isVisibleTo(self.window))


if __name__ == "__main__":
    unittest.main()
