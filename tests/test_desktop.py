from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from mcp_hub import control
from mcp_hub.desktop import i18n, prefs, status, theme, win32
from mcp_hub.desktop.status import HubSnapshot

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
        self.assertEqual(snapshot.client_names, ("claude-code", "antigravity"))
        self.assertEqual(snapshot.recent, ("codex",))
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
        self.assertEqual(status.recent_clients([{"client": "a", "live": True}, {"client": "a"}, {"client": "b"}, {"client": "b"}, 3]),
                         ("b",))

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

    def test_palettes_share_tokens_and_tones(self) -> None:
        self.assertEqual(set(theme.LIGHT), set(theme.DARK))
        self.assertEqual(set(theme.GLASS), set(theme.THEMES))
        self.assertEqual(set(theme.TONE_TOKENS), set(status.TONES))
        self.assertEqual(set(theme.TONE_GLYPHS), set(status.TONES))
        self.assertEqual(theme.tone_color(theme.DARK, "error"), theme.DARK["red"])

    def test_stylesheet_is_frosted_only_with_glass(self) -> None:
        for name, palette in theme.PALETTES.items():
            with self.subTest(theme=name):
                look = theme.Appearance(theme=name)
                frosted = theme.stylesheet(look, glass_active=True)
                opaque = theme.stylesheet(look, glass_active=False)
                card_alpha = theme.GLASS[name]["card_alpha"]
                self.assertIn("QWidget#window { background: transparent; }", frosted)
                self.assertIn(theme.rgba(palette["surface"], card_alpha), frosted)
                self.assertEqual(theme.stylesheet(theme.Appearance(theme=name, glass=False), glass_active=True), opaque)
                self.assertIn(f"QWidget#window {{ background: {palette['canvas']}; }}", opaque)
                self.assertIn(f"QFrame#card {{ background: {palette['surface']};", opaque)
                for tone, token in theme.TONE_TOKENS.items():
                    self.assertIn(f'QLabel[tone="{tone}"] {{ color: {palette[token]};', opaque)
                self.assertIn('QPushButton[primary="true"]', opaque)
                self.assertIn("QSlider::handle:horizontal", opaque)

    def test_user_overrides_change_the_card_and_tint(self) -> None:
        look = theme.Appearance(theme="dark", card_opacity=80, tint=10)
        self.assertEqual(theme.card_alpha(look), 0.8)
        self.assertEqual(theme.tint_alpha(look), 26)
        self.assertIn(theme.rgba(theme.DARK["surface"], 0.8), theme.stylesheet(look, glass_active=True))
        self.assertEqual(theme.backdrop_tint(look) >> 24, 26)
        for name in theme.THEMES:
            default = theme.Appearance(theme=name)
            self.assertEqual(theme.card_alpha(default), theme.default_card_opacity(name) / 100)
            self.assertEqual(theme.tint_alpha(default), round(theme.default_tint(name) * 255 / 100))
            low, high = theme.CARD_OPACITY_RANGE
            self.assertTrue(low <= theme.default_card_opacity(name) <= high)
            self.assertTrue(theme.TINT_RANGE[0] <= theme.default_tint(name) <= theme.TINT_RANGE[1])

    def test_color_helpers(self) -> None:
        self.assertEqual(theme.rgba("#2563eb", 0.5), "rgba(37, 99, 235, 0.5)")
        # Light tint: canvas #f4f6f9 at the default 33 % (alpha 0x54), packed as AABBGGRR.
        self.assertEqual(theme.backdrop_tint(theme.Appearance(theme="light")), 0x54F9F6F4)
        self.assertEqual(theme.backdrop_tint(theme.Appearance(theme="light", tint=0)), 0x00F9F6F4)

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
            choices = {"theme": "dark", "language": "vi", "glass": False, "card_opacity": 0, "tint": 0,
                       "glass_style": "clear", "compact": True, "compact_pos": [40, -8]}
            self.assertTrue(prefs.save_prefs(path, {**choices, "token": "x"}))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), choices)
            self.assertEqual(prefs.load_prefs(path), choices)
            path.write_text(json.dumps({
                "theme": "neon", "language": "vi", "glass": "yes", "card_opacity": -1, "tint": True,
                "glass_style": "smoky", "compact": 1, "compact_pos": [1, True],
            }), encoding="utf-8")
            self.assertEqual(prefs.load_prefs(path), {"language": "vi"})
            path.write_text(json.dumps({"card_opacity": 101, "tint": 91, "glass": True, "compact_pos": [1, 2, 3]}),
                            encoding="utf-8")
            self.assertEqual(prefs.load_prefs(path), {"glass": True})

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

    def test_style_window_sets_theme_corners_then_the_backdrop(self) -> None:
        calls: list[tuple] = []

        def set_attribute(hwnd: int, attribute: int, value: int) -> bool:
            calls.append(("attribute", hwnd, attribute, value))
            return True

        def set_backdrop(hwnd: int, tint: int, state: int) -> bool:
            calls.append(("backdrop", hwnd, tint, state))
            return True

        self.assertTrue(win32.style_window(5, True, 0x80112233, True, set_attribute, set_backdrop))
        self.assertEqual(calls, [
            ("attribute", 5, win32.DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
            ("attribute", 5, win32.DWMWA_WINDOW_CORNER_PREFERENCE, win32.DWMWCP_ROUND),
            ("backdrop", 5, 0x80112233, win32.ACCENT_ENABLE_ACRYLICBLURBEHIND),
        ])
        calls.clear()
        # The Clear style: the light blur without the acrylic layer.
        self.assertTrue(win32.style_window(5, False, 0, True, set_attribute, set_backdrop, clear=True))
        self.assertEqual(calls[-1], ("backdrop", 5, 0, win32.ACCENT_ENABLE_BLURBEHIND))
        calls.clear()
        # Glass off removes a blur applied earlier and reports no backdrop.
        self.assertFalse(win32.style_window(5, False, 0, False, set_attribute, set_backdrop))
        self.assertEqual(calls[-1], ("backdrop", 5, 0, win32.ACCENT_DISABLED))

    def test_set_acrylic_sends_the_accent_policy(self) -> None:
        seen: list[tuple[int, int, int]] = []

        def apply(_hwnd, data) -> int:
            composition = data._obj
            accent = win32.ctypes.cast(composition.data, win32.ctypes.POINTER(win32._AccentPolicy)).contents
            seen.append((composition.attribute, accent.state, accent.gradient_color))
            return 1

        user32 = SimpleNamespace(SetWindowCompositionAttribute=apply)
        self.assertTrue(win32.set_acrylic(9, 0x55F9F6F4, user32=user32))
        self.assertTrue(win32.set_acrylic(9, 0, win32.ACCENT_DISABLED, user32=user32))
        self.assertEqual(seen, [
            (win32.WCA_ACCENT_POLICY, win32.ACCENT_ENABLE_ACRYLICBLURBEHIND, 0x55F9F6F4),
            (win32.WCA_ACCENT_POLICY, win32.ACCENT_DISABLED, 0),
        ])
        self.assertFalse(win32.set_acrylic(9, 0, user32=SimpleNamespace(SetWindowCompositionAttribute=lambda *_args: 0)))
        self.assertFalse(win32.set_acrylic(9, 0, user32=SimpleNamespace()))

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


class ControlTest(unittest.TestCase):
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

    def test_run_hubctl_returns_output_and_maps_failures_to_os_error(self) -> None:
        done = SimpleNamespace(returncode=0, stdout='{"hub":true,"app":false}\n')
        with patch.dict(control.os.environ, {"SystemRoot": "/sysroot"}), \
                patch.object(control.subprocess, "run", return_value=done) as run:
            self.assertEqual(control.run_hubctl(["autostart"]), done.stdout)
        self.assertEqual(run.call_args.args[0][-2:], [str(control.HUBCTL_SCRIPT), "autostart"])
        self.assertEqual(run.call_args.kwargs["timeout"], control.HUBCTL_TIMEOUT_SECONDS)
        self.assertTrue(run.call_args.kwargs["capture_output"])
        failures = (SimpleNamespace(returncode=1, stdout=""), control.subprocess.TimeoutExpired("hubctl", 1))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), patch.dict(control.os.environ, {"SystemRoot": "/sysroot"}), \
                    patch.object(control.subprocess, "run", **({"side_effect": failure} if isinstance(failure, Exception)
                                                               else {"return_value": failure})), \
                    self.assertRaises(OSError):
                control.run_hubctl(["autostart"])

    def test_autostart_state_and_changes_go_through_hubctl(self) -> None:
        calls: list[list[str]] = []

        def run(arguments: list[str]) -> str:
            calls.append(arguments)
            return 'WARNING: noise\n{"hub": false, "app": true}\n'

        self.assertEqual(control.autostart_state(run), {"hub": False, "app": True})
        self.assertEqual(control.set_autostart("app", False, run), {"hub": False, "app": True})
        self.assertEqual(calls, [["autostart"], ["autostart", "-Target", "app", "-State", "off"]])
        with self.assertRaises(ValueError):
            control.set_autostart("everything", True, run)
        for output in ("", "not json", '{"hub": "yes", "app": true}', "[]"):
            with self.subTest(output=output), self.assertRaises(OSError):
                control.autostart_state(lambda _arguments, text=output: text)


if __name__ == "__main__":
    unittest.main()
