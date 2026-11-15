"""The Hub answers `subscriptions/listen` from its one event bus, with or without the activity UI. The activity UI
wraps that handler and registers no bus of its own (FR-005, D6, D9)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from mcp_hub.activity_ui import install as install_activity_ui
from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig
from mcp_hub.server import HubRuntime, create_mcp_server
from mcp_hub.settings import Settings

LISTEN = "subscriptions/listen"


class ListenHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.audit = AuditLog(root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        self.runtime = HubRuntime(HubConfig(root / "hub.json", {}, ()), self.audit, Settings())

    def test_the_hub_answers_listen_without_the_activity_ui(self) -> None:
        server = create_mcp_server(self.runtime)
        entry = server.get_request_handler(LISTEN)
        self.assertIsNotNone(entry)
        self.assertIs(entry.handler, self.runtime.events.listen_handler)

    def test_the_activity_ui_wraps_the_hub_handler_and_replaces_only_that_entry(self) -> None:
        server = create_mcp_server(self.runtime)
        install_activity_ui(self.runtime, store=None, audit=self.audit).attach_server(server)
        entry = server.get_request_handler(LISTEN)
        self.assertIsNotNone(entry)
        self.assertIsNot(entry.handler, self.runtime.events.listen_handler)
        self.assertEqual(entry.handler.__name__, "listen")

    async def test_the_wrapper_records_the_stream_and_returns_the_hub_result(self) -> None:
        server = create_mcp_server(self.runtime)
        ui = install_activity_ui(self.runtime, store=None, audit=self.audit)
        ui.client_sessions = MagicMock()
        ui.client_sessions.open_stream.return_value = 1
        hub_listen = AsyncMock(return_value=object())
        self.runtime.events.listen_handler = hub_listen
        ui.attach_server(server)
        ctx, params = SimpleNamespace(), object()
        result = await server.get_request_handler(LISTEN).handler(ctx, params)
        self.assertIs(result, hub_listen.return_value)
        hub_listen.assert_awaited_once_with(ctx, params)
        ui.client_sessions.open_stream.assert_called_once()
        ui.client_sessions.close_stream.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
