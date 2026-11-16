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

    def test_browser_pairing_settings_are_removed(self) -> None:
        self.assertFalse(hasattr(Settings(), "dashboard"))
        self.assertEqual(parse_settings({"dashboard": {"pairingSeconds": 120}}), Settings())
        with self.assertRaisesRegex(ConfigError, r"settings\.dashboard must be an object"):
            parse_settings({"dashboard": 12})

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

    def test_the_standards_alignment_keys_have_their_defaults(self) -> None:
        settings = Settings()
        self.assertEqual((settings.bridge.presence_retry_seconds, settings.bridge.connect_timeout_seconds), (5.0, 60.0))
        self.assertEqual(settings.protocol.validate_arguments, "warn")
        self.assertEqual(settings.protocol.list_changed_debounce_seconds, 1.0)
        self.assertEqual(settings.protocol.tools_list_ttl_ms, 0)
        self.assertEqual((settings.security.tool_pin_mode, settings.security.max_description_chars), ("warn", 4096))
        self.assertEqual(settings.children.startup_wait_seconds, 10.0)
        self.assertEqual(settings.policy.repeat_after_seconds, 0.0)
        self.assertEqual(settings.jobs.coordinator_seconds, 90.0)
        self.assertEqual(settings.jobs.specialist_max_rounds, 8)
        self.assertEqual(settings.jobs.tool_result_chars, 6000)
        self.assertEqual(settings.jobs.context_chars, 48000)
        self.assertIs(settings.jobs.prompt_cache, True)
        self.assertEqual(settings.bitnet.full_result_max_bytes, 33554432)

    def test_new_keys_accept_their_documented_ranges(self) -> None:
        settings = parse_settings({
            "protocol": {"validateArguments": "off", "listChangedDebounceSeconds": 0, "toolsListTtlMs": 0},
            "security": {"toolPinMode": "off", "maxDescriptionChars": 512},
            "policy": {"repeatAfterSeconds": 0},
            "jobs": {"promptCache": False, "contextChars": 1000},
            "bitnet": {"fullResultMaxBytes": 1024},
        })
        self.assertEqual(settings.protocol.validate_arguments, "off")
        self.assertIs(settings.jobs.prompt_cache, False)
        self.assertEqual(settings.security.max_description_chars, 512)
        self.assertEqual(settings.bitnet.full_result_max_bytes, 1024)

    def test_new_keys_refuse_bad_values_naming_the_key(self) -> None:
        cases = {
            "settings.jobs.promptCache must be true or false": {"jobs": {"promptCache": "yes"}},
            "settings.protocol.validateArguments must be one of: off, warn": {"protocol": {"validateArguments": "block"}},
            "settings.security.toolPinMode must be one of: off, warn": {"security": {"toolPinMode": "block"}},
            "settings.protocol.toolsListTtlMs must be at least 0": {"protocol": {"toolsListTtlMs": -1}},
            "settings.policy.repeatAfterSeconds must be at least 0": {"policy": {"repeatAfterSeconds": -0.5}},
            "settings.children.startupWaitSeconds must be positive": {"children": {"startupWaitSeconds": 0}},
            "settings.bridge.connectTimeoutSeconds must be a number": {"bridge": {"connectTimeoutSeconds": "60"}},
        }
        for message, raw in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ConfigError, message.replace(".", r"\.")):
                parse_settings(raw)


if __name__ == "__main__":
    unittest.main()
