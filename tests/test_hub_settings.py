"""hub_settings: the child switches and the saved activity-UI mode the desktop app writes to hub.json."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_hub import config, hub_settings
from mcp_hub.config import ServerSwitch
from mcp_hub.extensions import ACTIVITY_UI, saved_extensions


class HubSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self.tmp.name).resolve()
        self.root = self.parent / "mcp-hub"
        (self.root / "config" / "mcps").mkdir(parents=True)
        self.private = self.root / "config" / "private"
        (self.private / "mcps").mkdir(parents=True)
        source = config.HUB_ROOT / "config"
        shutil.copy2(source / "hub.example.json", self.root / "config" / "hub.example.json")
        for path in (source / "mcps").glob("mcp-*.example.json"):
            shutil.copy2(path, self.root / "config" / "mcps" / path.name)
        for name in ("mcp-server-logs", "mcp-jira-sync"):
            (self.parent / name).mkdir()
        self.hub_json = self.private / "hub.json"
        self.patches = [
            patch.object(config, "HUB_ROOT", self.root),
            patch.object(config, "PRIVATE_ROOT", self.private),
            patch.object(config, "_DEFAULT_CONFIG", self.hub_json),
            patch.dict(os.environ, {}, clear=False),
        ]
        for item in self.patches:
            item.start()
        os.environ.pop("MCP_HUB_CONFIG", None)

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def write_hub_json(self, value: dict) -> None:
        self.hub_json.write_text(json.dumps(value), encoding="utf-8")

    def test_without_a_private_config_detected_children_are_on_and_fast_mode_is_saved(self) -> None:
        settings = hub_settings.read_settings()
        self.assertEqual(settings.servers, (ServerSwitch("mcp-server-logs", True), ServerSwitch("mcp-jira-sync", True)))
        self.assertFalse(settings.activity_ui)
        self.assertTrue(settings.editable)

    def test_switching_a_child_keeps_every_other_key(self) -> None:
        self.write_hub_json({"version": 1, "servers": {"mcp-server-logs": {"enabled": True, "note": "kept"}},
                             "features": {"logs": {"enabled": True}}})
        hub_settings.set_server_enabled("mcp-server-logs", False)
        saved = json.loads(self.hub_json.read_text(encoding="utf-8"))
        self.assertEqual(saved["servers"]["mcp-server-logs"], {"enabled": False, "note": "kept"})
        self.assertEqual(saved["features"], {"logs": {"enabled": True}})
        self.assertIn(ServerSwitch("mcp-server-logs", False), hub_settings.read_settings().servers)
        self.assertNotIn("mcp-server-logs", config.load_config().servers)
        self.assertEqual(list(self.private.glob(".hub-*")), [])

    def test_a_missing_config_is_created_with_only_the_change(self) -> None:
        hub_settings.set_server_enabled("mcp-jira-sync", False)
        self.assertEqual(json.loads(self.hub_json.read_text(encoding="utf-8")),
                         {"version": 1, "servers": {"mcp-jira-sync": {"enabled": False}}, "features": {}})

    def test_unknown_or_missing_children_are_refused(self) -> None:
        for server_id in ("mcp-read-source", "not-a-child"):
            with self.subTest(server_id=server_id), self.assertRaises(config.ConfigError):
                hub_settings.set_server_enabled(server_id, True)
        self.assertFalse(self.hub_json.exists())

    def test_tracking_is_saved_for_every_start_and_removed_when_off(self) -> None:
        self.write_hub_json({"version": 1, "servers": {}, "features": {}})
        hub_settings.set_activity_ui(True)
        self.assertTrue(hub_settings.read_settings().activity_ui)
        self.assertEqual(saved_extensions(self.hub_json), frozenset({ACTIVITY_UI}))
        config.load_config()  # the saved mode never breaks the config schema
        hub_settings.set_activity_ui(False)
        self.assertNotIn("extensions", json.loads(self.hub_json.read_text(encoding="utf-8")))

    def test_credentials_placeholder_is_reported_without_reading_out_values(self) -> None:
        child = {"transport": "stdio", "command": "node", "cwd": "../mcp-server-logs", "env": {"TOKEN": config.SECRET_PLACEHOLDER}}
        (self.private / "mcps" / "mcp-server-logs.json").write_text(json.dumps(child), encoding="utf-8")
        switches = {switch.server_id: switch for switch in hub_settings.read_settings().servers}
        self.assertEqual(switches["mcp-server-logs"].reason, "credentials required")
        self.assertIsNone(switches["mcp-jira-sync"].reason)
        (self.private / "mcps" / "mcp-server-logs.json").write_text("not json", encoding="utf-8")
        self.assertIsNone(hub_settings.read_settings().servers[0].reason)

    def test_an_explicit_config_is_read_only(self) -> None:
        custom = self.private / "custom.json"
        custom.write_text(json.dumps({"version": 1, "servers": {"mcp-server-logs": {"enabled": False}}, "features": {},
                                      "extensions": [ACTIVITY_UI]}), encoding="utf-8")
        with patch.dict(os.environ, {"MCP_HUB_CONFIG": str(custom)}):
            settings = hub_settings.read_settings()
            self.assertFalse(settings.editable)
            self.assertTrue(settings.activity_ui)
            self.assertEqual(settings.servers, (ServerSwitch("mcp-server-logs", False),))
            for change in (lambda: hub_settings.set_activity_ui(False),
                           lambda: hub_settings.set_server_enabled("mcp-server-logs", True)):
                with self.assertRaises(config.ConfigError):
                    change()

    def test_unreadable_or_malformed_configs_are_refused(self) -> None:
        for content, error in (("not json", "Could not read"), ("[]", "must be an object")):
            self.hub_json.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaisesRegex(config.ConfigError, error):
                hub_settings.set_activity_ui(True)
        self.write_hub_json({"version": 1, "servers": {"mcp-server-logs": "on"}, "features": {}})
        with self.assertRaises(config.ConfigError):
            hub_settings.set_server_enabled("mcp-server-logs", False)

    def test_a_failed_write_leaves_the_old_file_and_no_temporary_file(self) -> None:
        self.write_hub_json({"version": 1, "servers": {}, "features": {}})
        with patch.object(hub_settings.os, "replace", side_effect=OSError("locked")), self.assertRaises(OSError):
            hub_settings.set_activity_ui(True)
        self.assertNotIn("extensions", json.loads(self.hub_json.read_text(encoding="utf-8")))
        self.assertEqual(list(self.private.glob(".hub-*")), [])


if __name__ == "__main__":
    unittest.main()
