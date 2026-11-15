"""`HubEvents` calls its optional `on_publish` once per publish, not once per scheduled change (decision D10). The Hub
writes `tools.list_changed.published` from that callback, so a burst inside one debounce window writes one line."""
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
from mcp_hub.events import HubEvents
from mcp_hub.server import HubRuntime
from mcp_hub.settings import ProtocolSettings, Settings

DEBOUNCE_SECONDS = 0.01
READ = ToolRoute("feature", "child", "read", "child__read")
EXTRA = ToolRoute("feature", "child", "extra", "child__extra")
READ_TOOL = Tool(name="read", input_schema={"type": "object"}, annotations={"readOnlyHint": True})
EXTRA_TOOL = Tool(name="extra", input_schema={"type": "object"}, annotations={"readOnlyHint": True})


class OnPublishTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_callback_runs_once_per_publish(self) -> None:
        published: list[str] = []
        events = HubEvents(DEBOUNCE_SECONDS, on_publish=lambda: published.append("sent"))
        events.bus.publish = AsyncMock()
        self.addCleanup(events.close)
        await events.tools_changed("first")
        await asyncio.sleep(DEBOUNCE_SECONDS * 20)
        self.assertEqual(published, ["sent"])
        await events.tools_changed("second")
        await events.tools_changed("third")
        await asyncio.sleep(DEBOUNCE_SECONDS * 20)
        self.assertEqual(published, ["sent", "sent"])
        self.assertEqual(events.bus.publish.await_count, 2)

    async def test_no_callback_is_needed(self) -> None:
        events = HubEvents(DEBOUNCE_SECONDS)
        events.bus.publish = AsyncMock()
        self.addCleanup(events.close)
        await events.tools_changed("only")
        await asyncio.sleep(DEBOUNCE_SECONDS * 20)
        self.assertEqual(events.bus.publish.await_count, 1)


class BurstAuditTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_burst_inside_one_window_writes_one_sent_line(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        audit = AuditLog(root / "audit.jsonl")
        self.addCleanup(audit.close)
        child = ServerConfig("child", "python", (), root, {}, (), timeout_seconds=60.0)
        settings = Settings(protocol=ProtocolSettings(list_changed_debounce_seconds=DEBOUNCE_SECONDS))
        runtime = HubRuntime(HubConfig(root / "hub.json", {"child": child}, (READ, EXTRA)), audit, settings)
        self.addCleanup(runtime.events.close)
        runtime.events.prime(runtime._register_routes())
        client = object()
        runtime.child_connected("child", client, {"read": READ_TOOL})
        runtime.child_connected("child", client, {"read": READ_TOOL, "extra": EXTRA_TOOL})
        runtime.child_connected("child", client, {"read": READ_TOOL})
        await asyncio.sleep(DEBOUNCE_SECONDS * 20)
        lines = (root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        sent = [json.loads(line) for line in lines if json.loads(line)["event"] == "tools.list_changed.published"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["tools_count"], len(runtime.public_tools))


if __name__ == "__main__":
    unittest.main()
