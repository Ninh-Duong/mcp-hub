from __future__ import annotations

import ast
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp.types import CallToolRequestParams, CallToolResult, Implementation, TextContent, Tool
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_hub import server as server_module
from mcp_hub import service
from mcp_hub.activity_ui.extension import ActivityUi, install
from mcp_hub.activity_ui.middleware import ClientSessionMiddleware
from mcp_hub.activity_ui.recorder import ActivityRecorder
from mcp_hub.activity_ui.tracker import ToolCallRecord
from mcp_hub.audit import AuditLog
from mcp_hub.config import HUB_ROOT, HubConfig, ServerConfig, ToolRoute
from mcp_hub.extensions import ACTIVITY_UI, empty_activity, read_launch_extensions, status_flags
from mcp_hub.server import HubRuntime, create_mcp_server

CORE_MODULES = ("server.py", "http.py", "extensions.py", "audit.py", "config.py", "dashboard/api.py", "jobs/store.py")


class _Client:
    def __init__(self, result: CallToolResult | None = None) -> None:
        self.calls = 0
        self.result = result or CallToolResult(content=[TextContent(type="text", text="ok")])

    async def call_tool(self, _name, _arguments):
        self.calls += 1
        return self.result


class _Observer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[tuple] = []

    def call_started(self, server_id, tool_name, client, agent_role):
        if self.fail:
            raise RuntimeError("observer broke with private text")
        self.events.append(("started", server_id, tool_name, client, agent_role))
        return "token"

    def call_finished(self, token, state, error_type, duration_ms):
        self.events.append(("finished", token, state, error_type, duration_ms >= 0))

    def client_seen(self, ctx):
        self.events.append(("seen", ctx.request_id))


class _AuditCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = AuditLog(self.root / "audit.jsonl")
        self.addCleanup(self.audit.close)  # runs first: release the file before the directory is removed
        config = HubConfig(
            self.root / "hub.json",
            {"child": ServerConfig("child", "python", (), self.root, {}, ())},
            (ToolRoute("sample", "child", "inspect", "child__inspect"),),
        )
        self.runtime = HubRuntime(config, self.audit)
        self.client = _Client()
        self.runtime.clients["child"] = self.client
        self.runtime.routes["child__inspect"] = config.routes[0]

    def log(self) -> str:
        return (self.root / "audit.jsonl").read_text(encoding="utf-8")


class CorePathTest(_AuditCase):
    async def test_without_observers_a_call_is_route_audit_child_only(self) -> None:
        self.assertEqual(self.runtime.observers, ())
        with patch.object(server_module, "request_client_name") as client_name:
            ctx = SimpleNamespace(request_id=1, session=None)
            await self.runtime.call_tool(ctx, CallToolRequestParams(name="child__inspect", arguments={}))
        client_name.assert_not_called()
        self.assertEqual(self.client.calls, 1)
        self.assertIn("tool.call.succeeded", self.log())
        self.assertEqual(self.runtime.activity_snapshot(), empty_activity())
        self.assertEqual(self.runtime.client_snapshot(), [])

    async def test_observers_receive_call_and_client_metadata_only(self) -> None:
        observer = _Observer()
        self.runtime.add_observer(observer)
        ctx = SimpleNamespace(request_id=7, session=SimpleNamespace(client_params=SimpleNamespace(
            client_info=Implementation(name="Codex / cli", version="1"),
        )))
        await self.runtime.call_tool(ctx, CallToolRequestParams(name="child__inspect", arguments={"secret": "x"}))
        self.assertEqual(observer.events, [
            ("seen", 7),
            ("started", "child", "inspect", "Codex cli", None),
            ("finished", "token", "succeeded", None, True),
        ])

    async def test_failing_observer_never_breaks_the_call(self) -> None:
        self.runtime.add_observer(_Observer(fail=True))
        result = await self.runtime.call_child_tool("child__inspect", {}, request_id="r1")
        self.assertFalse(result.is_error)
        self.assertIn("extension.observer.failed", self.log())
        self.assertNotIn("private text", self.log())

    async def test_error_summary_is_truncated_before_redaction(self) -> None:
        text = "Error: " + "x" * 50_000
        self.client.result = CallToolResult(content=[TextContent(type="text", text=text)], isError=True)
        seen = []
        original = server_module.redact

        def recording_redact(value, *args, **kwargs):
            seen.append(len(value))
            return original(value, *args, **kwargs)

        with patch.object(server_module, "redact", recording_redact):
            await self.runtime.call_child_tool("child__inspect", {}, request_id="r2")
        self.assertEqual(len(seen), 1)
        self.assertLessEqual(seen[0], server_module.SUMMARY_LIMIT + 256)
        self.assertIn("tool.call.failed", self.log())

    async def test_successful_result_is_not_redacted(self) -> None:
        with patch.object(server_module, "redact") as redact:
            await self.runtime.call_child_tool("child__inspect", {}, request_id="r3")
        redact.assert_not_called()

    def test_job_tool_definitions_are_built_once(self) -> None:
        class Jobs:
            built = 0

            def tool_definitions(self):
                Jobs.built += 1
                return [Tool(name="hub__get_job", inputSchema={"type": "object"})]

        create_mcp_server(self.runtime, Jobs())
        self.assertEqual(Jobs.built, 1)

    def test_core_modules_never_import_the_activity_ui(self) -> None:
        for relative in CORE_MODULES:
            tree = ast.parse((HUB_ROOT / "src" / "mcp_hub" / relative).read_text(encoding="utf-8"))
            names = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            names |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            self.assertFalse([name for name in names if "activity_ui" in name], relative)

    def test_service_imports_the_activity_ui_only_inside_install_extensions(self) -> None:
        tree = ast.parse((HUB_ROOT / "src" / "mcp_hub" / "service.py").read_text(encoding="utf-8"))
        top_level = {node.module for node in tree.body if isinstance(node, ast.ImportFrom)}
        self.assertFalse([name for name in top_level if name and "activity_ui" in name])


