"""Desktop window tests: Command line of the desktop app (cli.main, parse_args, run_screenshots)."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch



from mcp_hub.desktop import prefs, theme
from tests.helpers.window import QApplication, app, cli, screenshot, QT_MISSING, qt_app


def setUpModule() -> None:
    qt_app()


@unittest.skipIf(QT_MISSING, QT_MISSING or "")
class MainTest(unittest.TestCase):
    def test_parse_args_validates_lists(self) -> None:
        args = cli.parse_args(["--screenshot", "out", "--states", "running, ui", "--theme", "dark"])
        self.assertEqual((args.screenshot, args.states, args.lang, args.theme), ("out", ["running", "ui"], ["en", "vi"], ["dark"]))
        self.assertIsNone(cli.parse_args([]).screenshot)
        for bad in (["--states", "nope"], ["--lang", ""], ["--theme", "light,neon"]):
            with self.subTest(bad=bad), patch("sys.stderr"), self.assertRaises(SystemExit):
                cli.parse_args(bad)

    def test_version_prints_the_app_version_and_starts_nothing(self) -> None:
        with patch.object(app.win32, "acquire_single_instance") as guard, patch.object(cli, "application") as application, \
                patch.object(app.update, "app_version", return_value="0.1.1"), patch("builtins.print") as printed:
            self.assertEqual(cli.main(["--version"]), 0)
        printed.assert_called_once_with("0.1.1")
        guard.assert_not_called()
        application.assert_not_called()
        with patch.object(app.update, "app_version", return_value=None), patch("builtins.print") as printed:
            cli.main(["--version"])
        printed.assert_called_once_with("unknown")

    def test_main_exits_quietly_when_another_copy_runs(self) -> None:
        with patch.object(app.win32, "acquire_single_instance", return_value=None), \
                patch.object(cli, "application") as application:
            self.assertEqual(cli.main([]), 0)
        application.assert_not_called()

    def test_main_opens_the_window_with_saved_choices(self) -> None:
        fake_app = SimpleNamespace(exec=lambda: 0)
        with patch.object(app.win32, "acquire_single_instance", return_value=7), \
                patch.object(cli, "application", return_value=fake_app), \
                patch.object(prefs, "load_prefs", return_value={"theme": "dark", "language": "vi", "tint": 20}), \
                patch.object(cli, "StatusWindow") as window_class:
            self.assertEqual(cli.main([]), 0)
        self.assertEqual(window_class.call_args.kwargs["appearance"], theme.Appearance(theme="dark", tint=20))
        self.assertEqual(window_class.call_args.args[0].language, "vi")
        window_class.return_value.show.assert_called_once()
        window_class.return_value.start.assert_called_once()

    def test_main_gives_the_window_the_default_updater(self) -> None:
        sentinel = object()
        with patch.object(app.win32, "acquire_single_instance", return_value=7), \
                patch.object(cli, "application", return_value=SimpleNamespace(exec=lambda: 0)), \
                patch.object(prefs, "load_prefs", return_value={}), \
                patch.object(app.update, "default_updater", return_value=sentinel), patch.object(cli, "StatusWindow") as window_class:
            cli.main([])
        self.assertIs(window_class.call_args.kwargs["updater"], sentinel)

    def test_screenshot_mode_reports_success_and_failure(self) -> None:
        args = cli.parse_args(["--screenshot", "out"])
        with patch.object(cli, "StatusWindow") as window_class, \
                patch.object(screenshot, "capture_all", return_value=[Path("a.png")]), patch("builtins.print") as printed:
            self.assertEqual(cli.run_screenshots(args), 0)
        self.assertEqual(printed.call_args.args[0], "a.png")
        window_class.return_value.close.assert_called_once()
        with patch.object(cli, "StatusWindow"), patch.object(screenshot, "capture_all", side_effect=OSError("disk")), \
                patch("builtins.print") as printed:
            self.assertEqual(cli.run_screenshots(args), 1)
        self.assertIn("could not be saved", printed.call_args.args[0])
        with patch.object(cli, "application"), patch.object(cli, "run_screenshots", return_value=0) as run:
            self.assertEqual(cli.main(["--screenshot", "out"]), 0)
        run.assert_called_once()

    def test_application_sets_the_app_font(self) -> None:
        self.assertEqual(app.application().font().pointSize(), theme.FONT_POINTS["body"])
        self.assertIs(app.application(), QApplication.instance())
