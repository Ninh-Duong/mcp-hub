"""A change to the exposed tool set is announced once per debounce window (FR-006, SC-003)."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from mcp.types import Tool, ToolListChangedNotification

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
        return sum(json.loads(line)["event"] == "tools.list_changed.published" for line in lines)

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


class EventsFakeClient:
    """Lists whatever tools its child currently has, like a child MCP session would."""

    def __init__(self, child: EventsFakeChild) -> None:
        self.child = child

    async def list_tools(self, cursor: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(tools=list(self.child.tools), next_cursor=None)


class EventsFakeChild:
    """Stands in for a child process: the opener keeps the SDK message handler the supervisor passes in."""

    def __init__(self) -> None:
        self.tools: list[Tool] = [READ_TOOL]
        self.handler: Any = None

    @asynccontextmanager
    async def opener(self, config: Any, errlog: Any, on_closed: Any, *, message_handler: Any) -> AsyncIterator[Any]:
        self.handler = message_handler
        yield EventsFakeClient(self)


class ChildSentChangeTest(unittest.IsolatedAsyncioTestCase):
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
        self.runtime.child_log_dir = self.root / "children"
        self.fake = EventsFakeChild()
        self.runtime.child_opener = self.fake.opener
        self.stack = AsyncExitStack()
        self.addAsyncCleanup(self.stack.aclose)
        self.addCleanup(self.runtime.events.close)
        await self.runtime.connect(self.stack)

    def exposed(self) -> list[str]:
        return [tool.name for tool in self.runtime.public_tools]

    async def test_child_sent_change_updates_the_tool_list(self) -> None:
        self.assertNotIn("child__extra", self.exposed())
        self.fake.tools.append(EXTRA_TOOL)
        await self.fake.handler(ToolListChangedNotification())
        self.assertIn("child__extra", self.exposed())
        self.assertIn("child__extra", self.runtime.routes)

    async def test_child_sent_change_is_announced_once(self) -> None:
        publish = AsyncMock()
        self.runtime.events.bus.publish = publish
        self.fake.tools.append(EXTRA_TOOL)
        await self.fake.handler(ToolListChangedNotification())
        await asyncio.sleep(DEBOUNCE_SECONDS * 20)
        self.assertEqual(publish.await_count, 1)


if __name__ == "__main__":
    unittest.main()
