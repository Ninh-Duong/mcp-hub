"""Pins are saved from a snapshot taken on the calling thread (decision D10): a later check cannot change what is being
written, and a failed save is logged at WARNING with the exception type only."""
from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mcp.types import Tool

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.server import HubRuntime
from mcp_hub.settings import PolicySettings, Settings
from mcp_hub.tool_pins import PinStore, fingerprint

TOOL_A = Tool(name="alpha", description="Alpha.", input_schema={"type": "object"})
TOOL_B = Tool(name="beta", description="Beta.", input_schema={"type": "object"})


class _GatedPinStore(PinStore):
    """Holds the write until the gate opens, so a check can run while the save is in progress."""

    def __init__(self, path: Path, gate: threading.Event) -> None:
        super().__init__(path)
        self.gate = gate

    def _write(self, payload: dict[str, Any]) -> None:
        self.gate.wait(5)
        super()._write(payload)


class SnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "tool_pins.json"

    def test_a_later_check_does_not_change_the_snapshot(self) -> None:
        store = PinStore(self.path)
        store.check("child", [TOOL_A])
        snapshot = store.snapshot()
        before = json.dumps(snapshot, sort_keys=True)
        store.check("child", [TOOL_B])
        self.assertEqual(json.dumps(snapshot, sort_keys=True), before)
        self.assertEqual(snapshot["child"], {"alpha": fingerprint(TOOL_A)})

    def test_a_check_during_a_worker_thread_save_does_not_change_what_is_written(self) -> None:
        gate = threading.Event()
        store = _GatedPinStore(self.path, gate)
        store.check("child", [TOOL_A])
        snapshot = store.snapshot()
        worker = threading.Thread(target=store.save, args=(snapshot,))
        worker.start()
        store.check("child", [TOOL_B])  # the pins change while the save waits at the gate
        gate.set()
        worker.join(5)
        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(written["servers"]["child"], {"alpha": fingerprint(TOOL_A)})


class SaveErrorTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_save_error_is_logged_with_its_type_only(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        audit = AuditLog(root / "audit.jsonl")
        self.addCleanup(audit.close)
        child = ServerConfig("child", "python", (), root, {}, (), timeout_seconds=60.0)
        route = ToolRoute("feature", "child", "alpha", "child__alpha")
        settings = Settings(policy=PolicySettings(mode="off"))
        runtime = HubRuntime(HubConfig(root / "hub.json", {"child": child}, (route,)), audit, settings,
                             pin_path=root / "tool_pins.json")
        self.addCleanup(runtime.events.close)
        secret_text = "disk full, marker abc123"
        with self.assertLogs("mcp_hub.server", level="WARNING") as logs, \
                _failing_replace(OSError(secret_text)):
            runtime.child_connected("child", object(), {"alpha": TOOL_A})
            for _ in range(100):
                if logs.records:
                    break
                await asyncio.sleep(0.02)
        message = logs.output[0]
        self.assertIn("OSError", message)
        self.assertNotIn("abc123", message)
        self.assertNotIn("disk full", message)


def _failing_replace(error: Exception) -> Any:
    return patch("mcp_hub.tool_pins.os.replace", side_effect=error)


if __name__ == "__main__":
    unittest.main()
