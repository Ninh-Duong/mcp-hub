from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


class ServiceConfigTest(unittest.TestCase):
    def test_creates_private_localhost_config_without_exposing_token_in_repr(self) -> None:
        from mcp_hub.service_config import load_service_config

        with tempfile.TemporaryDirectory() as directory:
            private_root = Path(directory)
            path = private_root / "service.json"
            config = load_service_config(path=path, private_root=private_root)

            self.assertEqual(config.host, "127.0.0.1")
            self.assertEqual(config.port, 8765)
            self.assertGreaterEqual(len(config.token), 32)
            self.assertNotIn(config.token, repr(config))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["token"], config.token)

    def test_rejects_config_path_outside_private_root(self) -> None:
        from mcp_hub.service_config import ServiceConfigError, load_service_config

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ServiceConfigError):
                load_service_config(path=root / "outside.json", private_root=root / "private")

    def test_refuses_public_bind_and_short_token_in_existing_config(self) -> None:
        from mcp_hub.service_config import ServiceConfigError, load_service_config

        with tempfile.TemporaryDirectory() as directory:
            private_root = Path(directory)
            path = private_root / "service.json"
            path.write_text(json.dumps({
                "version": 1,
                "host": "0.0.0.0",
                "port": 8765,
                "token": "short",
            }), encoding="utf-8")

            with self.assertRaises(ServiceConfigError):
                load_service_config(path=path, private_root=private_root)

    def test_the_service_moves_the_port_to_the_setting_and_keeps_the_token(self) -> None:
        from mcp_hub.service_config import ServiceConfigError, load_service_config

        token = "test-token-value-long-enough-for-validation-12345"
        with tempfile.TemporaryDirectory() as directory:
            private_root = Path(directory)
            path = private_root / "service.json"
            path.write_text(json.dumps({"version": 1, "host": "127.0.0.1", "port": 8765, "token": token}),
                            encoding="utf-8")
            # A reader such as the bridge takes the file's port and never rewrites it.
            self.assertEqual(load_service_config(path=path, private_root=private_root, create=False, port=9876).port, 8765)
            moved = load_service_config(path=path, private_root=private_root, port=9876)
            self.assertEqual((moved.port, moved.token), (9876, token))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["port"], 9876)
            for bad in (80, 70000, "8765", True):
                path.write_text(json.dumps({"version": 1, "host": "127.0.0.1", "port": bad, "token": token}),
                                encoding="utf-8")
                with self.subTest(port=bad), self.assertRaises(ServiceConfigError):
                    load_service_config(path=path, private_root=private_root, create=False)


class ConnectionFileTest(unittest.TestCase):
    """The file the Hub writes so the desktop app finds it without reading config/private/."""

    def test_writes_url_and_the_admin_token_once_and_the_app_reads_the_same(self) -> None:
        from unittest.mock import MagicMock

        from mcp_hub.desktop.connection import load_connection
        from mcp_hub.service_config import ServiceConfig, new_admin_token, write_connection_file

        config = ServiceConfig(host="127.0.0.1", port=8765, token="t" * 48)
        admin = new_admin_token()
        self.assertNotEqual(admin, new_admin_token())  # new at every start
        restrict = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "connection.json"
            self.assertTrue(write_connection_file(config, admin, path, restrict=restrict))
            restrict.assert_called_once_with(path)
            self.assertFalse(write_connection_file(config, admin, path, restrict=restrict))  # unchanged
            connection = load_connection(path)
            # The file carries the admin token, never the AI clients' token from service.json.
            self.assertEqual((connection.url, connection.token), ("http://127.0.0.1:8765", admin))
            self.assertNotIn(config.token, path.read_text(encoding="utf-8"))
            self.assertNotIn(admin, repr(connection))
            path.write_text("{broken", encoding="utf-8")
            self.assertTrue(write_connection_file(config, admin, path, restrict=restrict))

    def test_the_file_is_restricted_to_this_user_with_icacls(self) -> None:
        from unittest.mock import MagicMock, patch

        from mcp_hub import service_config

        run = MagicMock(return_value=SimpleNamespace(returncode=0))
        environ = {"USERNAME": "someone", "USERDOMAIN": "PC", "SystemRoot": "/sysroot"}
        with patch.object(service_config.os, "name", "nt"), patch.dict(service_config.os.environ, environ, clear=True):
            service_config.restrict_to_current_user(Path("/data/connection.json"), run)
            command = run.call_args.args[0]
            self.assertEqual(Path(command[0]).name, "icacls.exe")
            self.assertEqual(command[2:], ["/inheritance:r", "/grant:r", r"PC\someone:F", "*S-1-5-18:F"])
            failures = (MagicMock(return_value=SimpleNamespace(returncode=5)),
                        MagicMock(side_effect=service_config.subprocess.TimeoutExpired("icacls", 1)))
            for failing in failures:
                with self.subTest(run=failing), self.assertRaises(OSError):
                    service_config.restrict_to_current_user(Path("/data/connection.json"), failing)
        with patch.object(service_config.os, "name", "nt"), patch.dict(service_config.os.environ, {}, clear=True),                 self.assertRaises(OSError):
            service_config.restrict_to_current_user(Path("/data/connection.json"), run)
        with patch.object(service_config.os, "name", "posix"):
            service_config.restrict_to_current_user(Path("/data/connection.json"), MagicMock(side_effect=AssertionError))

    def test_path_follows_the_overrides_and_matches_the_app(self) -> None:
        from unittest.mock import patch

        from mcp_hub import service_config
        from mcp_hub.desktop import connection

        cases = (
            ({"MCP_HUB_CONNECTION_FILE": "/x/conn.json", "LOCALAPPDATA": "/data"}, Path("/x/conn.json")),
            ({"LOCALAPPDATA": "/data"}, Path("/data") / "mcp-hub" / "connection.json"),
        )
        for environ, expected in cases:
            with self.subTest(environ=environ), patch.dict(service_config.os.environ, environ, clear=True):
                self.assertEqual(service_config.connection_file(), expected)
                self.assertEqual(connection.connection_path(), expected)
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(service_config.os.environ, {"MCP_HUB_PRIVATE_DIR": directory}, clear=True), \
                patch.object(service_config, "PRIVATE_ROOT", Path(directory).resolve()):
            # Tests that relocate the private files never touch the real connection file.
            self.assertEqual(service_config.connection_file(), Path(directory).resolve() / "connection.json")
            self.assertEqual(connection.connection_path(), Path(directory).resolve() / "connection.json")
        home = {"HOME": "/profile", "USERPROFILE": "/profile"}
        with patch.dict(service_config.os.environ, home, clear=True):
            expected = Path.home() / ".local" / "state" / "mcp-hub" / "connection.json"
            self.assertEqual(service_config.connection_file(), expected)
            self.assertEqual(connection.connection_path(), expected)

    def test_the_service_publishes_the_file_and_logs_only_the_error_type(self) -> None:
        from unittest.mock import MagicMock, patch

        from mcp_hub import service

        audit, config = MagicMock(), object()
        with patch.object(service, "write_connection_file") as write:
            service._publish_connection(config, "admin", audit)
        write.assert_called_once_with(config, "admin")
        audit.write.assert_not_called()
        with patch.object(service, "write_connection_file", side_effect=PermissionError("denied")):
            service._publish_connection(config, "admin", audit)
        audit.write.assert_called_once()
        self.assertEqual(audit.write.call_args.args[0], "connection_file.failed")
        self.assertEqual(audit.write.call_args.kwargs["error_type"], "PermissionError")


if __name__ == "__main__":
    unittest.main()
