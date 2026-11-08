from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.types import CallToolRequestParams, CallToolResult, TextContent, Tool

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ToolRoute
from mcp_hub.extensions import Condensed, PlanContext, PlanHints
from mcp_hub.full_results import FullResultStore
from mcp_hub.server import FULL_RESULT_TOOL, HubRuntime
from mcp_hub.settings import Settings


class FakeCondenser:
    def __init__(
        self,
        text: str = "condensed summary",
        kept: int = 5,
        total: int = 100,
        delay: float = 0.0,
        fail: bool = False,
    ) -> None:
        self.text = text
        self.kept = kept
        self.total = total
        self.delay = delay
        self.fail = fail

    async def condense(self, text: str, task: str | None) -> Condensed | None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("condenser failed")
        return Condensed(text=self.text, kept_lines=self.kept, total_lines=self.total, source="bitnet")


class ServerFullResultToolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = AuditLog(self.root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        self.config = HubConfig(self.root / "hub.json", {}, ())
        self.runtime = HubRuntime(self.config, self.audit)
        self.ctx = SimpleNamespace(request_id="req-1", session=None)

    async def test_tool_absent_from_list_tools_when_full_results_is_none(self) -> None:
        self.assertIsNone(self.runtime.full_results)
        result = await self.runtime.list_tools(self.ctx, None)
        tool_names = [tool.name for tool in result.tools]
        self.assertNotIn(FULL_RESULT_TOOL, tool_names)

    async def test_tool_present_in_list_tools_when_full_results_is_set(self) -> None:
        self.runtime.full_results = FullResultStore(max_entries=10, ttl_seconds=60.0)
        result = await self.runtime.list_tools(self.ctx, None)
        tool_names = [tool.name for tool in result.tools]
        self.assertIn(FULL_RESULT_TOOL, tool_names)

    async def test_calling_get_full_result_returns_paged_content(self) -> None:
        store = FullResultStore(max_entries=10, ttl_seconds=60.0)
        self.runtime.full_results = store
        rid = store.put("0123456789" * 10)  # 100 chars

        params = CallToolRequestParams(name=FULL_RESULT_TOOL, arguments={"result_id": rid, "offset": 10, "limit": 20})
        res = await self.runtime.call_tool(self.ctx, params)
        self.assertFalse(res.is_error)
        data = json.loads(res.content[0].text)
        self.assertEqual(data["result_id"], rid)
        self.assertEqual(data["offset"], 10)
        self.assertEqual(data["limit"], 20)
        self.assertEqual(data["text"], "01234567890123456789")
        self.assertEqual(data["next_offset"], 30)

        # Audit events check
        lines = (self.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        events = [json.loads(line)["event"] for line in lines]
        self.assertIn("tool.call.started", events)
        self.assertIn("tool.call.succeeded", events)

    async def test_calling_get_full_result_with_unknown_id_returns_error(self) -> None:
        self.runtime.full_results = FullResultStore(max_entries=10, ttl_seconds=60.0)
        params = CallToolRequestParams(name=FULL_RESULT_TOOL, arguments={"result_id": "r-unknown"})
        res = await self.runtime.call_tool(self.ctx, params)
        self.assertTrue(res.is_error)
        self.assertIn("Result expired or unknown", res.content[0].text)


class ServerCondenseHookTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = AuditLog(self.root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        route = ToolRoute("feature", "logs-server", "query", "logs__query")
        self.config = HubConfig(self.root / "hub.json", {}, (route,))
        settings = Settings()
        self.runtime = HubRuntime(self.config, self.audit, settings=settings)
        self.runtime.routes["logs__query"] = route
        self.runtime._read_only.add("logs__query")
        self.store = FullResultStore(max_entries=10, ttl_seconds=60.0)
        self.runtime.full_results = self.store
        self.runtime.condenser = FakeCondenser()

    async def test_small_result_not_condensed(self) -> None:
        small = CallToolResult(content=[TextContent(type="text", text="small result")])
        res = await self.runtime._maybe_condense("logs__query", small, "find errors", "req-1")
        self.assertEqual(res, small)

    async def test_error_result_not_condensed(self) -> None:
        large_error = CallToolResult(
            content=[TextContent(type="text", text="error\n" * 1500)],
            is_error=True,
        )
        res = await self.runtime._maybe_condense("logs__query", large_error, "find errors", "req-1")
        self.assertEqual(res, large_error)

    async def test_write_tool_not_condensed(self) -> None:
        self.runtime._read_only.discard("logs__query")
        large = CallToolResult(content=[TextContent(type="text", text="data\n" * 1500)])
        res = await self.runtime._maybe_condense("logs__query", large, "find errors", "req-1")
        self.assertEqual(res, large)

    async def test_large_read_only_result_is_condensed(self) -> None:
        large_text = "log line with information\n" * 400  # > 6000 chars
        large = CallToolResult(content=[TextContent(type="text", text=large_text)])
        res = await self.runtime._maybe_condense("logs__query", large, "find errors", "req-1")
        self.assertFalse(res.is_error)
        output = res.content[0].text
        self.assertIn("condensed summary", output)
        self.assertIn("[Condensed by mcp-hub: kept 5 of 100 lines.", output)
        self.assertIn("Full result: hub__get_full_result result_id=\"r-", output)

        # Retrieve full result from store
        rid = output.split('result_id="')[1].split('"')[0]
        paged = self.store.page(rid)
        self.assertIsNotNone(paged)
        self.assertEqual(paged["total_chars"], len(large_text))

        # Check audit event and ensure no raw text is logged
        lines = (self.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        condense_events = [json.loads(line) for line in lines if "tool.result.condensed" in line]
        self.assertEqual(len(condense_events), 1)
        ev = condense_events[0]
        self.assertEqual(ev["source"], "bitnet")
        self.assertEqual(ev["kept_lines"], 5)
        self.assertEqual(ev["total_lines"], 100)
        self.assertNotIn("log line with information", json.dumps(ev))

    async def test_condenser_failure_falls_back_to_original(self) -> None:
        self.runtime.condenser = FakeCondenser(fail=True)
        large_text = "log line with information\n" * 400
        large = CallToolResult(content=[TextContent(type="text", text=large_text)])
        res = await self.runtime._maybe_condense("logs__query", large, "find errors", "req-1")
        self.assertEqual(res, large)

        lines = (self.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        ev = [json.loads(line) for line in lines if "tool.result.condensed" in line][0]
        self.assertEqual(ev["source"], "fallback")

    async def test_condenser_none_regression(self) -> None:
        self.runtime.condenser = None
        large_text = "log line with information\n" * 400
        large = CallToolResult(content=[TextContent(type="text", text=large_text)])
        res = await self.runtime._maybe_condense("logs__query", large, "find errors", "req-1")
        self.assertEqual(res, large)

    async def test_batch_condenses_only_large_item(self) -> None:
        large_text = "log line with information\n" * 400

        async def fake_call_child(name: str, args: dict, **kwargs) -> CallToolResult:
            if args.get("large"):
                return CallToolResult(content=[TextContent(type="text", text=large_text)])
            return CallToolResult(content=[TextContent(type="text", text="short text")])

        self.runtime.call_child_tool = fake_call_child  # type: ignore[assignment]
        self.runtime.settings = Settings(policy=self.runtime.settings.policy.__class__(mode="off"))
        batch_args = {
            "calls": [
                {"tool": "logs__query", "arguments": {"large": False}},
                {"tool": "logs__query", "arguments": {"large": True}},
            ]
        }
        res = await self.runtime._call_batch(batch_args, "req-batch", "client")
        self.assertFalse(res.is_error)
        output = "\n".join(b.text for b in res.content if hasattr(b, "text"))
        self.assertIn("short text", output)
        self.assertIn("[Condensed by mcp-hub:", output)




class FakePlanAssistant:
    def __init__(self, hints: PlanHints | None = None, fail: bool = False, delay: float = 0.0) -> None:
        self._hints = hints
        self.fail = fail
        self.delay = delay

    async def hints(self, context: PlanContext) -> PlanHints | None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("assistant failed")
        return self._hints


class ServerPlanAssistTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = AuditLog(self.root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        route = ToolRoute("feature", "logs-server", "query", "logs__query")
        self.config = HubConfig(self.root / "hub.json", {}, (route,))
        settings = Settings()
        self.runtime = HubRuntime(self.config, self.audit, settings=settings)
        from mcp_hub.policy import CallFacts
        self.runtime.routes["logs__query"] = route
        self.runtime._call_facts["logs__query"] = CallFacts("logs__query", "logs-server", True, True)
        self.runtime.public_tools = [Tool(name="logs__query", input_schema={"type": "object"})]
        self.runtime.clients["logs-server"] = SimpleNamespace()  # mark as connected child
        self.ctx = SimpleNamespace(request_id="req-plan", session=None)

    async def test_plan_created_without_assistant_has_assist_off(self) -> None:
        args = {"task": "Find error", "servers": ["logs-server"], "steps": ["step 1"]}
        params = CallToolRequestParams(name="hub__plan", arguments=args)
        res = await self.runtime.call_tool(self.ctx, params)
        self.assertFalse(res.is_error)
        lines = (self.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        ev = [json.loads(line) for line in lines if "hub.plan.created" in line][0]
        self.assertEqual(ev["assist"], "off")

    async def test_plan_assistant_fills_servers_env_and_window(self) -> None:
        from datetime import UTC, datetime, timedelta
        now = datetime.now(UTC)
        hints = PlanHints(
            servers=("logs-server",),
            env="stg",
            since=now - timedelta(hours=2),
            until=now,
            source="rules",
        )
        self.runtime.plan_assistant = FakePlanAssistant(hints=hints)
        args = {"task": "Find error in stg", "steps": ["step 1"]}
        params = CallToolRequestParams(name="hub__plan", arguments=args)
        res = await self.runtime.call_tool(self.ctx, params)
        self.assertFalse(res.is_error)
        lines = (self.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        ev = [json.loads(line) for line in lines if "hub.plan.created" in line][0]
        self.assertEqual(ev["assist"], "rules")
        self.assertEqual(ev["servers"], ["logs-server"])
        self.assertTrue(ev["env_set"])

    async def test_plan_assistant_failure_gives_fallback(self) -> None:
        self.runtime.plan_assistant = FakePlanAssistant(fail=True)
        args = {"task": "Find error", "servers": ["logs-server"], "steps": ["step 1"]}
        params = CallToolRequestParams(name="hub__plan", arguments=args)
        res = await self.runtime.call_tool(self.ctx, params)
        self.assertFalse(res.is_error)
        lines = (self.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        ev = [json.loads(line) for line in lines if "hub.plan.created" in line][0]
        self.assertEqual(ev["assist"], "fallback")

    async def test_hinted_env_outside_project_is_dropped(self) -> None:
        from mcp_hub.projects import ProjectConfig
        self.runtime.projects = {"myproj": ProjectConfig("myproj", ("dev", "stg"), default_env="dev")}
        hints = PlanHints(servers=("logs-server",), env="prod", source="rules")
        self.runtime.plan_assistant = FakePlanAssistant(hints=hints)
        args = {"task": "Find error", "project": "myproj", "steps": ["step 1"]}
        params = CallToolRequestParams(name="hub__plan", arguments=args)
        res = await self.runtime.call_tool(self.ctx, params)
        self.assertFalse(res.is_error)
        lines = (self.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        ev = [json.loads(line) for line in lines if "hub.plan.created" in line][0]
        self.assertEqual(ev["assist"], "rules")
        self.assertIn("Environment: dev.", res.content[0].text)
        self.assertNotIn("Environment: prod.", res.content[0].text)


if __name__ == "__main__":
    unittest.main()
