"""`tools/list` has a stable order: Hub tools, then each child in config order with its tools sorted by name, and
carries the cache hint from `protocol.toolsListTtlMs` (FR-008)."""
from __future__ import annotations

from tests.helpers.fakes import plain_tool as _tool
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.types import Tool

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.results.full_results import FullResultStore
from mcp_hub.server import HubRuntime, create_mcp_server
from mcp_hub.settings import ProtocolSettings, Settings

BETA = ToolRoute("feature", "beta-server", "beta", "beta-server__beta")
ALPHA = ToolRoute("feature", "alpha-server", "alpha", "alpha-server__alpha")
ZETA = ToolRoute("feature", "alpha-server", "zeta", "alpha-server__zeta")




class ToolListOrderTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.audit = AuditLog(root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        # beta-server is first in config order, although its name sorts last.
        servers = {
            "beta-server": ServerConfig("beta-server", "python", (), root, {}, (), timeout_seconds=60.0),
            "alpha-server": ServerConfig("alpha-server", "python", (), root, {}, (), timeout_seconds=60.0),
        }
        config = HubConfig(root / "hub.json", servers, (ZETA, BETA, ALPHA))
        self.runtime = HubRuntime(config, self.audit, Settings())
        self.runtime.child_connected("alpha-server", object(), {"zeta": _tool("zeta"), "alpha": _tool("alpha")})
        self.runtime.child_connected("beta-server", object(), {"beta": _tool("beta")})

    def names(self, tools: list[Tool]) -> list[str]:
        return [tool.name for tool in tools]

    async def listed(self) -> list[str]:
        result = await self.runtime.list_tools(SimpleNamespace(request_id=1), None)
        return self.names(result.tools)

    async def test_hub_tools_first_then_children_in_config_order_sorted_by_name(self) -> None:
        self.assertEqual(
            await self.listed(),
            ["hub__status", "hub__plan", "hub__batch", "hub__workflow", "beta-server__beta", "alpha-server__alpha", "alpha-server__zeta"],
        )

    async def test_full_result_tool_stays_after_the_child_tools(self) -> None:
        self.runtime.full_results = FullResultStore(max_entries=10, ttl_seconds=60.0)
        self.assertEqual(
            await self.listed(),
            [
                "hub__status", "hub__plan", "hub__batch", "hub__workflow",
                "beta-server__beta", "alpha-server__alpha", "alpha-server__zeta", "hub__get_full_result",
            ],
        )

    async def test_two_builds_give_the_same_order(self) -> None:
        first = await self.listed()
        self.runtime._register_routes()
        self.assertEqual(await self.listed(), first)

    async def test_tools_list_carries_the_configured_cache_hint(self) -> None:
        settings = Settings(protocol=ProtocolSettings(tools_list_ttl_ms=5000))
        runtime = HubRuntime(self.runtime.config, self.audit, settings)
        server = create_mcp_server(runtime)
        self.assertEqual(server.cache_hints["tools/list"].ttl_ms, 5000)

    async def test_planned_tool_exposure_hides_child_tools_until_plan_activates_them(self) -> None:
        from mcp.types import CallToolRequestParams
        from mcp_hub.settings import PolicySettings

        settings = Settings(
            protocol=ProtocolSettings(tool_exposure="planned", list_changed_debounce_seconds=0.01),
            policy=PolicySettings(mode="enforce", confirm_playbook="off", confirm_execution="off"),
        )
        runtime = HubRuntime(self.runtime.config, self.audit, settings)
        runtime.child_connected("alpha-server", object(), {"zeta": _tool("zeta"), "alpha": _tool("alpha")})
        runtime.child_connected("beta-server", object(), {"beta": _tool("beta")})
        self.addCleanup(runtime.events.close)

        before = [t.name for t in (await runtime.list_tools(SimpleNamespace(request_id=1), None)).tools]
        self.assertEqual(before, ["hub__status", "hub__plan", "hub__batch", "hub__workflow"])

        res = await runtime.call_tool(
            SimpleNamespace(request_id=2, session=None),
            CallToolRequestParams(name="hub__plan", arguments={
                "task": "Use alpha only", "servers": ["alpha-server"], "steps": ["alpha-server__alpha"],
            }),
        )
        self.assertFalse(res.is_error, res.content)
        after = [t.name for t in (await runtime.list_tools(SimpleNamespace(request_id=3), None)).tools]
        self.assertEqual(
            after,
            ["hub__status", "hub__plan", "hub__batch", "hub__workflow", "alpha-server__alpha", "alpha-server__zeta"],
        )


if __name__ == "__main__":
    unittest.main()
