"""The desktop app as a pure client: its connection file, the Hub API calls, its own system work, and its boundary."""
from __future__ import annotations

import ast
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from mcp_hub.desktop import connection, hub_api, system, win32
from tests.test_desktop import TOKEN, FakeOpener

DESKTOP = Path(__file__).resolve().parents[1] / "src" / "mcp_hub" / "desktop"


def write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class ConnectionTest(unittest.TestCase):
    def test_bad_connection_files_are_missing_without_showing_the_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "broken.json").write_text("{", encoding="utf-8")
            cases = {
                "absent": root / "absent.json",
                "broken": root / "broken.json",
                "version": write(root / "v.json", {"version": 2, "url": "http://127.0.0.1:8765", "token": TOKEN}),
                "remote": write(root / "r.json", {"version": 1, "url": "http://example.com:8765", "token": TOKEN}),
                "no port": write(root / "p.json", {"version": 1, "url": "http://127.0.0.1:", "token": TOKEN}),
                "short token": write(root / "t.json", {"version": 1, "url": "http://127.0.0.1:8765", "token": "short"}),
                "list": write(root / "l.json", []),
            }
            for name, path in cases.items():
                with self.subTest(name=name), self.assertRaises(connection.ConnectionMissing) as caught:
                    connection.load_connection(path)
                self.assertNotIn(TOKEN, str(caught.exception))


class HubClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = write(Path(self.tmp.name) / "connection.json",
                          {"version": 1, "url": "http://127.0.0.1:8765", "token": TOKEN})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def client(self, answers: dict[str, object]) -> tuple[hub_api.HubClient, FakeOpener]:
        opener = FakeOpener(answers)
        return hub_api.HubClient(opener=opener, connection_file=self.path), opener

    def test_settings_are_read_and_changed_over_http_with_the_bearer(self) -> None:
        settings = {"editable": True, "activity_ui": True, "servers": [
            {"id": "sample-child", "enabled": True, "reason": "credentials required"},
            {"id": "other child", "enabled": False, "reason": 3},
        ]}
        client, opener = self.client({
            "/dashboard/api/settings": settings, "/dashboard/api/settings/tracking": settings,
            "/dashboard/api/settings/servers/other%20child": settings,
        })
        read = client.read_settings()
        self.assertEqual(read, hub_api.HubSettings(True, (
            hub_api.ServerSwitch("sample-child", True, "credentials required"),
            hub_api.ServerSwitch("other child", False, None)), True))
        client.set_activity_ui(False)
        client.set_server_enabled("other child", True)
        self.assertEqual([request[:3] for request in opener.requests], [
            ("GET", "/dashboard/api/settings", f"Bearer {TOKEN}"),
            ("POST", "/dashboard/api/settings/tracking", f"Bearer {TOKEN}"),
            ("POST", "/dashboard/api/settings/servers/other%20child", f"Bearer {TOKEN}"),
        ])

    def test_sign_in_task_and_control(self) -> None:
        client, opener = self.client({
            "/dashboard/api/settings/autostart": {"hub": False},
            "/dashboard/api/control/restart": {"accepted": True},
        })
        self.assertFalse(client.read_hub_autostart())
        self.assertFalse(client.set_hub_autostart(False))
        client.control("restart")
        self.assertEqual(opener.requests[-1][:2], ("POST", "/dashboard/api/control/restart"))
        with self.assertRaises(ValueError):
            client.control("start")  # starting is the Hub's task, not an HTTP call

    def test_failures_are_os_or_value_errors(self) -> None:
        error = HTTPError("http://127.0.0.1:8765/dashboard/api/settings", 409, "conflict", None, None)
        cases = (
            ({"/dashboard/api/settings": URLError("refused")}, OSError),
            ({"/dashboard/api/settings": error}, OSError),
            ({"/dashboard/api/settings": {"servers": "nope"}}, ValueError),
            ({"/dashboard/api/settings": {"servers": ["nope"]}}, ValueError),
        )
        for answers, expected in cases:
            with self.subTest(answers=answers), self.assertRaises(expected):
                self.client(answers)[0].read_settings()
        with self.assertRaises(ValueError):
            self.client({"/dashboard/api/settings/autostart": {"hub": "yes"}})[0].read_hub_autostart()
        with self.assertRaises(ValueError):
            hub_api.HubClient(opener=FakeOpener({}), connection_file=Path(self.tmp.name) / "none.json").read_settings()


class LoopbackOpenerTest(unittest.TestCase):
    def test_the_opener_speaks_plain_http_without_a_proxy(self) -> None:
        from mcp_hub.desktop import status

        opener = status.loopback_opener()
        kinds = {type(handler).__name__ for handler in opener.handlers}
        self.assertIn("HTTPHandler", kinds)
        self.assertNotIn("HTTPSHandler", kinds)  # no TLS setup: the Hub is on 127.0.0.1
        self.assertNotIn("ProxyHandler", kinds)  # a configured proxy never sees the token
        with self.assertRaises(URLError):
            opener.open("https://127.0.0.1:1/", timeout=1)


