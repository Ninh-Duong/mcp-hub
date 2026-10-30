from __future__ import annotations

import json
import re
import struct
import tempfile
import threading
import time
import tkinter as tk
import unittest
import zlib
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from mcp_hub import control
from mcp_hub.desktop import app, i18n, prefs, screenshot, status, theme, widgets, win32
from mcp_hub.desktop.status import ChildRow, HubSnapshot

DESKTOP = Path(__file__).resolve().parents[1] / "src" / "mcp_hub" / "desktop"
STYLE_CSS = DESKTOP.parent / "dashboard" / "style.css"
TOKEN = "t" * 48
STATUS = {
    "ok": True,
    "children": {"mcp-server-logs": "connected", "mcp-jira-sync": "reconnecting", "mcp-read-source": "disabled",
                 "mcp-pr-companion": "credentials required"},
    "extensions": {"activityUi": True},
    "clients": [
        {"client": "claude-code", "live": True, "connections": 2},
        {"client": "antigravity", "live": True, "connections": 1},
        {"client": "codex", "live": False, "connections": 0},
    ],
}


class FakeResponse:
    def __init__(self, value: object) -> None:
        self.body = json.dumps(value).encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class FakeOpener:
    """Answers by URL path; a value that is an exception is raised instead."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.requests: list[tuple[str, str, str | None, float]] = []

    def __call__(self, request, timeout: float):
        path = request.full_url.split("8765", 1)[1]
        self.requests.append((request.get_method(), path, request.get_header("Authorization"), timeout))
        answer = self.answers[path]
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)


def write_service_config(root: Path) -> None:
    value = {"version": 1, "host": "127.0.0.1", "port": 8765, "token": TOKEN}
    (root / "service.json").write_text(json.dumps(value), encoding="utf-8")


class StatusSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_service_config(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fetch(self, answers: dict[str, object]) -> tuple[HubSnapshot, FakeOpener]:
        opener = FakeOpener(answers)
        return status.fetch_snapshot(opener=opener, private_root=self.root), opener

    def test_running_hub_reports_children_tones_and_live_clients(self) -> None:
        snapshot, opener = self.fetch({"/health": {"ok": True}, "/dashboard/api/status": STATUS})
        self.assertEqual(snapshot.hub, "running")
        self.assertEqual(snapshot.tone, "ok")
        self.assertTrue(snapshot.activity_ui)
        self.assertEqual(
            [(row.server_id, row.tone) for row in snapshot.children],
            [("mcp-server-logs", "ok"), ("mcp-jira-sync", "wait"), ("mcp-read-source", "off"), ("mcp-pr-companion", "error")],
        )
        self.assertEqual(snapshot.clients, ("claude-code ×2", "antigravity"))
        self.assertEqual(opener.requests[0], ("GET", "/health", None, status.REQUEST_TIMEOUT_SECONDS))
        self.assertEqual(opener.requests[1][:3], ("GET", "/dashboard/api/status", f"Bearer {TOKEN}"))

    def test_fast_mode_has_no_clients(self) -> None:
        fast = {"ok": True, "children": {}, "extensions": {"activityUi": False}}
        snapshot, _ = self.fetch({"/health": {"ok": True}, "/dashboard/api/status": fast})
        self.assertFalse(snapshot.activity_ui)
        self.assertEqual((snapshot.children, snapshot.clients), ((), ()))

    def test_unreachable_hub_is_stopped(self) -> None:
        for failure in (URLError("refused"), OSError("reset"), {"ok": False}):
            with self.subTest(failure=type(failure).__name__):
                snapshot, opener = self.fetch({"/health": failure})
                self.assertEqual(snapshot.hub, "stopped")
                self.assertEqual(len(opener.requests), 1)

    def test_stale_token_is_unauthorized_and_other_http_errors_are_stopped(self) -> None:
        for code, expected in ((401, "unauthorized"), (500, "stopped")):
            with self.subTest(code=code):
                error = HTTPError("http://127.0.0.1:8765/dashboard/api/status", code, "error", None, None)
                snapshot, _ = self.fetch({"/health": {"ok": True}, "/dashboard/api/status": error})
                self.assertEqual(snapshot.hub, expected)

    def test_missing_service_config_is_not_installed(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            snapshot = status.fetch_snapshot(opener=FakeOpener({}), private_root=Path(empty))
        self.assertEqual(snapshot.hub, "not_installed")
        self.assertEqual(snapshot.tone, "off")

    def test_malformed_status_fields_are_ignored(self) -> None:
        snapshot = status.snapshot_from_status({"children": [], "extensions": None, "clients": {"x": 1}})
        self.assertEqual((snapshot.children, snapshot.clients, snapshot.activity_ui), ((), (), False))
        self.assertEqual(status.client_labels([{"live": True}, "junk", {"client": "a", "live": True, "connections": -1}]),
                         ("unknown", "a"))

    def test_child_tone_uses_only_the_four_documented_tones(self) -> None:
        states = ["connected", "connecting", "reconnecting", "disabled", "credentials required", "unavailable", "", "x"]
        self.assertEqual([status.child_tone(state) for state in states],
                         ["ok", "wait", "wait", "off", "error", "error", "error", "error"])
        self.assertEqual(set(status.HUB_TONES.values()) | {status.child_tone(s) for s in states}, set(status.TONES))
        self.assertEqual(set(theme.TONE_TOKENS), set(status.TONES))
        self.assertEqual(set(theme.TONE_GLYPHS), set(status.TONES))


class OpenDashboardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_service_config(self.root)
        self.opened: list[str] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def open(self, answer: object) -> FakeOpener:
        opener = FakeOpener({"/dashboard/pair": answer})
        status.open_dashboard(opener=opener, browser=self.opened.append, private_root=self.root)
        return opener

    def test_pairs_with_the_bearer_and_opens_an_escaped_link(self) -> None:
        opener = self.open({"pairing_token": "a+b/c"})
        self.assertEqual(opener.requests[0][:3], ("POST", "/dashboard/pair", f"Bearer {TOKEN}"))
        self.assertEqual(self.opened, ["http://127.0.0.1:8765/dashboard/?pair=a%2Bb%2Fc"])

    def test_failed_pairing_raises_without_opening_the_browser(self) -> None:
        for answer in (URLError("down"), {"pairing_token": ""}, ["not", "a", "dict"]):
            with self.subTest(answer=answer), self.assertRaises(status.DesktopError) as caught:
                self.open(answer)
            self.assertNotIn(TOKEN, str(caught.exception))
        self.assertEqual(self.opened, [])

    def test_missing_service_config_raises(self) -> None:
        with tempfile.TemporaryDirectory() as empty, self.assertRaises(status.DesktopError):
            status.open_dashboard(opener=FakeOpener({}), browser=self.opened.append, private_root=Path(empty))


class ThemeAndI18nTest(unittest.TestCase):
    def test_light_palette_matches_the_dashboard_root_tokens(self) -> None:
        css = STYLE_CSS.read_text(encoding="utf-8")
        root_block = css[css.index(":root"):css.index("}", css.index(":root"))]
        tokens = dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{3,8})\s*;", root_block))
        self.assertEqual(theme.LIGHT, {name: tokens[name] for name in theme.LIGHT})

    def test_palettes_share_tokens_and_never_use_the_glass_key(self) -> None:
        self.assertEqual(set(theme.LIGHT), set(theme.DARK))
        for palette in theme.PALETTES.values():
            self.assertNotIn(theme.GLASS_KEY, palette.values())
            self.assertEqual({tone: theme.tone_color(palette, tone) for tone in status.TONES},
                             {tone: palette[token] for tone, token in theme.TONE_TOKENS.items()})
        self.assertEqual(set(theme.TONE_TOKENS), set(status.TONES))
        self.assertEqual(set(theme.TONE_GLYPHS), set(status.TONES))
        self.assertEqual(theme.primary_button(theme.DARK)["bg"], theme.DARK["blue"])
        self.assertEqual(theme.secondary_button(theme.LIGHT)["fg"], theme.LIGHT["ink"])

    def test_system_language_follows_the_windows_display_language(self) -> None:
        self.assertEqual(i18n.system_language(lambda: 0x042A), "vi")
        self.assertEqual(i18n.system_language(lambda: 0x0409), "en")
        self.assertIn(i18n.system_language(), i18n.LANGUAGES)

    def test_translator_fills_placeholders_and_falls_back(self) -> None:
        t = i18n.Translator("fr")
        self.assertEqual(t.language, "en")
        self.assertEqual(t("desktop.updated", time="10:42:05"), "Updated 10:42:05")
        self.assertEqual(t("no.such.key"), "no.such.key")
        self.assertEqual(t.state_label("connected"), "Connected")
        self.assertEqual(t.state_label("missing runtime"), "missing runtime")
        self.assertEqual(t.state_label(""), "Unknown status")
        self.assertEqual(t.other_language(), "vi")
        t.set_language("vi")
        self.assertEqual(t("desktop.hub.running"), i18n.load_strings("vi")["desktop.hub.running"])
        self.assertEqual(t.other_language(), "en")

    def test_every_key_the_app_uses_exists_in_both_locales(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in DESKTOP.glob("*.py"))
        keys = {key for key in re.findall(r"""["']((?:desktop|connection|clients)\.[\w.]+)["']""", source)
                if not key.endswith(".json")}
        self.assertIn("desktop.button.startHub", keys)
        for language in i18n.LANGUAGES:
            with self.subTest(language=language):
                self.assertEqual(keys - set(i18n.load_strings(language)), set())


