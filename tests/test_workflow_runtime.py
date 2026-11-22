from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from mcp.types import CallToolResult, ImageContent, TextContent

from tests.helpers.workflow import call, create_plan, runtime_fixture
from mcp_hub.planning.workflow_runtime import WORKFLOW_META, workflow_tool
from mcp_hub.planning.policy import PlanBook
from mcp_hub.results.full_results import FullResultStore


class WorkflowRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime, self.client = runtime_fixture(Path(self.tmp.name))
        self.pid = await create_plan(self.runtime)

    async def child(self, name, **arguments):
        return await call(self.runtime, name, {"hub_plan_id": self.pid, **arguments})

    async def operation(self, action="status", **arguments):
        return await self.child("hub__workflow", action=action, **arguments)

    def receipt(self, result):
        self.assertFalse(result.is_error, result.content)
        return result.meta[WORKFLOW_META]["receipt_id"]

    async def listed(self, decision="cache_miss"):
        ref = self.receipt(await self.child("logs__list_ai_context_snapshots", env="dev"))
        result = await self.operation("report", decision=decision, refs=[ref])
        self.assertFalse(result.is_error, result.content)
        return ref

    def log_args(self):
        w = self.runtime.plans.get(self.pid).workflow
        return {"env": "dev", "organization": "org", "stream": "stream", "field": "request_id", "rcid": "request-1",
                "from": w.window[0].isoformat(), "to": w.window[1].isoformat(), "limit": 50}

    async def test_full_cache_only_flow(self):
        self.assertEqual(workflow_tool().name, "hub__workflow")
        await self.listed("cache_hit")
        result = await self.child("logs__read_ai_context", snapshot="cache-1", file="SUMMARY.lhub")
        ref = self.receipt(result)
        reported = await self.operation("report", decision="findings", refs=[ref], needs_source=False)
        self.assertFalse(reported.is_error)
        finished = await self.operation("finish", outcome="supported", refs=[ref])
        self.assertEqual(json.loads(finished.content[0].text)["outcome"], "supported")
        self.assertEqual([name for name, _ in self.client.calls], ["list_ai_context_snapshots", "read_ai_context"])
        status = await self.operation()
        self.assertEqual(json.loads(status.content[0].text)["stage"], "finished")

    async def test_order_scope_and_whole_batch_refusal(self):
        denied = await self.child("logs__find_openobserve_logs_by_rcid", **self.log_args())
        self.assertTrue(denied.is_error)
        batch = await self.child("hub__batch", calls=[
            {"tool": "logs__list_ai_context_snapshots", "arguments": {"env": "dev"}},
            {"tool": "logs__find_openobserve_logs_by_rcid", "arguments": self.log_args()}])
        self.assertTrue(batch.is_error)
        self.assertEqual(self.client.calls, [])
        await self.listed()
        bad_scope = await self.child("logs__find_openobserve_logs_by_rcid", **{**self.log_args(), "rcid": "other"})
        self.assertTrue(bad_scope.is_error)
        result = await self.child("logs__find_openobserve_logs_by_rcid", **self.log_args())
        ref = self.receipt(result)
        self.assertFalse((await self.operation("report", decision="findings", refs=[ref], needs_source=True)).is_error)
        self.assertTrue((await self.child("source__read", project="orders")).is_error)
        search = self.receipt(await self.child("source__search", project="orders", query="failure"))
        self.assertFalse((await self.operation("report", decision="source_found", refs=[search])).is_error)
        read = self.receipt(await self.child("source__read", project="orders", path="app.py"))
        self.assertFalse((await self.operation("report", decision="source_read", refs=[read])).is_error)
        self.assertFalse((await self.operation("finish", outcome="supported", refs=[read])).is_error)

    async def test_failure_retry_and_cross_plan_references(self):
        self.client.failure = True
        result = await self.child("logs__list_ai_context_snapshots", env="dev")
        ref = result.meta[WORKFLOW_META]["receipt_id"]
        self.assertTrue(result.is_error)
        self.assertTrue((await self.operation("report", decision="cache_miss", refs=[ref])).is_error)
        self.assertTrue((await self.operation("report", decision="cache_miss", refs=["fake"])).is_error)
        self.assertTrue((await self.operation("finish", outcome="supported", refs=[])).is_error)
        await self.child("logs__list_ai_context_snapshots", env="dev")
        self.assertTrue((await self.child("logs__list_ai_context_snapshots", env="dev")).is_error)
        self.assertEqual(len(self.client.calls), 2)
        other = await create_plan(self.runtime)
        self.assertTrue((await call(self.runtime, "hub__workflow", {
            "hub_plan_id": other, "action": "report", "decision": "cache_miss", "refs": [ref]})).is_error)
        self.assertFalse((await self.operation("report", decision="access_blocked", refs=[ref])).is_error)
        self.assertFalse((await self.operation("finish", outcome="blocked", refs=[ref])).is_error)

    async def test_busy_and_cancellation_release_lock(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(*args, **kwargs):
            entered.set()
            await asyncio.wait_for(release.wait(), 2)
            return CallToolResult(content=[])

        self.client.call_tool = slow
        task = asyncio.create_task(self.child("logs__list_ai_context_snapshots", env="dev"))
        await asyncio.wait_for(entered.wait(), 2)
        self.assertTrue((await self.operation("finish", outcome="blocked")).is_error)
        self.assertTrue((await self.child("logs__list_ai_context_snapshots", env="dev")).is_error)
        self.assertTrue((await self.child("hub__batch", calls=[])).is_error)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        w = self.runtime.plans.get(self.pid).workflow
        self.assertFalse(w.lock.locked())
        self.assertEqual(next(iter(w.receipts.values())).outcome, "cancelled")

    async def test_batch_metadata_condensation_and_shape(self):
        await self.listed("cache_hit")
        self.runtime.enable_core_condenser()
        self.client.result = CallToolResult(content=[TextContent(type="text", text="x " * 12000)],
                                             **{"_meta": {"child": "kept"}})
        result = await self.child("hub__batch", calls=[
            {"tool": "logs__read_ai_context", "arguments": {"snapshot": "c", "file": "a"}},
            {"tool": "logs__read_ai_context", "arguments": {"snapshot": "c", "file": "b"}}])
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(set(result.meta[WORKFLOW_META]["items"]), {"1", "2"})
        w = self.runtime.plans.get(self.pid).workflow
        refs = [r for r in w.receipts.values() if r.stage == "cache_read"]
        self.assertEqual(len(refs), 2)
        self.assertTrue(all(r.result_id for r in refs))
        self.client.result = CallToolResult(content=[ImageContent(type="image", data="eA==", mime_type="image/png")],
                                             structured_content={"answer": 42})
        shaped = await self.child("logs__read_ai_context", snapshot="c", file="image")
        self.assertEqual(shaped.structured_content, {"answer": 42})
        self.assertIsInstance(shaped.content[0], ImageContent)
        self.assertIn(WORKFLOW_META, shaped.meta)

    async def test_expiry_eviction_and_invalid_input(self):
        self.runtime.full_results = FullResultStore(2, 1, clock=lambda: 2)
        expired = await self.child("hub__get_full_result", result_id="gone")
        self.assertTrue(expired.is_error)
        self.assertIn("do not replay automatically", expired.content[0].text)
        self.assertEqual(self.client.calls, [])
        self.assertTrue((await self.operation("report", decision="wrong")).is_error)
        self.runtime.plans = PlanBook(1)
        self.assertTrue((await self.operation()).is_error)
        self.assertTrue((await self.child("logs__list_ai_context_snapshots", env="dev")).is_error)

    async def test_invalid_pilot_and_non_enforcing_modes(self):
        invalid = await call(self.runtime, "hub__plan", {"task": "request", "playbook": "rcid-bug",
                                                        "project": "orders", "env": "dev"})
        self.assertTrue(invalid.is_error)
        for mode in ("warn", "off"):
            runtime, client = runtime_fixture(Path(self.tmp.name), mode)
            pid = await create_plan(runtime)
            self.assertIsNone(runtime.plans.get(pid).workflow)
            result = await call(runtime, "logs__list_ai_context_snapshots", {"hub_plan_id": pid, "env": "dev"})
            self.assertFalse(result.is_error)
            self.assertEqual(len(client.calls), 1)

    async def test_receipt_limit_rejects_batch_without_execution(self):
        await self.listed("cache_hit")
        self.runtime.plans.get(self.pid).workflow.max_receipts = 2
        result = await self.child("hub__batch", calls=[
            {"tool": "logs__read_ai_context", "arguments": {"snapshot": "c", "file": "a"}},
            {"tool": "logs__read_ai_context", "arguments": {"snapshot": "c", "file": "b"}}])
        self.assertTrue(result.is_error)
        self.assertEqual(len(self.client.calls), 1)
