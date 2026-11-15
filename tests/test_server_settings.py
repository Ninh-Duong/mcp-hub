"""Phase 1 settings reach the objects that use them, passed in when those objects are created (settings.py)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig
from mcp_hub.server import HubRuntime, enable_core_condenser
from mcp_hub.settings import BitnetSettings, PolicySettings, Settings


class SettingsWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.audit = AuditLog(root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        self.config = HubConfig(root / "hub.json", {}, ())

    def test_the_repeat_window_reaches_the_plan_book(self) -> None:
        settings = Settings(policy=PolicySettings(repeat_after_seconds=30.0))
        runtime = HubRuntime(self.config, self.audit, settings)
        self.assertEqual(runtime.plans.repeat_after_seconds, 30.0)

    def test_the_default_repeat_window_keeps_repeats_refused(self) -> None:
        runtime = HubRuntime(self.config, self.audit, Settings())
        self.assertEqual(runtime.plans.repeat_after_seconds, 0.0)

    def test_the_full_result_store_takes_the_byte_limit_from_settings(self) -> None:
        settings = Settings(bitnet=BitnetSettings(full_result_max_bytes=1234))
        runtime = HubRuntime(self.config, self.audit, settings)
        enable_core_condenser(runtime)
        self.assertEqual(runtime.full_results.max_bytes, 1234)


if __name__ == "__main__":
    unittest.main()
