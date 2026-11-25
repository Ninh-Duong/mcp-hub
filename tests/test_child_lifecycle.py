"""Child lifecycle on HubRuntime (child_lifecycle.py): a connected child publishes its session and routes, and a lost
child keeps its routes while its session is withdrawn (moved from server.py)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.types import Tool

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.server import HubRuntime
from mcp_hub.settings import PolicySettings, Settings

ALPHA = "alpha-server"
COUNT = Tool(name="count", inputSchema={"type": "object"}, annotations={"readOnlyHint": True})
ROUTE = ToolRoute("feature", ALPHA, "count", "alpha-server__count")


class ChildLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.audit = AuditLog(root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        child = ServerConfig(ALPHA, "python", (), root, {}, (), timeout_seconds=60.0)
        config = HubConfig(root / "hub.json", {ALPHA: child}, (ROUTE,))
        self.runtime = HubRuntime(config, self.audit, Settings(policy=PolicySettings(mode="off")))
        self.addCleanup(self.runtime.events.close)
        self.session = SimpleNamespace(name="fake-session")

    def test_a_connected_child_publishes_its_session_and_routes(self) -> None:
        # No event loop in a sync test: the config probe and the announcement are skipped, as the code documents.
        self.runtime.child_connected(ALPHA, self.session, {"count": COUNT})
        self.assertIs(self.runtime.clients[ALPHA], self.session)
        self.assertEqual(self.runtime.discovered_tool_counts[ALPHA], 1)
        self.assertEqual(self.runtime.tools_by_server()[ALPHA], ["alpha-server__count"])
        self.assertEqual(self.runtime.child_states()[ALPHA], "connected")

    def test_a_lost_child_drops_its_session_and_keeps_its_routes(self) -> None:
        self.runtime.child_connected(ALPHA, self.session, {"count": COUNT})
        self.runtime.child_lost(ALPHA)
        self.assertNotIn(ALPHA, self.runtime.clients)
        self.assertEqual(self.runtime.tools_by_server()[ALPHA], ["alpha-server__count"])
        self.assertNotEqual(self.runtime.child_states()[ALPHA], "connected")

    def test_a_reconnected_child_is_connected_again(self) -> None:
        self.runtime.child_connected(ALPHA, self.session, {"count": COUNT})
        self.runtime.child_lost(ALPHA)
        replacement = SimpleNamespace(name="new-session")
        self.runtime.child_connected(ALPHA, replacement, {"count": COUNT})
        self.assertIs(self.runtime.clients[ALPHA], replacement)
        self.assertEqual(self.runtime.child_states()[ALPHA], "connected")

    def test_while_the_hub_starts_routes_wait_for_connect(self) -> None:
        # During `connect`, a child's session is published but its routes are registered by connect itself.
        self.runtime._starting = True
        self.runtime.child_connected(ALPHA, self.session, {"count": COUNT})
        self.assertIs(self.runtime.clients[ALPHA], self.session)
        self.assertNotIn(ALPHA, self.runtime.tools_by_server())


class ChildHotToggleTest(unittest.IsolatedAsyncioTestCase):
    async def test_reload_server_stops_disabled_child_and_starts_enabled_child(self) -> None:
        import asyncio
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = AuditLog(root / "audit.jsonl")
            try:
                child = ServerConfig(ALPHA, "python", (), root, {}, (), timeout_seconds=60.0)
                enabled_cfg = HubConfig(root / "hub.json", {ALPHA: child}, (ROUTE,))
                disabled_cfg = HubConfig(root / "hub.json", {}, (), unavailable={ALPHA: "disabled"})
                runtime = HubRuntime(enabled_cfg, audit, Settings(policy=PolicySettings(mode="off")))
                runtime.child_log_dir = root / "children"

                fake_client = SimpleNamespace(
                    list_tools=AsyncMock(return_value=SimpleNamespace(tools=[COUNT], next_cursor=None)),
                )

                @asynccontextmanager
                async def fake_opener(_cfg, _errlog, _on_closed, *, message_handler=None):
                    del message_handler
                    yield fake_client

                runtime.child_opener = fake_opener
                stopped = False

                class _FakeSupervisor:
                    async def stop(self) -> None:
                        nonlocal stopped
                        stopped = True

                runtime.supervisors[ALPHA] = _FakeSupervisor()
                runtime.child_connected(ALPHA, fake_client, {"count": COUNT})
                self.assertIn("alpha-server__count", runtime.routes)

                is_enabled = await runtime.reload_server(ALPHA, config_loader=lambda: disabled_cfg)
                self.assertFalse(is_enabled)
                self.assertTrue(stopped)
                self.assertNotIn(ALPHA, runtime.clients)
                self.assertNotIn("alpha-server__count", runtime.routes)

                is_enabled = await runtime.reload_server(ALPHA, config_loader=lambda: enabled_cfg)
                self.assertTrue(is_enabled)
                await asyncio.sleep(0.05)
                self.assertIn(ALPHA, runtime.clients)
                self.assertIn("alpha-server__count", runtime.routes)
                await runtime.close_children()
                runtime.events.close()
            finally:
                audit.close()


if __name__ == "__main__":
    unittest.main()
