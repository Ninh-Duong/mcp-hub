from __future__ import annotations

import asyncio
from dataclasses import replace
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from mcp.types import CallToolRequestParams, CallToolResult, TextContent, Tool

from mcp_hub import batch
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.server import HubRuntime
from mcp_hub.settings import BatchSettings, PolicySettings, Settings

ROUTES = (
    ToolRoute("logs", "logs-child", "query", "logs__query", cache_seconds=60),
    ToolRoute("logs", "logs-child", "export", "logs__export"),
    ToolRoute("jira", "jira-child", "read", "jira__read"),
)


def tool(name: str, read_only: bool) -> Tool:
    return Tool(name=name, input_schema={"type": "object"}, annotations={"readOnlyHint": read_only})


class SlowClient:
    """A child whose calls take `delay` seconds and record how many run at once."""

    def __init__(self, delay: float = 0.3) -> None:
        self.delay = delay
        self.running = 0
        self.peak = 0
        self.calls: list[tuple[str, dict]] = []
        self.fail: set[str] = set()

    async def call_tool(self, name: str, arguments: dict) -> CallToolResult:
        self.calls.append((name, arguments))
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await asyncio.sleep(arguments.get("delay", self.delay))
        finally:
            self.running -= 1
        text = f"{name} {arguments.get('env', '')}".strip()
        return CallToolResult(content=[TextContent(type="text", text=text)], is_error=name in self.fail)


def calls(*items: tuple[str, dict]) -> dict:
    return {"calls": [{"tool": name, "arguments": arguments} for name, arguments in items]}


class BatchRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        root = Path(tempfile.mkdtemp())
        servers = {sid: ServerConfig(sid, "python", (), root, {}, ()) for sid in ("logs-child", "jira-child")}
        self.audit = MagicMock()
        # The usage policy has its own tests; these exercise the batch itself.
        self.runtime = HubRuntime(HubConfig(root / "hub.json", servers, ROUTES), self.audit, Settings(policy=PolicySettings(mode="off")))
        self.logs, self.jira = SlowClient(), SlowClient()
        self.runtime.child_connected("logs-child", self.logs, {"query": tool("query", True), "export": tool("export", False)})
        self.runtime.child_connected("jira-child", self.jira, {"read": tool("read", True)})

    async def call(self, arguments: dict) -> CallToolResult:
        params = CallToolRequestParams(name="hub__batch", arguments=arguments)
        return await self.runtime.call_tool(SimpleNamespace(request_id=7), params)

    def events(self) -> list[tuple[str, dict]]:
        return [(call.args[0], call.kwargs) for call in self.audit.write.call_args_list]

    async def test_the_batch_tool_is_listed_as_read_only_with_the_configured_size(self) -> None:
        listed = {tool.name: tool for tool in self.runtime.public_tools}
        self.assertTrue(listed["hub__batch"].annotations.read_only_hint)
        self.assertEqual(listed["hub__batch"].input_schema["properties"]["calls"]["maxItems"], 8)
        small = batch.batch_tool(3)
        self.assertEqual(small.input_schema["properties"]["calls"]["maxItems"], 3)
        self.assertIn("2-3 independent", small.description)

    async def test_independent_reads_of_two_children_overlap(self) -> None:
        started = time.monotonic()
        result = await self.call(calls(("logs__query", {"env": "dev"}), ("jira__read", {"key": "A-1"})))
        self.assertLess(time.monotonic() - started, 0.55)  # two 0.3 s calls, not 0.6 s in a row
        texts = [block.text for block in result.content]
        self.assertRegex(texts[0], r"^\[1\] logs__query: ok \(\d+ ms\)$")
        self.assertEqual(texts[1], "query dev")
        self.assertRegex(texts[2], r"^\[2\] jira__read: ok")
        self.assertFalse(result.is_error)

    async def test_each_call_is_audited_under_the_batch_request(self) -> None:
        self.audit.reset_mock()
        await self.call(calls(("logs__query", {"env": "dev"}), ("logs__query", {"env": "stg"})))
        events = self.events()
        self.assertEqual(events[0][0], "tool.call.started")
        self.assertEqual((events[0][1]["tool"], events[0][1]["calls"]), ("hub__batch", 2))
        items = {kwargs["request_id"] for event, kwargs in events if kwargs.get("tool") == "query"}
        self.assertEqual(items, {"7.1", "7.2"})
        self.assertEqual(events[-1][0], "tool.call.succeeded")
        self.assertEqual(events[-1][1]["failed"], 0)

    async def test_cached_reads_are_reused_inside_a_batch(self) -> None:
        await self.call(calls(("logs__query", {"env": "dev", "delay": 0}), ("jira__read", {"delay": 0})))
        await self.call(calls(("logs__query", {"env": "dev", "delay": 0}), ("jira__read", {"delay": 0})))
        self.assertEqual([name for name, _ in self.logs.calls], ["query"])  # second one came from the cache

    async def test_whole_batch_is_refused_before_anything_runs(self) -> None:
        cases = {
            "write": calls(("logs__query", {}), ("logs__export", {})),
            "unknown": calls(("logs__query", {}), ("other__tool", {})),
            "nested": calls(("logs__query", {}), ("hub__batch", {})),
            "status": calls(("logs__query", {}), ("hub__status", {})),
            "one call": calls(("logs__query", {})),
            "none": {"calls": []},
            "too many": calls(*[("logs__query", {"env": str(i)}) for i in range(BatchSettings().max_calls + 1)]),
            "no list": {"calls": "logs__query"},
            "bad arguments": {"calls": [{"tool": "logs__query", "arguments": []}, {"tool": "jira__read"}]},
            "no tool": {"calls": [{"arguments": {}}, {"tool": "jira__read"}]},
        }
        for label, arguments in cases.items():
            with self.subTest(label):
                result = await self.call(arguments)
                self.assertTrue(result.is_error)
        self.assertEqual((self.logs.calls, self.jira.calls), ([], []))
        denied = [kwargs for event, kwargs in self.events() if event == "tool.call.denied"]
        self.assertEqual(len(denied), len(cases))
        self.assertIn("may write", denied[0]["reason"])

    async def test_one_failed_call_does_not_spoil_the_others(self) -> None:
        self.jira.fail.add("read")
        result = await self.call(calls(("logs__query", {"delay": 0}), ("jira__read", {"delay": 0})))
        headers = [block.text for block in result.content if block.text.startswith("[")]
        self.assertEqual([header.split(" (")[0] for header in headers], ["[1] logs__query: ok", "[2] jira__read: error"])
        self.assertFalse(result.is_error)
        self.logs.fail.add("query")
        result = await self.call(calls(("logs__query", {"delay": 0, "env": "x"}), ("jira__read", {"delay": 0})))
        self.assertTrue(result.is_error)  # only when every call failed
        self.assertEqual(self.events()[-1][0], "tool.call.failed")

    async def test_a_call_still_running_at_the_limit_is_cancelled(self) -> None:
        self.runtime.settings = replace(self.runtime.settings, batch=BatchSettings(timeout_seconds=0.2))
        result = await self.call(calls(("logs__query", {"delay": 0}), ("jira__read", {"delay": 5})))
        texts = [block.text for block in result.content]
        self.assertTrue(texts[2].startswith("[2] jira__read: error"))
        self.assertIn("Timed out", texts[3])
        self.assertEqual(self.jira.running, 0)
        self.assertIn("tool.call.interrupted", [event for event, _ in self.events()])


class BatchUnitTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_child_serves_at_most_the_per_child_limit_at_once(self) -> None:
        client = SlowClient(delay=0.05)

        async def call_child(tool_name: str, arguments: dict, _item_id: str) -> CallToolResult:
            return await client.call_tool(tool_name, arguments)

        parsed = [batch.BatchCall(i, "logs__query", {"env": str(i)}, "logs-child") for i in range(1, 9)]
        items = await batch.run_batch(parsed, call_child, "1", lambda result: not result.is_error, per_child=4)
        self.assertEqual(client.peak, 4)
        self.assertTrue(all(item.ok for item in items))

    def test_long_results_are_cut_and_say_so(self) -> None:
        call = batch.BatchCall(1, "logs__query", {}, "logs-child")
        long = CallToolResult(content=[TextContent(type="text", text="x" * 50)])
        items = [batch.BatchItem(call, long, True, 1), batch.BatchItem(call, long, True, 1)]
        texts = [block.text for block in batch.batch_result(items, limit=60).content]
        self.assertEqual(texts[1], "x" * 50)
        self.assertEqual(texts[3], "x" * 10 + "\n[cut: batch result limit reached]")


if __name__ == "__main__":
    unittest.main()