class FakeKey:
    def __enter__(self) -> FakeKey:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class FakeRegistry:
    """winreg stand-in for the user's Run key."""

    HKEY_CURRENT_USER, REG_SZ = "HKCU", 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def OpenKey(self, _root: str, _key: str) -> FakeKey:  # noqa: N802 - winreg API
        return FakeKey()

    CreateKey = OpenKey

    def QueryValueEx(self, _key: FakeKey, name: str) -> tuple[str, int]:  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, _key: FakeKey, name: str, _reserved: int, _kind: int, value: str) -> None:  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, _key: FakeKey, name: str) -> None:  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


class SystemTest(unittest.TestCase):
    def test_start_hub_runs_the_hub_task_without_a_window(self) -> None:
        run = MagicMock(return_value=SimpleNamespace(returncode=0))
        with patch.dict(system.os.environ, {"SystemRoot": "/sysroot"}):
            system.start_hub(run)
        command = run.call_args.args[0]
        self.assertEqual(Path(command[0]).parts[-4:], ("System32", "WindowsPowerShell", "v1.0", "powershell.exe"))
        self.assertEqual(command[1:4], ["-NoProfile", "-NonInteractive", "-Command"])
        script = command[4]
        # A stopping Hub's task still runs while it drains and would ignore the start: wait for it, then start.
        self.assertLess(script.index("-eq 'Running'"), script.index("Start-ScheduledTask -TaskName 'mcp-hub'"))
        self.assertIn(f"AddSeconds({system.DRAIN_WAIT_SECONDS})", script)
        self.assertGreater(system.START_TIMEOUT_SECONDS, system.DRAIN_WAIT_SECONDS)
        self.assertEqual(run.call_args.kwargs["timeout"], system.START_TIMEOUT_SECONDS)
        self.assertEqual(run.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_start_hub_failures_are_os_errors(self) -> None:
        failures = (MagicMock(return_value=SimpleNamespace(returncode=1)),
                    MagicMock(side_effect=subprocess.TimeoutExpired("schtasks", 1)))
        for run in failures:
            with self.subTest(run=run), patch.dict(system.os.environ, {"SystemRoot": "/sysroot"}), \
                    self.assertRaises(OSError):
                system.start_hub(run)
        with patch.dict(system.os.environ, {}, clear=True), self.assertRaises(OSError):
            system.start_hub(MagicMock())

    def test_the_installed_app_manages_its_own_run_value(self) -> None:
        registry = FakeRegistry()
        exe = "/apps/mcp-hub-status.exe"
        self.assertFalse(system.app_autostart(exe=exe, registry=registry))
        self.assertTrue(system.set_app_autostart(True, exe=exe, registry=registry))
        self.assertEqual(registry.values, {"mcp-hub-status": f'"{exe}"'})
        self.assertTrue(system.app_autostart(exe=exe, registry=registry))
        self.assertFalse(system.set_app_autostart(False, exe=exe, registry=registry))
        self.assertFalse(system.set_app_autostart(False, exe=exe, registry=registry))  # already gone
        self.assertEqual(registry.values, {})

    def test_from_source_there_is_no_sign_in_entry(self) -> None:
        with patch.object(system.sys, "frozen", False, create=True):
            self.assertIsNone(system.installed_exe())
            self.assertIsNone(system.app_autostart())
            with self.assertRaises(OSError):
                system.set_app_autostart(True)
        with patch.object(system.sys, "frozen", True, create=True):
            self.assertEqual(system.installed_exe(), system.sys.executable)

    def test_set_app_id_names_the_process_like_the_shortcut(self) -> None:
        shell32 = MagicMock()
        shell32.SetCurrentProcessExplicitAppUserModelID.return_value = 0
        self.assertTrue(win32.set_app_id(shell32))
        shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with(win32.APP_USER_MODEL_ID)
        with patch.object(win32, "ctypes", SimpleNamespace()):
            self.assertFalse(win32.set_app_id())


class BoundaryTest(unittest.TestCase):
    def test_the_app_imports_nothing_of_the_hub(self) -> None:
        """A viewer: the installed app bundles only mcp_hub.desktop and reaches the Hub over HTTP."""
        for path in sorted(DESKTOP.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    with self.subTest(module=path.name, line=node.lineno):
                        self.assertLessEqual(node.level, 1)
                        self.assertFalse((node.module or "").startswith("mcp_hub") and
                                         not (node.module or "").startswith("mcp_hub.desktop"))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(alias.name.startswith("mcp_hub") and not alias.name.startswith("mcp_hub.desktop"))


if __name__ == "__main__":
    unittest.main()
