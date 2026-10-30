from __future__ import annotations

import asyncio
import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from mcp.types import CallToolRequestParams, CallToolResult, Implementation, TextContent

from mcp_hub.activity_ui import install
from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.jobs.store import JobStore
from mcp_hub.server import HubRuntime


class ActivityRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.logger = logging.getLogger("mcp_hub.audit")
        self.original_handlers = list(self.logger.handlers)
        for handler in self.original_handlers:
            self.logger.removeHandler(handler)
        self.original_propagate = self.logger.propagate
        config = HubConfig(
            self.tmp / "hub.json",
            {"sample-child": ServerConfig("sample-child", "python", (), self.tmp, {}, ())},
            (ToolRoute("sample", "sample-child", "inspect", "sample-child__inspect"),),
        )
        self.store = JobStore(self.tmp / "jobs.sqlite3", protector=_FakeProtector())
        audit = AuditLog(self.tmp / "audit.jsonl")
        self.runtime = HubRuntime(config, audit)
        self.ui = install(self.runtime, store=self.store, audit=audit)
        self.runtime.clients["sample-child"] = _FakeClient()
        self.runtime.routes["sample-child__inspect"] = config.routes[0]
        self.runtime.discovered_tool_counts = {"sample-child": 3}

    def tearDown(self) -> None:
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            handler.close()
        for handler in self.original_handlers:
            self.logger.addHandler(handler)
        self.logger.propagate = self.original_propagate
        self.store.close()
        for path in self.tmp.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(self.tmp.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
        self.tmp.rmdir()

    async def test_success_records_safe_activity_and_tool_count(self) -> None:
        await self.runtime.call_child_tool(
            "sample-child__inspect", {"private_value": "do-not-expose"}, request_id="direct-call",
        )

        snapshot = self.runtime.activity_snapshot()
        child = snapshot["children"]["sample-child"]
        self.assertEqual(child["tool_count"], 3)
        self.assertEqual(child["active_calls"], 0)
        self.assertEqual(child["last_tool"], "inspect")
        self.assertEqual(child["last_state"], "succeeded")
        self.assertEqual([event["state"] for event in snapshot["events"]], ["started", "succeeded"])
        self.assertNotIn("private_value", str(snapshot))
        self.assertNotIn("do-not-expose", str(snapshot))

        await self.ui.close()
        report = self.store.activity_report(
            datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0),
            datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1),
        )
        self.assertEqual(report["summary"]["total_calls"], 1)
        self.assertEqual(report["events"][0]["tool"], "inspect")
        self.assertNotIn("do-not-expose", str(report))

    async def test_activity_subscribers_receive_ordered_call_events_without_arguments(self) -> None:
        subscriber = self.ui.tracker.subscribe()
        await self.runtime.call_child_tool(
            "sample-child__inspect", {"secret": "must-not-stream"}, request_id="stream-test",
            client_name="Codex",
        )

        started = subscriber.get_nowait()
        finished = subscriber.get_nowait()
        self.ui.tracker.unsubscribe(subscriber)

        self.assertEqual([started["state"], finished["state"]], ["started", "succeeded"])
        self.assertEqual(started["call_id"], finished["call_id"])
        self.assertEqual([started["sequence"], finished["sequence"]], [1, 2])
        self.assertEqual(started["client"], "Codex")
        self.assertNotIn("secret", str(started))
        self.assertNotIn("must-not-stream", str(finished))

    async def test_slow_activity_subscriber_is_told_to_resync(self) -> None:
        subscriber = self.ui.tracker.subscribe()
        for call_id in range(257):
            self.ui.tracker.append_event(
                "sample-child", "inspect", "started", call_id=call_id,
            )

        self.assertEqual(subscriber.qsize(), 1)
        self.assertEqual(subscriber.get_nowait(), {"resync": True})
        self.ui.tracker.unsubscribe(subscriber)

    async def test_call_records_client_and_investigation_role(self) -> None:
        await self.runtime.call_child_tool(
            "sample-child__inspect", {"sensitive": "not-stored"}, request_id="job:1",
            client_name="investigation", agent_role="logs",
        )

        start = datetime.now(timezone.utc) - timedelta(minutes=1)
        end = datetime.now(timezone.utc) + timedelta(minutes=1)
        await self.ui.close()
        report = self.store.activity_report(start, end)
        self.assertEqual(report["events"][0]["client"], "investigation")
        self.assertEqual(report["events"][0]["agent_role"], "logs")
        self.assertNotIn("not-stored", str(report))

    async def test_mcp_client_info_is_normalized_and_saved(self) -> None:
        ctx = SimpleNamespace(
            request_id="http-call",
            session=SimpleNamespace(client_params=SimpleNamespace(
                client_info=Implementation(name="Antigravity / local", version="1"),
            )),
        )
        await self.runtime.call_tool(ctx, CallToolRequestParams(name="sample-child__inspect", arguments={}))

        start = datetime.now(timezone.utc) - timedelta(minutes=1)
        end = datetime.now(timezone.utc) + timedelta(minutes=1)
        await self.ui.close()
        report = self.store.activity_report(start, end)
        self.assertEqual(report["events"][0]["client"], "Antigravity local")

    async def test_concurrent_calls_remain_active_until_each_finishes(self) -> None:
        self.runtime.activity_snapshot()
        gate = asyncio.Event()
        entered = asyncio.Event()
        client = _FakeClient(gate=gate, entered=entered)
        self.runtime.clients["sample-child"] = client

        calls = [asyncio.create_task(self.runtime.call_child_tool(
            "sample-child__inspect", {}, request_id=f"call-{index}",
        )) for index in range(2)]
        await entered.wait()
        await asyncio.sleep(0)
        self.assertEqual(self.runtime.activity_snapshot()["children"]["sample-child"]["active_calls"], 2)
        await self.ui.close()
        report = self.store.activity_report(
            datetime.now(timezone.utc) - timedelta(minutes=1),
            datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        # Running calls stay in memory; a row is written once the call finishes.
        self.assertEqual(report["events"], [])

        gate.set()
        await asyncio.gather(*calls)
        self.assertEqual(self.runtime.activity_snapshot()["children"]["sample-child"]["active_calls"], 0)
        await self.ui.close()
        report = self.store.activity_report(
            datetime.now(timezone.utc) - timedelta(minutes=1),
            datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        self.assertEqual(report["summary"]["total_calls"], 2)

    async def test_cancelled_call_is_persisted_as_interrupted(self) -> None:
        entered = asyncio.Event()
        self.runtime.clients["sample-child"] = _FakeClient(
            gate=asyncio.Event(), entered=entered, expected_entries=1,
        )
        call = asyncio.create_task(self.runtime.call_child_tool("sample-child__inspect", {}, request_id="cancel-me"))
        await entered.wait()
        call.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await call

        await self.ui.close()
        report = self.store.activity_report(
            datetime.now(timezone.utc) - timedelta(minutes=1),
            datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        self.assertEqual(report["events"][0]["state"], "interrupted")
        self.assertEqual(self.runtime.activity_snapshot()["children"]["sample-child"]["active_calls"], 0)

    async def test_child_error_exposes_type_but_not_error_text(self) -> None:
        self.runtime.clients["sample-child"] = _FakeClient(error=RuntimeError("private-value-from-child"))
        await self.runtime.call_child_tool("sample-child__inspect", {}, request_id="failed-call")

        child = self.runtime.activity_snapshot()["children"]["sample-child"]
        self.assertEqual(child["last_state"], "failed")
        self.assertEqual(child["last_error_type"], "RuntimeError")
        self.assertNotIn("private-value-from-child", str(self.runtime.activity_snapshot()))
        await self.ui.close()
        report = self.store.activity_report(
            datetime.now(timezone.utc) - timedelta(minutes=1),
            datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        self.assertEqual(report["events"][0]["state"], "failed")
        self.assertEqual(report["events"][0]["error_type"], "RuntimeError")

    async def test_tool_error_result_is_marked_failed_without_copying_message(self) -> None:
        self.runtime.clients["sample-child"] = _FakeClient(result=CallToolResult(
            content=[TextContent(type="text", text="Error: private tool response")], isError=True,
        ))
        await self.runtime.call_child_tool("sample-child__inspect", {}, request_id="tool-error")

        snapshot = self.runtime.activity_snapshot()
        child = snapshot["children"]["sample-child"]
        self.assertEqual(child["last_state"], "failed")
        self.assertEqual(child["last_error_type"], "ToolError")
        self.assertNotIn("private tool response", str(snapshot))


class _FakeClient:
    def __init__(self, *, gate: asyncio.Event | None = None, entered: asyncio.Event | None = None, expected_entries: int = 2, error: Exception | None = None, result: CallToolResult | None = None) -> None:
        self.gate = gate
        self.entered = entered
        self.expected_entries = expected_entries
        self.error = error
        self.result = result
        self.entered_count = 0

    async def call_tool(self, _name, _arguments):
        if self.entered:
            self.entered_count += 1
            if self.entered_count == self.expected_entries:
                self.entered.set()
        if self.gate:
            await self.gate.wait()
        if self.error:
            raise self.error
        if self.result:
            return self.result
        return CallToolResult(content=[TextContent(type="text", text="ok")])


class _FakeProtector:
    def protect(self, data: bytes) -> bytes:
        return data

    def unprotect(self, data: bytes) -> bytes:
        return data


if __name__ == "__main__":
    unittest.main()
