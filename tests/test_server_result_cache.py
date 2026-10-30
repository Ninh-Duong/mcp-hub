from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from mcp.types import CallToolResult, TextContent, Tool

from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.server import HubRuntime

READ = ToolRoute("jira", "child", "read_ticket", "jira__read_ticket", cache_seconds=60)
SYNC = ToolRoute("jira", "child", "sync_ticket", "jira__sync_ticket", cache_seconds=60)
UNCACHED = ToolRoute("jira", "child", "list_synced", "jira__list_synced")


def tool(name: str, read_only: bool) -> Tool:
    return Tool(name=name, input_schema={"type": "object"}, annotations={"readOnlyHint": read_only})


class FakeClient:
    def __init__(self, *, error: bool = False) -> None:
        self.calls: list[str] = []
        self.error = error
        self.prefix = ""

    async def call_tool(self, name: str, arguments: dict) -> CallToolResult:
        self.calls.append(name)
        text = f"{self.prefix}{name} #{len(self.calls)}"
        return CallToolResult(content=[TextContent(type="text", text=text)], is_error=self.error)


class HubResultCacheTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        config = HubConfig(tmp / "hub.json", {"child": ServerConfig("child", "python", (), tmp, {}, ())}, (READ, SYNC, UNCACHED))
        self.audit = MagicMock()
        self.runtime = HubRuntime(config, self.audit)
        self.client = FakeClient()
        # SYNC declares a TTL but is not read-only, so it must never be cached.
        self.runtime.child_connected("child", self.client, {
            "read_ticket": tool("read_ticket", True), "sync_ticket": tool("sync_ticket", False),
            "list_synced": tool("list_synced", True),
        })

    def events(self) -> list[str]:
        return [call.args[0] for call in self.audit.write.call_args_list]

    async def call(self, name: str, **arguments) -> str:
        result = await self.runtime.call_child_tool(name, arguments)
        return result.content[0].text

    async def test_read_only_tool_with_ttl_is_served_from_cache_and_audited(self) -> None:
        self.assertEqual(await self.call("jira__read_ticket", key="A-1"), "read_ticket #1")
        self.assertEqual(await self.call("jira__read_ticket", key="A-1"), "read_ticket #1")
        self.assertEqual(self.client.calls, ["read_ticket"])
        self.assertIn("tool.call.cached", self.events())

    async def test_tools_without_ttl_or_read_only_hint_always_reach_the_child(self) -> None:
        await self.call("jira__list_synced")
        await self.call("jira__list_synced")
        await self.call("jira__sync_ticket", key="A-1")
        await self.call("jira__sync_ticket", key="A-1")
        self.assertEqual(self.client.calls, ["list_synced", "list_synced", "sync_ticket", "sync_ticket"])

    async def test_a_write_tool_drops_cached_reads_of_the_same_child(self) -> None:
        await self.call("jira__read_ticket", key="A-1")
        await self.call("jira__sync_ticket", key="A-1")
        self.assertEqual(await self.call("jira__read_ticket", key="A-1"), "read_ticket #3")

    async def test_error_results_are_not_cached(self) -> None:
        self.client.error = True
        await self.call("jira__read_ticket", key="A-1")
        await self.call("jira__read_ticket", key="A-1")
        self.assertEqual(self.client.calls, ["read_ticket", "read_ticket"])

    async def test_error_looking_text_results_are_not_cached(self) -> None:
        for prefix in ("❌ ", "Error: "):
            with self.subTest(prefix=prefix):
                self.client.prefix = prefix
                self.client.calls.clear()
                await self.call("jira__read_ticket", key=prefix)
                await self.call("jira__read_ticket", key=prefix)
                self.assertEqual(self.client.calls, ["read_ticket", "read_ticket"])
        self.assertNotIn("tool.call.cached", self.events())

    async def test_call_to_a_lost_child_fails_fast_as_unavailable(self) -> None:
        self.runtime.child_lost("child")
        result = await self.runtime.call_child_tool("jira__read_ticket", {"key": "A-1"}, request_id="r-1")
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0].text, "Child MCP child is reconnecting; retry shortly. request_id=r-1")
        self.assertEqual(self.client.calls, [])
        failed = [call.kwargs for call in self.audit.write.call_args_list if call.args[0] == "tool.call.failed"]
        self.assertEqual(failed[0]["error_type"], "ChildUnavailable")

    async def test_hub_status_reports_cache_stats(self) -> None:
        await self.call("jira__read_ticket", key="A-1")
        await self.call("jira__read_ticket", key="A-1")
        status = json.loads(self.runtime._hub_status("status").content[0].text)
        self.assertEqual(status["result_cache"]["hits"], 1)
        self.assertEqual(status["result_cache"]["misses"], 1)


if __name__ == "__main__":
    unittest.main()
