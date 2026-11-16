"""Desktop window tests: Screenshot mode (--screenshot)."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, UTC
from pathlib import Path
from unittest.mock import MagicMock, patch



from mcp_hub.desktop import i18n, status
from mcp_hub.desktop.status import HubSnapshot
from tests.helpers.window import app, screenshot, QT_MISSING, qt_app


def setUpModule() -> None:
    qt_app()


@unittest.skipIf(QT_MISSING, QT_MISSING or "")
class ScreenshotTest(unittest.TestCase):
    def test_capture_all_saves_every_combination(self) -> None:
        class FakeWindow:
            def __init__(self) -> None:
                self.t, self.shown, self.captured, self.previewed = i18n.Translator("en"), [], False, []
                self.releases: list = []

            def preview_update(self, release) -> None:
                self.releases.append(release)

            def show_for_capture(self) -> None:
                self.captured = True

            def set_theme(self, name: str) -> None:
                self.theme_name = name

            def set_language(self, language: str) -> None:
                self.t.set_language(language)

            def show_settings(self, visible: bool) -> None:
                self.settings_open = visible

            def set_compact(self, on: bool) -> None:
                self.compact = on

            def show_tab(self, name: str) -> None:
                self.settings_open, self.tab = True, name

            def process_results(self) -> None:
                self.drained = True

            def now(self) -> datetime:
                return datetime(2026, 10, 31, tzinfo=UTC)

            def preview_usage(self, board) -> None:
                self.previewed.append(board.usage("mcp-server-logs", self.now()))

            def apply(self, snapshot: HubSnapshot) -> None:
                self.shown.append((self.theme_name, self.t.language, snapshot.hub))

        window = FakeWindow()
        with tempfile.TemporaryDirectory() as tmp:
            saved = screenshot.capture_all(window, Path(tmp) / "shots", ["running", "settings-hub"], ["en", "vi"], ["dark"],
                                           capture=lambda _window, path: path.write_bytes(b"png"), settle=lambda _window: None)
            self.assertEqual([path.name for path in saved], [
                "running-en-dark.png", "settings-hub-en-dark.png", "running-vi-dark.png", "settings-hub-vi-dark.png",
            ])
            self.assertEqual(saved[0].read_bytes(), b"png")
        self.assertTrue(window.captured)
        self.assertEqual(window.shown[-1], ("dark", "vi", "running"))
        self.assertTrue(window.settings_open)
        self.assertEqual(window.tab, "hub")
        self.assertTrue(window.drained)
        self.assertEqual(window.previewed, [None] * 4)  # only the `ui` and `compact` samples show usage
        self.assertEqual(window.releases, [None] * 4)  # and only the update samples show the update card
        self.assertFalse(window.compact)

    def test_the_update_sample_shows_the_card(self) -> None:
        window = type("W", (), {})()
        window.t, window.shown, window.releases = i18n.Translator("en"), [], []
        for name, _value in (("show_for_capture", None), ("set_theme", None), ("set_language", None), ("show_settings", None),
                            ("set_compact", None), ("preview_usage", None), ("process_results", None)):
            setattr(window, name, lambda *_a, **_k: None)
        window.now = lambda: datetime(2026, 10, 31, tzinfo=UTC)
        window.apply = lambda snapshot: window.shown.append(snapshot)
        window.preview_update = window.releases.append
        with tempfile.TemporaryDirectory() as tmp:
            screenshot.capture_all(window, Path(tmp), ["update"], ["en"], ["light"],
                                   capture=lambda _w, path: path.write_bytes(b"png"), settle=lambda _w: None)
        self.assertEqual(window.releases, [screenshot.SAMPLE_RELEASE])

    def test_samples_cover_every_hub_state(self) -> None:
        self.assertEqual({sample.hub for sample in screenshot.SAMPLES.values()}, set(status.HUB_TONES))
        self.assertEqual(screenshot.STATES[-5:], ("settings", "settings-hub", "settings-mcps", "settings-config", "compact"))
        sample = screenshot.SampleSettings()
        self.assertIsNone(sample.set_activity_ui(True))
        self.assertIsNone(sample.set_server_enabled("mcp-server-logs", False))
        self.assertIs(sample.read_settings(), screenshot.SAMPLE_HUB_SETTINGS)

    def test_window_image_renders_or_reads_the_screen(self) -> None:
        window = app.StatusWindow(i18n.Translator("en"), style=lambda *_args, **_kwargs: False)
        try:
            self.assertFalse(screenshot.window_image(window, screen_capture=False).isNull())
            with patch.object(screenshot.win32, "window_rect", return_value=(0, 0, 2, 1)), \
                    patch.object(screenshot.win32, "capture_screen", return_value=(2, 1, b"\x10\x20\x30\x00" * 2)):
                image = screenshot.window_image(window, screen_capture=True)
            self.assertEqual((image.width(), image.height()), (2, 1))
            self.assertEqual(image.pixelColor(0, 0).name(), "#302010")
            with tempfile.TemporaryDirectory() as tmp:
                with patch.object(screenshot, "window_image", return_value=window.grab().toImage()):
                    screenshot.save_window_png(window, Path(tmp) / "shot.png")
                self.assertTrue((Path(tmp) / "shot.png").read_bytes().startswith(b"\x89PNG"))
                with patch.object(screenshot, "window_image", return_value=MagicMock(save=lambda *_args: False)), \
                        self.assertRaises(OSError):
                    screenshot.save_window_png(window, Path(tmp) / "nope.png")
        finally:
            window.close()
