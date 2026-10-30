from __future__ import annotations

import asyncio
import json
import logging
import socket
import tempfile
import unittest
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx2
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation
from starlette.testclient import TestClient

from mcp_hub.activity_ui import install
from mcp_hub.activity_ui.client_sessions import ClientSessions
from mcp_hub.audit import AuditLog
from mcp_hub.bridge import hold_presence
from mcp_hub.config import HubConfig
from mcp_hub.http import create_http_app
from mcp_hub.server import HubRuntime, create_mcp_server


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


class ClientSessionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.sessions = ClientSessions(idle_timeout=60, clock=self.clock)

    def test_listen_stream_is_live_until_closed(self) -> None:
        stream_id = self.sessions.open_stream("Antigravity")
        self.assertEqual(
            [(entry["client"], entry["live"], entry["connections"]) for entry in self.sessions.snapshot()],
            [("Antigravity", True, 1)],
        )

        self.sessions.close_stream(stream_id)
        self.assertEqual(
            [(entry["client"], entry["live"]) for entry in self.sessions.snapshot()],
            [("Antigravity", False)],
        )
        self.clock.now += timedelta(seconds=61)
        self.assertEqual(self.sessions.snapshot(), [])

    def test_legacy_session_is_live_until_deleted(self) -> None:
        self.sessions.begin("session-a")
        self.sessions.identify("session-a", "Codex")
        self.sessions.end("session-a")
        self.assertTrue(self.sessions.snapshot()[0]["live"])

        self.sessions.close("session-a")
        self.assertEqual(self.sessions.snapshot(), [])

    def test_idle_session_expires_but_in_flight_session_stays(self) -> None:
        self.sessions.identify("idle", "Codex")
        self.sessions.begin("streaming")
        self.sessions.identify("streaming", "Claude Code")
        self.clock.now += timedelta(seconds=61)

        self.assertEqual([entry["client"] for entry in self.sessions.snapshot()], ["Claude Code"])

    def test_stateless_request_marks_client_recently_active(self) -> None:
        self.sessions.seen("claude-code")
        self.clock.now += timedelta(seconds=30)
        snapshot = self.sessions.snapshot()
        self.assertEqual(snapshot, [{
            "client": "claude-code", "live": False, "connections": 0,
            "connected_at": None, "last_seen": "2026-10-30T09:00:00+00:00",
        }])
        self.clock.now += timedelta(seconds=31)
        self.assertEqual(self.sessions.snapshot(), [])

    def test_live_clients_come_first_and_session_ids_stay_private(self) -> None:
        self.sessions.seen("Aider")
        self.sessions.identify("secret-session-1", "Cursor")
        self.clock.now += timedelta(seconds=5)
        self.sessions.identify("secret-session-2", "Cursor")
        self.sessions.open_stream("Cursor")

        snapshot = self.sessions.snapshot()
        self.assertEqual([(entry["client"], entry["connections"]) for entry in snapshot], [("Cursor", 3), ("Aider", 0)])
        self.assertEqual(snapshot[0]["connected_at"], "2026-10-30T09:00:00+00:00")
        self.assertEqual(snapshot[0]["last_seen"], "2026-10-30T09:00:05+00:00")
        self.assertNotIn("secret-session", json.dumps(snapshot))


class ClientPresenceOverHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        logger = logging.getLogger("mcp_hub.audit")
        original_handlers = list(logger.handlers)
        for handler in original_handlers:
            logger.removeHandler(handler)

        def restore_audit_logger() -> None:
            # Release the temporary audit file before the directory is removed.
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)

        self.addCleanup(restore_audit_logger)
        self.token = "test-local-token"
        root = Path(self.tmp.name)
        audit = AuditLog(root / "audit.jsonl")
        self.runtime = HubRuntime(HubConfig(root / "hub.json", {}, ()), audit)
        self.ui = install(self.runtime, store=None, audit=audit)

    def _app(self, port: int = 8765):
        server = create_mcp_server(self.runtime)
        self.ui.attach_server(server)
        return create_http_app(server, self.token, port=port, middleware=self.ui.middleware())

    def test_unauthorized_requests_are_not_counted(self) -> None:
        with TestClient(self._app()) as client:
            response = client.post(
                "/mcp",
                headers={"Host": "127.0.0.1:8765", "Mcp-Session-Id": "forged", "Content-Type": "application/json"},
                content=b"{}",
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.runtime.client_snapshot(), [])

    def test_bridge_presence_is_live_while_open_and_ends_with_the_bridge(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}/mcp"

        def summary() -> list[tuple[str, bool]]:
            return [(entry["client"], entry["live"]) for entry in self.runtime.client_snapshot()]

        async def wait_for(expected: list[tuple[str, bool]]) -> None:
            for _ in range(200):
                if summary() == expected:
                    return
                await asyncio.sleep(0.02)
            self.assertEqual(summary(), expected)

        async def exercise() -> None:
            async with AsyncExitStack() as stack:
                await self.runtime.connect(stack)
            app = self._app(port)
            http_server = uvicorn.Server(uvicorn.Config(
                app, host="127.0.0.1", port=port, log_level="critical", access_log=False,
            ))
            server_task = asyncio.create_task(http_server.serve())
            try:
                for _ in range(200):
                    if http_server.started:
                        break
                    await asyncio.sleep(0.025)
                self.assertTrue(http_server.started)

                # A stateless client without a listen stream is only recently active.
                async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {self.token}"}) as http_client:
                    async with Client(
                        streamable_http_client(url, http_client=http_client),
                        client_info=Implementation(name="claude-code", version="1.0"),
                    ) as client:
                        await client.list_tools()
                self.assertEqual(summary(), [("claude-code", False)])

                presence = asyncio.create_task(hold_presence(
                    url, self.token, Implementation(name="Antigravity", version="1.0"),
                ))
                await wait_for([("Antigravity", True), ("claude-code", False)])

                presence.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await presence
                await wait_for([("Antigravity", False), ("claude-code", False)])

                # Shutdown closes open listen streams instead of waiting for the drain deadline.
                presence = asyncio.create_task(hold_presence(
                    url, self.token, Implementation(name="Antigravity", version="1.0"),
                ))
                await wait_for([("Antigravity", True), ("claude-code", False)])
                self.ui.close_listeners()
                await wait_for([("Antigravity", False), ("claude-code", False)])
                presence.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await presence
            finally:
                http_server.should_exit = True
                await server_task

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
