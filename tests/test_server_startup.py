"""The Hub is ready after `children.startupWaitSeconds` even when a child hangs; a child that connects later joins
through its own registration (SC-004)."""
from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mcp.types import Tool

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.server import HubRuntime
from mcp_hub.settings import ChildrenSettings, Settings

SLOW_ROUTE = ToolRoute("feature", "slow", "read", "slow__read")
STARTUP_WAIT_SECONDS = 0.2
READ_TOOL = Tool(name="read", input_schema={"type": "object"}, annotations={"readOnlyHint": True})


class _Client:
    async def list_tools(self, cursor: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(tools=[READ_TOOL], next_cursor=None)


class StartupWaitTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.audit = AuditLog(root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        child = ServerConfig("slow", "python", (), root, {}, (), timeout_seconds=60.0)
        config = HubConfig(root / "hub.json", {"slow": child}, (SLOW_ROUTE,))
        settings = Settings(children=ChildrenSettings(startup_wait_seconds=STARTUP_WAIT_SECONDS))
        self.runtime = HubRuntime(config, self.audit, settings)
        self.runtime.child_log_dir = root / "children"
        self.runtime.child_opener = self.opener
        self.release = asyncio.Event()
        self.stack = AsyncExitStack()
        self.addAsyncCleanup(self.stack.aclose)
        self.addCleanup(self.runtime.events.close)

    @asynccontextmanager
    async def opener(self, config: Any, errlog: Any, on_closed: Any, *, message_handler: Any) -> AsyncIterator[Any]:
        """The child hangs until the test releases it, the way a child that is slow to start does."""
        await self.release.wait()
        yield _Client()

    def exposed(self) -> list[str]:
        return [tool.name for tool in self.runtime.public_tools]

    async def test_the_hub_is_ready_while_a_child_hangs(self) -> None:
        started = time.monotonic()
        await asyncio.wait_for(self.runtime.connect(self.stack), 10)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0)
        self.assertNotIn("slow__read", self.exposed())

    async def test_a_child_that_connects_later_joins_the_tool_list(self) -> None:
        await asyncio.wait_for(self.runtime.connect(self.stack), 10)
        self.release.set()
        for _ in range(200):
            if "slow__read" in self.exposed():
                break
            await asyncio.sleep(0.02)
        self.assertIn("slow__read", self.exposed())


if __name__ == "__main__":
    unittest.main()