class ActivityUiTest(_AuditCase):
    async def test_installed_ui_tracks_events_and_persists_one_row_per_call(self) -> None:
        store = _Store()
        ui = install(self.runtime, store=store, audit=self.audit)
        subscriber = ui.tracker.subscribe()
        await self.runtime.call_child_tool("child__inspect", {"secret": "x"}, client_name="Codex", agent_role="logs")
        self.assertEqual([subscriber.get_nowait()["state"] for _ in range(2)], ["started", "succeeded"])
        self.assertEqual(self.runtime.activity_snapshot()["children"]["child"]["last_state"], "succeeded")
        self.assertEqual(store.batches, [])  # nothing written on the call path
        await ui.close()
        self.assertEqual(len(store.batches), 1)
        record = store.batches[0][0]
        self.assertEqual((record.client, record.agent_role, record.state), ("Codex", "logs", "succeeded"))
        self.assertNotIn("secret", repr(record))

    async def test_status_fields_and_dashboard_extension(self) -> None:
        ui = install(self.runtime, store=None, audit=self.audit)
        extension = ui.dashboard_extension()
        self.assertEqual(extension.name, ACTIVITY_UI)
        self.assertEqual(extension.hubctl_flags, ("-Ui",))
        self.assertEqual({route.path for route in extension.routes},
                         {"/dashboard/api/activity", "/dashboard/api/activity/stream"})
        self.assertEqual(set(extension.status_fields()), {"activity", "clients"})
        self.assertEqual(status_flags((ACTIVITY_UI,)), {"activityUi": True})
        self.assertEqual(status_flags(()), {"activityUi": False})

    async def test_attach_server_serves_presence_listen(self) -> None:
        server = create_mcp_server(self.runtime)
        self.assertIsNone(server.get_request_handler("subscriptions/listen"))
        install(self.runtime, store=None, audit=self.audit).attach_server(server)
        self.assertIsNotNone(server.get_request_handler("subscriptions/listen"))

    def test_middleware_tracks_sessions_inside_the_app(self) -> None:
        ui = ActivityUi(self.runtime, None, self.audit)
        (middleware_class, options), = ui.middleware()
        self.assertIs(middleware_class, ClientSessionMiddleware)
        app = Starlette(routes=[Route("/mcp", lambda _request: PlainTextResponse("ok"), methods=["POST", "DELETE"])])
        app.add_middleware(middleware_class, **options)
        with TestClient(app) as client:
            client.post("/mcp", headers={"Mcp-Session-Id": "session-1"})
            self.assertEqual([entry["connections"] for entry in ui.client_snapshot()], [1])
            client.delete("/mcp", headers={"Mcp-Session-Id": "session-1"})
        self.assertEqual(ui.client_snapshot(), [])


class _Store:
    def __init__(self) -> None:
        self.batches: list[list[ToolCallRecord]] = []
        self.threads: list[bool] = []

    def record_tool_calls(self, records):
        import threading

        self.threads.append(threading.current_thread() is threading.main_thread())
        self.batches.append(list(records))
        return len(records)


def _record(index: int) -> ToolCallRecord:
    return ToolCallRecord("child", f"tool_{index}", "Codex", None, "t0", "t1", 1, "succeeded", None)


