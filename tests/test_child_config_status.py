"""Per-child required settings as the Hub sees them: switches, the file the app opens, and the startup check."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_hub import config, hub_settings
from mcp_hub.child_requirements import REQUIREMENTS_FILE

REQUIRED_TOKEN = {"version": 1, "groups": [{"label": "OpenObserve", "anyOf": [["openobserve.dev.token"]]}]}


class ChildConfigStatusTest(unittest.TestCase):
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

    def declare(self, child: str, value: object) -> None:
        (self.parent / child / REQUIREMENTS_FILE).write_text(json.dumps(value), encoding="utf-8")

    def private_file(self, child: str, value: object) -> None:
        (self.private / "mcps" / f"{child}.json").write_text(json.dumps(value), encoding="utf-8")

    def switch(self, child: str) -> config.ServerSwitch:
        return next(item for item in hub_settings.read_settings().servers if item.server_id == child)

    def test_a_declared_empty_key_is_missing_and_the_file_exists(self) -> None:
        self.declare("mcp-server-logs", REQUIRED_TOKEN)
        self.private_file("mcp-server-logs", {"openobserve": {"dev": {"token": ""}}, "cwd": "../mcp-server-logs"})
        item = self.switch("mcp-server-logs")
        self.assertEqual(item.missing, ("openobserve.dev.token",))
        self.assertEqual(item.requirements, "declared")
        self.assertTrue(item.config_file_exists)
        self.assertIsNone(item.reason)

    def test_a_filled_key_is_not_missing(self) -> None:
        self.declare("mcp-server-logs", REQUIRED_TOKEN)
        self.private_file("mcp-server-logs", {"openobserve": {"dev": {"token": "not-a-real-token"}}})
        self.assertEqual(self.switch("mcp-server-logs").missing, ())

    def test_a_child_without_a_declaration_reports_none(self) -> None:
        item = self.switch("mcp-jira-sync")
        self.assertEqual((item.requirements, item.missing), ("none", ()))
        self.assertFalse(item.config_file_exists)

    def test_a_malformed_declaration_is_reported_as_invalid(self) -> None:
        (self.parent / "mcp-server-logs" / REQUIREMENTS_FILE).write_text("{oops", encoding="utf-8")
        self.assertEqual(self.switch("mcp-server-logs").requirements, "invalid")

    def test_the_placeholder_still_means_credentials_required_and_lists_missing_keys(self) -> None:
        self.declare("mcp-server-logs", REQUIRED_TOKEN)
        self.private_file("mcp-server-logs", {"openobserve": {"dev": {"token": config.SECRET_PLACEHOLDER}}})
        item = self.switch("mcp-server-logs")
        self.assertEqual(item.reason, "credentials required")
        self.assertEqual(item.missing, ("openobserve.dev.token",))

    def test_a_disabled_child_still_lists_its_missing_keys_without_a_reason(self) -> None:
        self.declare("mcp-jira-sync", {"version": 1, "groups": [{"label": "Jira", "anyOf": [["jira.api_token"]]}]})
        self.write_hub_json({"version": 1, "servers": {"mcp-jira-sync": {"enabled": False}}, "features": {}})
        item = self.switch("mcp-jira-sync")
        self.assertFalse(item.enabled)
        self.assertEqual(item.missing, ("jira.api_token",))
        self.assertIsNone(item.reason)

    def test_a_missing_private_file_lists_every_required_key(self) -> None:
        self.declare("mcp-server-logs", REQUIRED_TOKEN)
        self.assertEqual(self.switch("mcp-server-logs").missing, ("openobserve.dev.token",))

    def write_hub_json(self, value: dict) -> None:
        self.hub_json.write_text(json.dumps(value), encoding="utf-8")

    def test_prepare_copies_the_template_once_and_keeps_an_existing_file(self) -> None:
        target = hub_settings.prepare_child_config("mcp-server-logs")
        self.assertEqual(target, self.private / "mcps" / "mcp-server-logs.json")
        template = self.root / "config" / "mcps" / "mcp-server-logs.example.json"
        self.assertEqual(target.read_bytes(), template.read_bytes())
        target.write_text(json.dumps({"kept": True}), encoding="utf-8")
        hub_settings.prepare_child_config("mcp-server-logs")
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"kept": True})

    def test_prepare_refuses_a_custom_config_file(self) -> None:
        os.environ["MCP_HUB_CONFIG"] = str(self.hub_json)
        with self.assertRaises(config.ConfigError):
            hub_settings.prepare_child_config("mcp-server-logs")

    def test_prepare_rejects_an_unknown_child_with_key_error(self) -> None:
        with self.assertRaises(KeyError):
            hub_settings.prepare_child_config("no-such-child")

    def test_prepare_without_a_template_is_a_config_error(self) -> None:
        (self.root / "config" / "mcps" / "mcp-jira-sync.example.json").unlink()
        with self.assertRaises(config.ConfigError):
            hub_settings.prepare_child_config("mcp-jira-sync")
        self.assertFalse((self.private / "mcps" / "mcp-jira-sync.json").exists())

    def test_the_file_to_edit_is_the_private_copy_unless_a_config_file_is_set(self) -> None:
        self.assertEqual(config.child_config_file("mcp-server-logs"), self.private / "mcps" / "mcp-server-logs.json")
        custom = self.private / "custom.json"
        custom.write_text("{}", encoding="utf-8")
        self.write_hub_json({"version": 1, "servers": {"mcp-server-logs": {"configFile": "config/private/custom.json"}},
                             "features": {}})
        self.assertEqual(config.child_config_file("mcp-server-logs"), custom)

    def test_load_config_records_incomplete_children_by_key_name(self) -> None:
        self.declare("mcp-server-logs", REQUIRED_TOKEN)
        self.private_file("mcp-server-logs", {"transport": "stdio", "command": "node", "args": [],
                                              "cwd": "../mcp-server-logs", "openobserve": {"dev": {"token": ""}}})
        loaded = config.load_config()
        self.assertEqual(loaded.incomplete, {"mcp-server-logs": ("openobserve.dev.token",)})


if __name__ == "__main__":
    unittest.main()