class PrefsTest(unittest.TestCase):
    def test_round_trip_keeps_only_known_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "desktop.json"
            self.assertTrue(prefs.save_prefs(path, {"theme": "dark", "language": "vi", "token": "x"}))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"theme": "dark", "language": "vi"})
            self.assertEqual(prefs.load_prefs(path), {"theme": "dark", "language": "vi"})
            path.write_text('{"theme": "neon", "language": "vi"}', encoding="utf-8")
            self.assertEqual(prefs.load_prefs(path), {"language": "vi"})

    def test_missing_broken_or_unwritable_files_are_harmless(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(prefs.load_prefs(root / "missing.json"), {})
            (root / "broken.json").write_text("{", encoding="utf-8")
            self.assertEqual(prefs.load_prefs(root / "broken.json"), {})
            (root / "list.json").write_text("[]", encoding="utf-8")
            self.assertEqual(prefs.load_prefs(root / "list.json"), {})
            (root / "taken").mkdir()
            self.assertFalse(prefs.save_prefs(root / "taken", {"theme": "dark"}))  # a directory cannot be replaced
        self.assertEqual(prefs.load_prefs(None), {})
        self.assertFalse(prefs.save_prefs(None, {"theme": "dark"}))


class Win32Test(unittest.TestCase):
    def test_second_copy_finds_the_mutex_taken(self) -> None:
        class Kernel32:
            def __init__(self, handle: int) -> None:
                self.handle = handle

            def CreateMutexW(self, _attributes, _owner, name):  # noqa: N802 - Win32 name
                self.name = name
                return self.handle

        first = Kernel32(7)
        self.assertEqual(win32.acquire_single_instance(first, lambda: 0), 7)
        self.assertEqual(first.name, win32.MUTEX_NAME)
        self.assertIsNone(win32.acquire_single_instance(Kernel32(7), lambda: win32.ERROR_ALREADY_EXISTS))
        self.assertTrue(win32.acquire_single_instance(Kernel32(0), lambda: 0))

    def test_style_window_sets_theme_corners_and_backdrop(self) -> None:
        calls: list[tuple[int, int, int]] = []

        def set_attribute(hwnd: int, attribute: int, value: int) -> bool:
            calls.append((hwnd, attribute, value))
            return attribute != win32.DWMWA_SYSTEMBACKDROP_TYPE

        self.assertFalse(win32.style_window(5, True, set_attribute))
        self.assertEqual(calls, [
            (5, win32.DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
            (5, win32.DWMWA_WINDOW_CORNER_PREFERENCE, win32.DWMWCP_ROUND),
            (5, win32.DWMWA_SYSTEMBACKDROP_TYPE, win32.DWMSBT_TRANSIENTWINDOW),
        ])
        self.assertTrue(win32.style_window(5, False, lambda *_args: True))

    def test_set_window_attribute_reports_the_hresult(self) -> None:
        class Dwm:
            def __init__(self, result: int) -> None:
                self.result = result

            def DwmSetWindowAttribute(self, *_args):  # noqa: N802 - Win32 name
                return self.result

        self.assertTrue(win32.set_window_attribute(1, 20, 1, Dwm(0)))
        self.assertFalse(win32.set_window_attribute(1, 20, 1, Dwm(-2147024809)))
        with patch.object(win32, "_dwm", side_effect=OSError("no dwmapi")):
            self.assertFalse(win32.set_window_attribute(1, 20, 1))

    def test_system_theme_reads_the_apps_theme(self) -> None:
        def unreadable() -> int:
            raise OSError("no key")

        self.assertEqual(win32.system_theme(lambda: 1), "light")
        self.assertEqual(win32.system_theme(lambda: 0), "dark")
        self.assertEqual(win32.system_theme(unreadable), "light")
        self.assertIn(win32.system_theme(), theme.THEMES)

    @unittest.skipUnless(hasattr(win32.ctypes, "WinDLL"), "Windows only")
    def test_capture_screen_returns_bgra_pixels(self) -> None:
        width, height, pixels = win32.capture_screen((0, 0, 3, 2))
        self.assertEqual((width, height, len(pixels)), (3, 2, 24))


class ScreenshotTest(unittest.TestCase):
    def test_encode_png_writes_rgba_rows(self) -> None:
        data = screenshot.encode_png(2, 1, bytes([1, 2, 3, 0, 4, 5, 6, 0]))
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(data[12:16], b"IHDR")
        self.assertEqual(struct.unpack(">II", data[16:24]), (2, 1))
        idat = data.index(b"IDAT")
        length = struct.unpack(">I", data[idat - 4:idat])[0]
        self.assertEqual(zlib.decompress(data[idat + 4:idat + 4 + length]), bytes([0, 3, 2, 1, 255, 6, 5, 4, 255]))
        self.assertTrue(data.endswith(b"IEND\xaeB`\x82"))

    def test_capture_all_saves_every_combination(self) -> None:
        class FakeRoot:
            def attributes(self, *args) -> None:
                self.attrs = args

            def geometry(self, value: str) -> None:
                self.position = value

        class FakeWindow:
            def __init__(self) -> None:
                self.root, self.t, self.shown = FakeRoot(), i18n.Translator("en"), []

            def set_theme(self, name: str) -> None:
                self.theme_name = name

            def apply(self, snapshot: HubSnapshot) -> None:
                self.shown.append((self.theme_name, self.t.language, snapshot.hub))

        window = FakeWindow()
        with tempfile.TemporaryDirectory() as tmp:
            saved = screenshot.capture_all(window, Path(tmp) / "shots", ["running", "stopped"], ["en", "vi"], ["dark"],
                                           capture=lambda _root: b"png", settle=lambda _root: None)
            self.assertEqual([path.name for path in saved], [
                "running-en-dark.png", "stopped-en-dark.png", "running-vi-dark.png", "stopped-vi-dark.png",
            ])
            self.assertEqual(saved[0].read_bytes(), b"png")
        self.assertEqual(window.shown[-1], ("dark", "vi", "stopped"))
        self.assertEqual(window.root.attrs, ("-topmost", True))

    def test_samples_cover_every_hub_state(self) -> None:
        self.assertEqual({sample.hub for sample in screenshot.SAMPLES.values()}, set(status.HUB_TONES))

    def test_parse_args_validates_lists(self) -> None:
        args = app.parse_args(["--screenshot", "out", "--states", "running, ui", "--theme", "dark"])
        self.assertEqual((args.screenshot, args.states, args.lang, args.theme), ("out", ["running", "ui"], ["en", "vi"], ["dark"]))
        self.assertIsNone(app.parse_args([]).screenshot)
        for bad in (["--states", "nope"], ["--lang", ""], ["--theme", "light,neon"]):
            with self.subTest(bad=bad), patch("sys.stderr"), self.assertRaises(SystemExit):
                app.parse_args(bad)


class ControlAndMainTest(unittest.TestCase):
    def test_main_exits_quietly_when_another_copy_runs(self) -> None:
        with patch.object(win32, "acquire_single_instance", return_value=None), patch.object(app.tk, "Tk") as tk_root:
            self.assertEqual(app.main([]), 0)
        tk_root.assert_not_called()

    def test_main_without_a_display_fails(self) -> None:
        with patch.object(win32, "acquire_single_instance", return_value=True), \
                patch.object(app.tk, "Tk", side_effect=tk.TclError("no display")):
            self.assertEqual(app.main([]), 1)

    def test_screenshot_mode_reports_a_capture_failure(self) -> None:
        root = SimpleNamespace(destroyed=False)
        root.destroy = lambda: setattr(root, "destroyed", True)
        with patch.object(app, "StatusWindow"), patch.object(screenshot, "capture_all", side_effect=OSError("no screen")), \
                patch("builtins.print") as printed:
            self.assertEqual(app._run_screenshots(root, app.parse_args(["--screenshot", "out"])), 1)
        self.assertTrue(root.destroyed)
        self.assertIn("need Windows", printed.call_args.args[0])

    def test_launch_hubctl_runs_hidden_windows_powershell(self) -> None:
        with patch.dict(control.os.environ, {"SystemRoot": "/sysroot"}), patch.object(control.subprocess, "Popen") as popen:
            control.launch_hubctl(["start"])
        command = popen.call_args.args[0]
        self.assertEqual(Path(command[0]).parts[-4:], ("System32", "WindowsPowerShell", "v1.0", "powershell.exe"))
        self.assertEqual(command[-3:], ["-File", str(control.HUBCTL_SCRIPT), "start"])
        self.assertIn("-NonInteractive", command)
        self.assertEqual(popen.call_args.kwargs["creationflags"], getattr(control.subprocess, "CREATE_NO_WINDOW", 0))

    def test_launch_hubctl_off_windows_raises_os_error(self) -> None:
        with patch.dict(control.os.environ, {}, clear=True), self.assertRaises(OSError):
            control.launch_hubctl(["start"])


def running(*children: ChildRow, activity_ui: bool = False, clients: tuple[str, ...] = ()) -> HubSnapshot:
    return HubSnapshot(hub="running", activity_ui=activity_ui, children=children, clients=clients,
                       checked_at=datetime(2026, 10, 30, 10, 42, 5))


class StatusWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display for Tk ({exc})")
        self.root.withdraw()
        self.launched: list[list[str]] = []
        self.now = 1000.0
        self.fetched = threading.Event()
        self.next_snapshot = HubSnapshot(hub="stopped")
        self.tmp = tempfile.TemporaryDirectory()
        self.prefs_path = Path(self.tmp.name) / "desktop.json"
        self.window = app.StatusWindow(
            self.root, i18n.Translator("en"), fetch=self.fake_fetch, open_dashboard=self.fake_open,
            launch=self.launched.append, clock=lambda: self.now, style=lambda _hwnd, _dark: False,
            prefs_path=self.prefs_path,
        )

    def tearDown(self) -> None:
        self.root.destroy()
        self.tmp.cleanup()

    def fake_fetch(self) -> HubSnapshot:
        self.fetched.set()
        return self.next_snapshot

    def fake_open(self) -> None:
        raise status.DesktopError("pairing failed")

    def texts(self, frame: tk.Frame) -> list[str]:
        found: list[str] = []
        for widget in frame.winfo_children():
            labels = widget.winfo_children() if isinstance(widget, tk.Frame) else [widget]
            found.append(" ".join(label.cget("text") for label in labels if label.cget("text")))
        return found

    def test_initial_window_is_checking_with_no_enabled_action(self) -> None:
        self.assertEqual(self.root.title(), "mcp-hub")
        self.assertEqual(self.window.hub_row.text.cget("text"), "Checking the Hub…")
        self.assertEqual(self.window.open_button.cget("state"), "disabled")
        self.assertEqual(self.window.start_button.cget("state"), "disabled")
        self.assertFalse(self.window.glass)
        self.assertEqual(self.window.behind, theme.LIGHT["canvas"])

    def test_running_snapshot_shows_children_and_clients(self) -> None:
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "ok"), ChildRow("mcp-jira-sync", "reconnecting", "wait"),
                                  activity_ui=True, clients=("claude-code ×2",)))
        self.assertEqual(self.window.hub_row.text.cget("text"), "Running")
        self.assertEqual(self.window.hub_row.detail.cget("text"), "activity UI")
        self.assertEqual(self.window.hub_row.glyph.cget("fg"), theme.LIGHT["green"])
        self.assertEqual(self.texts(self.window.children_list),
                         ["● mcp-server-logs Connected", "◐ mcp-jira-sync Reconnecting"])
        self.assertEqual(self.window.children_count.cget("text"), "1/2")
        self.assertEqual(self.texts(self.window.clients_list), ["● claude-code ×2"])
        self.assertEqual(self.window.clients_count.cget("text"), "1")
        self.assertEqual(self.window.open_button.cget("bg"), theme.LIGHT["blue"])
        self.assertEqual(self.window.open_button.cget("state"), "normal")
        self.assertEqual(self.window.start_button.cget("state"), "disabled")
        self.assertEqual(self.window.footer.cget("text"), "Updated 10:42:05")
        heights = [int(card.canvas.cget("height")) for card in self.window.cards]
        self.assertTrue(all(height > 20 for height in heights), heights)

    def test_fast_mode_and_empty_lists_show_notes(self) -> None:
        self.window.apply(running())
        self.assertEqual(self.texts(self.window.children_list), ["No child MCPs detected."])
        self.assertEqual(self.window.hub_row.detail.cget("text"), "fast mode")
        self.assertIn("hubctl start -Ui", self.texts(self.window.clients_list)[0])
        self.window.apply(running(activity_ui=True))
        self.assertEqual(self.texts(self.window.clients_list), ["No AI clients connected yet"])

    def test_an_unchanged_poll_redraws_nothing(self) -> None:
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "ok")))
        rows = self.window.children_list.winfo_children()
        later = replace(running(ChildRow("mcp-server-logs", "connected", "ok")), checked_at=datetime(2026, 10, 30, 10, 42, 10))
        with patch.object(self.root, "geometry") as geometry, patch.object(self.root, "iconphoto") as icon:
            self.window.apply(later)
        geometry.assert_not_called()
        icon.assert_not_called()
        self.assertEqual(self.window.children_list.winfo_children(), rows)
        self.assertEqual(self.window.footer.cget("text"), "Updated 10:42:10")

    def test_stopped_hub_makes_start_the_primary_action(self) -> None:
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertEqual(self.window.hub_row.glyph.cget("fg"), theme.LIGHT["red"])
        self.assertEqual(self.window.start_button.cget("bg"), theme.LIGHT["blue"])
        self.assertEqual(self.window.start_button.cget("state"), "normal")
        self.assertEqual(self.texts(self.window.children_list), ["Shown when the Hub is running."])

    def test_start_hub_shows_starting_until_the_hub_answers(self) -> None:
        self.window.apply(HubSnapshot(hub="stopped"))
        self.window.start_hub()
        self.assertEqual(self.launched, [["start"]])
        self.assertEqual(self.window.snapshot.hub, "starting")
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertEqual(self.window.snapshot.hub, "starting")
        self.assertEqual(self.window.start_button.cget("state"), "disabled")
        self.now += theme.START_GRACE_SECONDS + 1
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertEqual(self.window.snapshot.hub, "stopped")

    def test_start_hub_off_windows_shows_an_error(self) -> None:
        def unavailable(_arguments: list[str]) -> None:
            raise OSError("no SystemRoot")

        self.window.launch = unavailable
        self.window.start_hub()
        self.assertIn("needs Windows", self.window.footer.cget("text"))
        self.assertEqual(self.window.footer.cget("fg"), theme.LIGHT["red"])

    def test_refresh_polls_on_a_worker_thread(self) -> None:
        self.next_snapshot = running(ChildRow("mcp-server-logs", "connected", "ok"))
        self.window.refresh()
        self.window.refresh()  # ignored while a poll is in flight
        self.assertTrue(self.fetched.wait(timeout=5))
        deadline = time.monotonic() + 5
        while self.window.snapshot is None and time.monotonic() < deadline:
            self.window.process_results()
            time.sleep(0.01)
        self.assertEqual(self.window.snapshot.hub, "running")
        self.assertFalse(self.window._polling)

    def test_failed_dashboard_open_is_reported_in_the_footer(self) -> None:
        self.window.open_dashboard_once()
        self.window.process_results()
        self.assertIn("Could not open the dashboard", self.window.footer.cget("text"))

    def test_language_toggle_rerenders_and_is_remembered(self) -> None:
        self.window.apply(running())
        self.window.toggle_language()
        vi = i18n.load_strings("vi")
        self.assertEqual(self.window.open_button.cget("text"), vi["desktop.button.openDashboard"])
        self.assertEqual(self.window.language_button.cget("text"), "EN")
        self.assertEqual(prefs.load_prefs(self.prefs_path), {"theme": "light", "language": "vi"})
        self.window.toggle_language()
        self.assertEqual(self.window.language_button.cget("text"), "VI")

    def test_theme_toggle_rebuilds_in_the_dark_palette_and_is_remembered(self) -> None:
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "ok")))
        self.window.toggle_theme()
        self.assertEqual(self.window.theme_name, "dark")
        self.assertEqual(self.window.hub_row.text.cget("bg"), theme.DARK["surface"])
        self.assertEqual(self.window.hub_row.glyph.cget("fg"), theme.DARK["green"])
        self.assertEqual(self.window.theme_button.cget("text"), theme.THEME_TOGGLE_GLYPHS["dark"])
        self.assertEqual(self.texts(self.window.children_list), ["● mcp-server-logs Connected"])
        self.assertEqual(len(self.window.cards), 4)
        self.assertEqual(prefs.load_prefs(self.prefs_path), {"theme": "dark", "language": "en"})
        self.window.set_theme("neon")
        self.assertEqual(self.window.theme_name, "dark")

    def test_glass_backdrop_makes_the_gaps_see_through(self) -> None:
        styled: list[bool] = []
        window = app.StatusWindow(tk.Toplevel(self.root), i18n.Translator("en"), theme_name="dark",
                                  fetch=self.fake_fetch, style=lambda _hwnd, dark: styled.append(dark) or True)
        self.assertTrue(window.glass)
        self.assertEqual(window.behind, theme.GLASS_KEY)
        self.assertEqual(styled, [True])
        self.assertEqual(window.root.attributes("-transparentcolor"), theme.GLASS_KEY)

    def test_window_icon_is_a_dot_in_the_hub_tone(self) -> None:
        self.window.apply(running())
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertEqual(set(self.window._icons), {"wait", "ok", "error"})
        icon = widgets.dot_icon(self.root, theme.LIGHT["red"], 8)
        self.assertEqual((icon.width(), icon.height()), (8, 8))
        self.assertEqual(icon.get(4, 4), (0xB4, 0x23, 0x18))
        self.assertTrue(icon.transparency_get(0, 0))

    def test_rounded_rect_points_clamp_the_radius(self) -> None:
        points = widgets.rounded_rect_points(0, 0, 10, 4, 12)
        self.assertEqual(len(points), 24)
        self.assertEqual(points[:2], [2.0, 0])

    def test_start_schedules_polling_and_draining(self) -> None:
        with patch.object(self.root, "after") as after, patch.object(self.window, "refresh") as refresh:
            self.window.start()
        refresh.assert_called_once()
        self.assertEqual([call.args[0] for call in after.call_args_list], [theme.POLL_INTERVAL_MS, theme.QUEUE_CHECK_MS])


if __name__ == "__main__":
    unittest.main()