class ActivityRecorderTest(_AuditCase):
    async def test_batches_up_to_fifty_records_off_the_event_loop(self) -> None:
        store = _Store()
        recorder = ActivityRecorder(store, self.audit, interval=5.0)
        recorder.start()
        for index in range(120):
            recorder.record(_record(index))
        for _ in range(100):
            if len(store.batches) >= 2:
                break
            await asyncio.sleep(0.01)
        await recorder.close()
        self.assertEqual([len(batch) for batch in store.batches], [50, 50, 20])
        self.assertEqual(store.threads, [False, False, False])

    async def test_partial_batch_is_written_after_the_interval(self) -> None:
        store = _Store()
        recorder = ActivityRecorder(store, self.audit, interval=0.05)
        recorder.start()
        recorder.record(_record(1))
        for _ in range(100):
            if store.batches:
                break
            await asyncio.sleep(0.01)
        self.assertEqual([len(batch) for batch in store.batches], [1])
        await recorder.close()

    async def test_close_flushes_queued_records_and_store_failure_is_audited(self) -> None:
        store = _Store()
        recorder = ActivityRecorder(store, self.audit)
        for index in range(3):
            recorder.record(_record(index))
        await recorder.close()  # never started: close still writes what is queued
        self.assertEqual([len(batch) for batch in store.batches], [3])

        failing = SimpleNamespace(record_tool_calls=lambda _records: (_ for _ in ()).throw(OSError("disk")))
        recorder = ActivityRecorder(failing, self.audit)
        recorder.start()
        recorder.record(_record(1))
        await recorder.close()
        self.assertIn("activity.persist.failed", self.log())

    async def test_full_queue_drops_and_audits_instead_of_blocking(self) -> None:
        recorder = ActivityRecorder(_Store(), self.audit, queue_limit=1)
        recorder.record(_record(1))
        recorder.record(_record(2))
        self.assertIn("activity.persist.dropped", self.log())


class LaunchFileTest(_AuditCase):
    def test_launch_file_is_read_once_and_deleted(self) -> None:
        path = self.root / "launch.json"
        path.write_text('﻿{"extensions": ["activity-ui", 3]}', encoding="utf-8")
        self.assertEqual(read_launch_extensions(path), frozenset({ACTIVITY_UI}))
        self.assertFalse(path.exists())
        self.assertEqual(read_launch_extensions(path), frozenset())

    def test_invalid_launch_file_means_fast_mode_and_is_deleted(self) -> None:
        path = self.root / "launch.json"
        for content in ("not json", "[]", '{"extensions": "activity-ui"}'):
            path.write_text(content, encoding="utf-8")
            self.assertEqual(read_launch_extensions(path, self.audit), frozenset())
            self.assertFalse(path.exists())
        self.assertEqual(self.log().count('"event": "launch.file.invalid"'), 3)

    def test_undecodable_or_unreadable_launch_file_never_stops_the_start(self) -> None:
        path = self.root / "launch.json"
        path.write_bytes(bytes([0xFF, 0xFE, 0x41]))
        self.assertEqual(read_launch_extensions(path, self.audit), frozenset())
        self.assertFalse(path.exists())
        path.mkdir()  # reading a directory raises an OSError, and so does deleting it as a file
        self.assertEqual(read_launch_extensions(path, self.audit), frozenset())
        events = [json.loads(line) for line in self.log().splitlines()]
        error_types = [event["error_type"] for event in events if event["event"] == "launch.file.invalid"]
        self.assertEqual(error_types[0], "UnicodeDecodeError")
        self.assertEqual(len(error_types), 3)

    def test_service_installs_only_requested_extensions(self) -> None:
        self.assertIsNone(service.install_extensions(frozenset(), self.runtime, None, self.audit))
        self.assertEqual(self.runtime.observers, ())
        self.assertIsNone(service.install_extensions(frozenset({"unknown"}), self.runtime, None, self.audit))
        self.assertIn("extension.unknown", self.log())

        ui = service.install_extensions(frozenset({ACTIVITY_UI}), self.runtime, None, self.audit)
        self.assertIsInstance(ui, ActivityUi)
        self.assertEqual(self.runtime.observers, (ui,))
        self.assertIs(self.runtime.activity_source, ui)

    def test_http_app_gets_activity_routes_and_presence_only_with_the_ui(self) -> None:
        jobs = SimpleNamespace(
            tool_definitions=lambda: [], agent_status=lambda: {}, list_projects=lambda: [], store=None,
        )
        fast = service._build_http_app(self.runtime, jobs, "token", "127.0.0.1", 8765, asyncio.Event())
        ui = install(self.runtime, store=None, audit=self.audit)
        full = service._build_http_app(self.runtime, jobs, "token", "127.0.0.1", 8765, asyncio.Event(), ui)
        headers = {"Host": "127.0.0.1:8765", "Authorization": "Bearer token"}
        with TestClient(fast) as client:
            fast_status = client.get("/dashboard/api/status", headers=headers).json()
            fast_activity = client.get("/dashboard/api/activity", headers=headers).status_code
        with TestClient(full) as client:
            full_status = client.get("/dashboard/api/status", headers=headers).json()
            full_activity = client.get("/dashboard/api/activity", headers=headers).status_code
        self.assertEqual((fast_status["extensions"], fast_activity), ({"activityUi": False}, 404))
        self.assertEqual((full_status["extensions"], full_activity), ({"activityUi": True}, 503))
        self.assertIn("clients", full_status)


if __name__ == "__main__":
    unittest.main()
