from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_hub import config
from mcp_hub.config import ConfigError
from mcp_hub.settings import BatchSettings, BitnetSettings, JobSettings, Settings, load_settings, parse_settings


class SettingsTest(unittest.TestCase):
    def test_an_empty_block_keeps_every_default(self) -> None:
        self.assertEqual(parse_settings({}), Settings())
        self.assertEqual(Settings().http.port, 8765)
        self.assertEqual(Settings().jobs.max_job_seconds, 900)
        self.assertEqual(Settings().jobs.max_window_hours, 168)
        self.assertEqual(Settings().bitnet.port, 8766)
        self.assertEqual(Settings().bitnet.condense_min_chars, 9000)
        self.assertEqual(Settings().batch.max_per_child, 8)
        self.assertEqual(Settings().batch.pack_chars, 9500)

    def test_camel_case_keys_override_only_what_they_name(self) -> None:
        settings = parse_settings({
            "http": {"port": 9000, "shutdownDrainSeconds": 5},
            "jobs": {"maxActive": 3, "approvalCodeMinutes": 2.5, "maxWindowHours": 72},
            "batch": {"maxCalls": 4},
            "logs": {"backups": 0},
            "bitnet": {"condenseMinChars": 8000, "threads": 0},
        })
        self.assertEqual((settings.http.port, settings.http.shutdown_drain_seconds), (9000, 5.0))
        self.assertIsInstance(settings.http.shutdown_drain_seconds, float)
        self.assertEqual(settings.jobs, JobSettings(max_active=3, approval_code_minutes=2.5, max_window_hours=72))
        self.assertEqual(settings.batch, BatchSettings(max_calls=4))
        self.assertEqual(settings.logs.backups, 0)
        self.assertEqual(settings.bitnet, BitnetSettings(condense_min_chars=8000, threads=0))

    def test_typos_and_bad_values_stop_the_hub_naming_the_key(self) -> None:
        cases = {
            "Unknown settings group: htpp": {"htpp": {}},
            "Unknown setting: settings.jobs.maxActiv": {"jobs": {"maxActiv": 3}},
            "Unknown setting: settings.bitnet.foo": {"bitnet": {"foo": 1}},
            "settings.http must be an object": {"http": 8765},
            "settings.jobs.maxActive must be a whole number": {"jobs": {"maxActive": 2.5}},
            "settings.cache.maxEntries must be a whole number": {"cache": {"maxEntries": True}},
            "settings.batch.timeoutSeconds must be a number": {"batch": {"timeoutSeconds": "120"}},
            "settings.http.port must be between 1024 and 65535": {"http": {"port": 80}},
            "settings.bitnet.port must be between 1024 and 65535": {"bitnet": {"port": 80}},
            "settings.batch.maxCalls must be at least 2": {"batch": {"maxCalls": 1}},
            "settings.jobs.maxJobSeconds must be positive": {"jobs": {"maxJobSeconds": 0}},
            "settings.children.reconnectMaxSeconds must be a number": {"children": {"reconnectMaxSeconds": float("inf")}},
        }
        for message, raw in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ConfigError, message.replace(".", r"\.")):
                parse_settings(raw)

    def test_the_block_is_read_from_the_hub_config_in_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.object(config, "PRIVATE_ROOT", root), patch.dict(config.os.environ, {}, clear=False):
                config.os.environ.pop("MCP_HUB_CONFIG", None)
                self.assertEqual(load_settings(root / "hub.json"), Settings())  # no file: defaults
                (root / "hub.json").write_text("﻿" + json.dumps({"version": 1, "settings": {"jobs": {"maxQueued": 5}}}),
                                               encoding="utf-8")
                self.assertEqual(load_settings(root / "hub.json").jobs.max_queued, 5)
                (root / "hub.json").write_text(json.dumps({"version": 1, "settings": []}), encoding="utf-8")
                with self.assertRaises(ConfigError):
                    load_settings(root / "hub.json")


if __name__ == "__main__":
    unittest.main()
