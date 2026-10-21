import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_hub import config


class DefaultConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self.tmp.name)
        self.root = self.parent / "mcp-hub"
        (self.root / "config" / "mcps").mkdir(parents=True)
        (self.root / "config" / "private" / "mcps").mkdir(parents=True)
        source = config.HUB_ROOT / "config"
        shutil.copy2(source / "hub.example.json", self.root / "config" / "hub.example.json")
        for path in (source / "mcps").glob("mcp-*.example.json"):
            shutil.copy2(path, self.root / "config" / "mcps" / path.name)
        self.patches = [
            patch.object(config, "HUB_ROOT", self.root),
            patch.object(config, "PRIVATE_ROOT", self.root / "config" / "private"),
            patch.object(config, "_DEFAULT_CONFIG", self.root / "config" / "private" / "hub.json"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_sibling_repositories_are_discovered_without_private_files(self):
        for name in ("mcp-server-logs", "mcp-jira-sync", "mcp-pr-companion"):
            (self.parent / name).mkdir()
        loaded = config.load_config()
        self.assertEqual(set(loaded.detected), {"mcp-server-logs", "mcp-jira-sync", "mcp-pr-companion"})
        self.assertEqual(set(loaded.servers), {"mcp-server-logs", "mcp-pr-companion"})
        self.assertEqual(loaded.unavailable["mcp-jira-sync"], "credentials required")
        self.assertIn("logs__query_logs", {route.exposed_name for route in loaded.routes})

    def test_private_disable_wins_over_sibling_discovery(self):
        (self.parent / "mcp-server-logs").mkdir()
        private = self.root / "config" / "private" / "hub.json"
        private.write_text(json.dumps({"version": 1, "servers": {"mcp-server-logs": {"enabled": False}}, "features": {}}), encoding="utf-8")
        loaded = config.load_config()
        self.assertEqual(loaded.detected, ("mcp-server-logs",))
        self.assertEqual(loaded.servers, {})
        self.assertEqual(loaded.unavailable["mcp-server-logs"], "disabled")
        self.assertEqual(loaded.routes, ())

    def test_private_child_config_overrides_default_template(self):
        (self.parent / "mcp-server-logs").mkdir()
        private = self.root / "config" / "private" / "mcps" / "mcp-server-logs.json"
        private.write_text(json.dumps({"transport": "stdio", "command": "custom-node", "cwd": "../mcp-server-logs"}), encoding="utf-8")
        loaded = config.load_config()
        self.assertEqual(loaded.servers["mcp-server-logs"].command, "custom-node")

    def test_minimal_private_override_keeps_default_launch_and_routes(self):
        (self.parent / "mcp-server-logs").mkdir()
        private = self.root / "config" / "private" / "hub.json"
        private.write_text(json.dumps({"version": 1, "servers": {"mcp-server-logs": {"enabled": True}},
                                       "features": {"logs": {"enabled": True}}}), encoding="utf-8")
        loaded = config.load_config()
        self.assertEqual(loaded.servers["mcp-server-logs"].command, "node")
        self.assertIn("logs__query_logs", {route.exposed_name for route in loaded.routes})

    def test_overrides_without_enabled_keep_sibling_automatic(self):
        (self.parent / "mcp-server-logs").mkdir()
        child = self.root / "config" / "private" / "mcps" / "mcp-server-logs.json"
        child.write_text(json.dumps({"transport": "stdio", "command": "custom-node", "cwd": "../mcp-server-logs"}), encoding="utf-8")
        manifest = {"version": 1, "servers": {"mcp-server-logs": {"configFile": "config/private/mcps/mcp-server-logs.json"}},
                    "features": {"logs": {"tools": [{"server": "mcp-server-logs", "name": "query_logs", "exposeAs": "logs__query_logs"}]}}}
        (self.root / "config" / "private" / "hub.json").write_text(json.dumps(manifest), encoding="utf-8")
        loaded = config.load_config()
        self.assertEqual(loaded.servers["mcp-server-logs"].command, "custom-node")
        self.assertEqual([route.exposed_name for route in loaded.routes], ["logs__query_logs"])

    def test_no_siblings_still_has_a_valid_status_only_configuration(self):
        (self.parent / "mcp-unknown").mkdir()
        loaded = config.load_config()
        self.assertEqual(loaded.detected, ())
        self.assertEqual(loaded.servers, {})
        self.assertEqual(loaded.routes, ())

    def test_jira_uses_the_hub_venv_for_this_platform(self):
        (self.parent / "mcp-jira-sync").mkdir()
        template = json.loads((self.root / "config" / "mcps" / "mcp-jira-sync.example.json").read_text(encoding="utf-8"))
        template["jira"] = {"base_url": "https://your-domain.atlassian.net", "email": "user@example.com",
                            "api_token": "test-token", "ai_context_key": "test-key"}
        private = self.root / "config" / "private" / "mcps" / "mcp-jira-sync.json"
        private.write_text(json.dumps(template), encoding="utf-8")
        loaded = config.load_config()
        suffix = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        self.assertEqual(Path(loaded.servers["mcp-jira-sync"].command), self.root / ".venv" / suffix)


if __name__ == "__main__":
    unittest.main()
