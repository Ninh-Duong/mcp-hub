"""A change to the exposed tool set is announced once per debounce window (FR-006, SC-003)."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from mcp.types import Tool

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.server import HubRuntime
from mcp_hub.settings import ProtocolSettings, Settings

READ = ToolRoute("feature", "child", "read", "child__read")
EXTRA = ToolRoute("feature", "child", "extra", "child__extra")
READ_TOOL = Tool(name="read", input_schema={"type": "object"}, annotations={"readOnlyHint": True})
EXTRA_TOOL = Tool(name="extra", input_schema={"type": "object"}, annotations={"readOnlyHint": True})
DEBOUNCE_SECONDS = 0.01


class ToolChangeAnnouncementTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = AuditLog(self.root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        child = ServerConfig("child", "python", (), self.root, {}, (), timeout_seconds=60.0)
        config = HubConfig(self.root / "hub.json", {"child": child}, (READ, EXTRA))
        settings = Settings(protocol=ProtocolSettings(list_changed_debounce_seconds=DEBOUNCE_SECONDS))
        self.runtime = HubRuntime(config, self.audit, settings)
        self.client = object()

    async def settle(self) -> None:
        """Wait past the debounce window so a scheduled announcement has run."""
        await asyncio.sleep(DEBOUNCE_SECONDS * 20)

    def sent_lines(self) -> int:
        lines = (self.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        return sum(json.loads(line)["event"] == "tools.list_changed.sent" for line in lines)

    def count_publishes(self) -> AsyncMock:
        publish = AsyncMock()
        self.runtime.events.bus.publish = publish
        return publish

    async def test_first_connection_after_the_baseline_is_announced_once(self) -> None:
        publish = self.count_publishes()
        self.runtime.events.prime(self.runtime._register_routes())
        self.runtime.child_connected("child", self.client, {"read": READ_TOOL})
        await self.settle()
        self.assertEqual(publish.await_count, 1)
        self.assertEqual(self.sent_lines(), 1)

    async def test_reconnect_with_one_new_tool_sends_exactly_one_notification(self) -> None:
        self.runtime.events.prime(self.runtime._register_routes())
        self.runtime.child_connected("child", self.client, {"read": READ_TOOL})
        await self.settle()
        publish = self.count_publishes()
        self.runtime.child_connected("child", self.client, {"read": READ_TOOL, "extra": EXTRA_TOOL})
        await self.settle()
        self.assertEqual(publish.await_count, 1)
        self.assertEqual(self.sent_lines(), 2)

    async def test_a_burst_inside_one_window_sends_one_notification(self) -> None:
        self.runtime.events.prime(self.runtime._register_routes())
        publish = self.count_publishes()
        self.runtime.child_connected("child", self.client, {"read": READ_TOOL})
        self.runtime.child_connected("child", self.client, {"read": READ_TOOL, "extra": EXTRA_TOOL})
        self.runtime.child_connected("child", self.client, {"read": READ_TOOL})
        await self.settle()
        self.assertEqual(publish.await_count, 1)

    async def test_same_tools_again_send_nothing(self) -> None:
        self.runtime.events.prime(self.runtime._register_routes())
        self.runtime.child_connected("child", self.client, {"read": READ_TOOL})
        await self.settle()
        publish = self.count_publishes()
        self.runtime.child_connected("child", self.client, {"read": READ_TOOL})
        await self.settle()
        self.assertEqual(publish.await_count, 0)


if __name__ == "__main__":
    unittest.main()
