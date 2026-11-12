from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mcp.server import Server

from mcp_hub.bitnet import install
from mcp_hub.bitnet.processing import ProcessingFeed
from mcp_hub.bitnet.setup import BitnetSetup
from mcp_hub.dashboard.sessions import DashboardSessions
from mcp_hub.extensions import ProcessingEvent
from mcp_hub.http import create_http_app
from mcp_hub.settings import Settings

STREAM = "/dashboard/api/bitnet/processing/stream"


def payload(message):
    return json.loads(next(line[6:] for line in message.splitlines() if line.startswith("data: ")))


class ProcessingApiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.feed = ProcessingFeed()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.setup = BitnetSetup(Path(self.tmp.name), Settings().bitnet, None, processing_feed=self.feed)

    async def test_snapshot_live_overflow_and_disconnect_cleanup(self) -> None:
        route = next(route for route in self.setup.routes() if route.path == STREAM)
        response = await route.endpoint(None)
        messages = response.body_iterator
        first = await anext(messages)
        self.assertIn("event: snapshot\n", first)
        self.assertEqual(payload(first)["events"], [])
        self.assertEqual(self.feed.subscriber_count, 1)
        self.feed.processing_event(ProcessingEvent("plan", "started", "local"))
        live = await asyncio.wait_for(anext(messages), 1)
        self.assertIn("event: event\n", live)
        self.assertEqual(payload(live)["sequence"], 1)
        for _ in range(10):
            self.feed.processing_event(ProcessingEvent("plan", "succeeded", "rules"))
        resync = await asyncio.wait_for(anext(messages), 1)
        self.assertIn("event: snapshot\n", resync)
        self.assertEqual([e["sequence"] for e in payload(resync)["events"]], [9, 10, 11])
        await messages.aclose()
        self.assertEqual(self.feed.subscriber_count, 0)

    async def test_idle_stream_sends_comment_heartbeat_without_advancing_sequence(self) -> None:
        messages = self.setup._processing_messages()
        await anext(messages)
        try:
            with patch("mcp_hub.bitnet.setup.PROCESSING_HEARTBEAT_SECONDS", 0.01, create=True):
                heartbeat = await asyncio.wait_for(anext(messages), 0.5)
            self.assertEqual(heartbeat, ": keep-alive\n\n")
            self.assertEqual(self.feed.snapshot()["sequence"], 0)
            self.assertEqual(self.feed.snapshot()["events"], [])
            self.feed.processing_event(ProcessingEvent("plan", "started", "local"))
            live = await asyncio.wait_for(anext(messages), 0.5)
            self.assertIn("event: event\n", live)
            self.assertEqual(payload(live)["sequence"], 1)
        finally:
            await messages.aclose()
        self.assertEqual(self.feed.subscriber_count, 0)

    async def request(self, routes, headers):
        sessions = DashboardSessions()
        session = sessions.consume_pairing_token(sessions.issue_pairing_token())
        if headers == "session":
            headers = [(b"cookie", f"hub_session={session}".encode())]
        app = create_http_app(Server("test"), "client-token", custom_routes=routes,
                              sessions=sessions, admin_token="admin-token")
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                 "http_version": "1.1", "method": "GET", "scheme": "http", "path": STREAM,
                 "raw_path": STREAM.encode(), "query_string": b"", "root_path": "",
                 "headers": [(b"host", b"127.0.0.1:8765"), *headers],
                 "client": ("127.0.0.1", 1000), "server": ("127.0.0.1", 8765)}
        sent, finished = [], asyncio.Event()

        async def receive():
            await finished.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body":
                finished.set()

        await asyncio.wait_for(app(scope, receive, send), 2)
        return sent

    async def test_existing_auth_allows_admin_or_paired_session_only(self) -> None:
        for credentials, expected in (([], 401), ([(b"authorization", b"Bearer client-token")], 403),
                                       ([(b"authorization", b"Bearer admin-token")], 200), ("session", 200)):
            with self.subTest(credentials=credentials):
                sent = await self.request(list(self.setup.routes()), credentials)
                self.assertEqual(sent[0]["status"], expected)
                self.assertEqual(self.feed.subscriber_count, 0)

    async def test_off_mode_does_not_install_stream(self) -> None:
        setup = BitnetSetup(Path(self.tmp.name), Settings().bitnet, None)
        self.assertNotIn(STREAM, [route.path for route in setup.routes()])
        sent = await self.request(list(setup.routes()), [(b"authorization", b"Bearer admin-token")])
        self.assertEqual(sent[0]["status"], 404)

    async def test_active_install_wires_same_feed_to_runtime_and_jobs(self) -> None:
        runtime = SimpleNamespace(settings=Settings(), config=SimpleNamespace(routes=()))
        runner = SimpleNamespace()
        with patch("mcp_hub.bitnet.setup._read_install", return_value=None):
            extension = install(runtime, runner, root=Path(self.tmp.name), audit=MagicMock())
        self.assertIs(runtime.processing_observer, runner.processing_observer)
        self.assertIs(runtime.processing_observer, extension.setup.processing_feed)
        self.assertIn(STREAM, [route.path for route in extension.dashboard_extension().routes])


if __name__ == "__main__":
    unittest.main()
