from __future__ import annotations

import dataclasses
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.types import CallToolResult, TextContent

from mcp_hub.extensions import ProcessingEvent
from mcp_hub.bitnet.processing import ProcessingFeed
from mcp_hub.batch import BatchCall, BatchItem
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.extensions import Condensed, PlanHints
from mcp_hub.extensions import processing_step
from mcp_hub.jobs.runner import JobRunner
from mcp_hub.server import HubRuntime


class ProcessingFeedTest(unittest.TestCase):
    def test_contract_rejects_content_and_unknown_codes(self) -> None:
        for field in ("operation", "state", "source", "error_code"):
            args = dict(operation="plan", state="started", source="local")
            args[field] = "secret-content"
            with self.subTest(field=field), self.assertRaises(ValueError):
                ProcessingEvent(**args)
        with self.assertRaises(TypeError):
            ProcessingEvent("plan", "started", "local", message="secret-content")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ProcessingEvent("plan", "started", "local").state = "failed"

    def test_projection_allows_only_configured_metadata_and_bounds_duration(self) -> None:
        feed = ProcessingFeed({("mcp-test", "read")})
        feed.processing_event(ProcessingEvent("child_call", "succeeded", "child",
                                             server_id="secret-content", tool_name="secret-content",
                                             duration_ms=10**20))
        event = feed.snapshot()["events"][0]
        self.assertNotIn("secret-content", json.dumps(event))
        self.assertEqual(event["duration_ms"], 3_600_000)
        feed.processing_event(ProcessingEvent("child_call", "started", "child",
                                             server_id="mcp-test", tool_name="read"))
        self.assertEqual(feed.snapshot()["events"][-1]["tool_name"], "read")

    def test_event_metadata_is_bounded_before_observation(self) -> None:
        event = ProcessingEvent("child_call", "succeeded", "child", server_id="x" * 129,
                                tool_name="https://private.invalid/task", duration_ms=10**20)
        self.assertIsNone(event.server_id)
        self.assertIsNone(event.tool_name)
        self.assertEqual(event.duration_ms, 3_600_000)

    def test_history_is_three_ordered_events_with_a_process_stream_id(self) -> None:
        feed = ProcessingFeed()
        for _ in range(7):
            feed.processing_event(ProcessingEvent("plan", "started", "local"))
        snapshot = feed.snapshot()
        self.assertEqual(snapshot["sequence"], 7)
        self.assertEqual([e["sequence"] for e in snapshot["events"]], [5, 6, 7])
        self.assertTrue(all(e["stream_id"] == snapshot["stream_id"] for e in snapshot["events"]))
        self.assertNotEqual(ProcessingFeed().snapshot()["stream_id"], snapshot["stream_id"])

    def test_subscription_overflow_requests_latest_snapshot_and_cleanup(self) -> None:
        feed = ProcessingFeed()
        queue = feed.subscribe()
        for _ in range(100):
            feed.processing_event(ProcessingEvent("plan", "started", "local"))
        self.assertLessEqual(queue.qsize(), queue.maxsize)
        self.assertEqual(queue.get_nowait(), {"resync": True})
        self.assertEqual(feed.snapshot()["events"][-1]["sequence"], 100)
        feed.unsubscribe(queue)
        self.assertEqual(feed.subscriber_count, 0)


class ProcessingProducerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        route = ToolRoute("test", "child", "read", "test__read", cache_seconds=10)
        config = HubConfig(Path("unused"), {"child": ServerConfig("child", "python", (), Path("."), {}, ())}, (route,))
        self.runtime = HubRuntime(config, MagicMock(), playbooks={})
        self.runtime.routes[route.exposed_name] = route
        self.runtime._read_only.add(route.exposed_name)
        self.client = SimpleNamespace(call_tool=AsyncMock(return_value=CallToolResult(
            content=[TextContent(type="text", text="secret-content")],
        )))
        self.runtime.clients["child"] = self.client
        self.events = []
        self.observer = SimpleNamespace(processing_event=self.events.append)
        self.runtime.processing_observer = self.observer

    def codes(self):
        return [(e.operation, e.state, e.source) for e in self.events]

    async def test_direct_planning_reports_rules_model_and_fallback_without_content(self) -> None:
        for result, source, state in ((PlanHints(source="rules"), "rules", "succeeded"),
                                      (PlanHints(source="bitnet"), "bitnet", "succeeded"),
                                      (None, "fallback", "fallback")):
            self.events.clear()
            self.runtime.plan_assistant = SimpleNamespace(hints=AsyncMock(return_value=result))
            await self.runtime._assist_plan({"task": "secret-content"})
            self.assertEqual(self.codes(), [("plan", "started", "local"), ("plan", state, source)])
            self.assertNotIn("secret-content", repr(self.events))

    async def test_child_calls_report_success_cache_and_failure_without_arguments(self) -> None:
        await self.runtime.call_child_tool("test__read", {"question": "secret-content"}, client_name="private-client")
        await self.runtime.call_child_tool("test__read", {"question": "secret-content"})
        self.client.call_tool.side_effect = RuntimeError("secret-content")
        await self.runtime.call_child_tool("test__read", {"different": True})
        self.assertEqual(self.codes(), [
            ("child_call", "started", "child"), ("child_call", "succeeded", "child"),
            ("child_call", "started", "child"), ("child_call", "succeeded", "cache"),
            ("child_call", "started", "child"), ("child_call", "failed", "child"),
        ])
        self.assertEqual(self.events[-1].error_code, "unexpected")
        self.assertNotIn("secret-content", repr(self.events))
        self.assertNotIn("private-client", repr(self.events))

    async def test_direct_condense_reports_actual_source_and_fallback(self) -> None:
        self.runtime.enable_core_condenser()
        result = CallToolResult(content=[TextContent(type="text", text="secret-content" * 1000)])
        for outcome, source, state in ((Condensed("secret-output", 1, 50, "rules"), "rules", "succeeded"),
                                       (Condensed("secret-output", 1, 50, "bitnet"), "bitnet", "succeeded"),
                                       (None, "fallback", "fallback")):
            self.events.clear()
            self.runtime.condenser = SimpleNamespace(condense=AsyncMock(return_value=outcome))
            await self.runtime._maybe_condense("test__read", result, "secret-task", "unused")
            self.assertEqual(self.codes(), [("condense", "started", "local"), ("condense", state, source)])
            self.assertNotIn("secret", repr(self.events))

    async def test_batch_rule_packing_reports_success_or_failure(self) -> None:
        self.runtime.enable_core_condenser()
        result = CallToolResult(content=[TextContent(type="text", text="private-content" * 20_000)])
        items = [BatchItem(BatchCall(1, "test__read", {}, "child"), result, True, 0)]
        self.runtime._pack_batch_items(items, "private-task", "private-request")
        self.assertEqual(self.codes(), [("condense", "started", "rules"), ("condense", "succeeded", "rules")])
        with patch("mcp_hub.server.condense_text_rules", side_effect=RuntimeError("private-error")):
            with self.assertRaises(RuntimeError):
                self.runtime._pack_batch_items(items, "private-task", "private-request")
        self.assertEqual(self.events[-1].state, "failed")
        self.assertNotIn("private", repr(self.events))

    async def test_job_planning_and_local_summary_report_outcomes(self) -> None:
        store = MagicMock(clock=lambda: datetime.now(UTC))
        runner = JobRunner(store=store, runtime=self.runtime, projects={}, agents={},
                           processing_observer=self.observer,
                           plan_assistant=SimpleNamespace(hints=AsyncMock(return_value=PlanHints(source="rules"))),
                           summarizer=SimpleNamespace(summarize=AsyncMock(return_value="secret-output")))
        request = {"project": "test", "question": "secret-question", "env": "dev"}
        await runner._plan_job("secret-job", request)
        await runner._summarize(request, [], False)
        self.assertEqual(self.codes(), [("job_plan", "started", "local"), ("job_plan", "succeeded", "rules"),
                                       ("local_summary", "started", "bitnet"), ("local_summary", "succeeded", "bitnet")])
        self.assertNotIn("secret", repr(self.events))

    async def test_observer_failure_and_disabled_mode_preserve_results(self) -> None:
        self.assertIsNone(HubRuntime(self.runtime.config, MagicMock()).processing_observer)
        self.runtime.processing_observer = SimpleNamespace(processing_event=MagicMock(side_effect=RuntimeError))
        result = await self.runtime.call_child_tool("test__read", {})
        self.assertFalse(result.is_error)

    async def test_stage_failures_keep_exceptions_and_use_only_fixed_error_codes(self) -> None:
        import asyncio

        for error, code in ((TimeoutError("secret"), "timeout"), (RuntimeError("secret"), "unexpected"),
                            (asyncio.CancelledError("secret"), "cancelled")):
            with self.subTest(code=code), self.assertRaises(type(error)):
                await processing_step(self.observer, "plan", AsyncMock(side_effect=error)())
            self.assertEqual(self.events[-1].error_code, code)
            self.assertEqual(self.events[-1].state, "failed")
        self.assertNotIn("secret", repr(self.events))

    async def test_job_calls_share_runtime_events_and_specialist_condensation_is_observed(self) -> None:
        runner = JobRunner(store=MagicMock(), runtime=self.runtime, projects={}, agents={},
                           processing_observer=self.observer,
                           condenser=SimpleNamespace(condense=AsyncMock(return_value=Condensed("private", 1, 2, "rules"))))
        await runner._invoke_child("private-job", "logs", "test__read", {"private": True}, "private-source")
        await runner._observed_condenser().condense("private-results", "private-question")
        self.assertEqual(self.codes(), [("child_call", "started", "child"), ("child_call", "succeeded", "child"),
                                       ("condense", "started", "local"), ("condense", "succeeded", "rules")])
        self.assertNotIn("private", repr(self.events))

    async def test_denied_calls_emit_nothing_and_child_failures_have_fixed_categories(self) -> None:
        import asyncio

        await self.runtime.call_child_tool("private-denied-tool", {})
        self.assertEqual(self.events, [])
        self.runtime.clients.clear()
        await self.runtime.call_child_tool("test__read", {})
        self.assertEqual(self.events[-1].error_code, "unavailable")
        self.runtime.clients["child"] = self.client
        for error, code in ((TimeoutError("private-error"), "timeout"),
                            (asyncio.CancelledError("private-error"), "cancelled")):
            self.client.call_tool.side_effect = error
            if code == "cancelled":
                with self.assertRaises(asyncio.CancelledError):
                    await self.runtime.call_child_tool("test__read", {"case": code})
            else:
                await self.runtime.call_child_tool("test__read", {"case": code})
            self.assertEqual(self.events[-1].error_code, code)
        self.client.call_tool.side_effect = None
        self.client.call_tool.return_value = CallToolResult(is_error=True, content=[TextContent(type="text", text="private")])
        await self.runtime.call_child_tool("test__read", {"case": "error"})
        self.assertEqual(self.events[-1].error_code, "tool_error")
        self.assertNotIn("private", repr(self.events))


if __name__ == "__main__":
    unittest.main()

