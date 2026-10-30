from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
import unittest
from unittest.mock import Mock, patch

import anyio
from mcp import Client
from mcp.server import Server
from mcp.shared.memory import create_client_server_memory_streams
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from mcp_hub.bridge import bridge_client_info


class FakeRemoteClient:
    async def list_tools(self, *, cursor: str | None = None):
        return ListToolsResult(tools=[Tool(
            name="sample__echo",
            description="[sample via child] Echo.",
            inputSchema={"type": "object", "properties": {"text": {"type": "string"}}},
            annotations={"readOnlyHint": True},
        )])

    async def call_tool(self, name: str, arguments: dict[str, str]):
        return CallToolResult(content=[TextContent(type="text", text=f"{name}:{arguments['text']}")])


class StdioBridgeTest(unittest.TestCase):
    def test_stdio_bridge_forwards_tool_list_and_calls(self) -> None:
        from mcp_hub.bridge import create_bridge_server

        async def exercise() -> None:
            server = create_bridge_server(FakeRemoteClient())
            async with create_client_server_memory_streams() as (client_streams, server_streams):
                client_read, client_write = client_streams
                server_read, server_write = server_streams

                @asynccontextmanager
                async def transport():
                    yield client_read, client_write

                async with anyio.create_task_group() as task_group:
                    task_group.start_soon(
                        server.run,
                        server_read,
                        server_write,
                        server.create_initialization_options(),
                    )
                    try:
                        async with Client(transport()) as client:
                            tools = await client.list_tools()
                            self.assertEqual([tool.name for tool in tools.tools], ["sample__echo"])
                            self.assertTrue(tools.tools[0].annotations.read_only_hint)
                            result = await client.call_tool("sample__echo", {"text": "hello"})
                            self.assertEqual(result.content[0].text, "sample__echo:hello")
                    finally:
                        task_group.cancel_scope.cancel()

        asyncio.run(exercise())

    def test_bridge_passes_the_configured_client_label(self) -> None:
        with patch.dict(os.environ, {"MCP_HUB_CLIENT_NAME": "Antigravity"}):
            info = bridge_client_info()
        self.assertEqual(info.name, "Antigravity")

    def test_bridge_uses_unknown_for_empty_or_unset_labels(self) -> None:
        with patch.dict(os.environ, {"MCP_HUB_CLIENT_NAME": "  /  "}):
            self.assertEqual(bridge_client_info().name, "unknown")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(bridge_client_info().name, "unknown")


class PresenceTest(unittest.TestCase):
    def test_main_client_has_no_read_timeout_so_long_child_calls_finish(self) -> None:
        from mcp_hub.bridge import BRIDGE_TIMEOUT

        self.assertIsNone(BRIDGE_TIMEOUT.read)
        self.assertEqual(BRIDGE_TIMEOUT.connect, 60.0)

    def test_presence_retries_connection_errors_and_stops_on_method_not_found(self) -> None:
        from mcp.shared.exceptions import MCPError
        from mcp.types import METHOD_NOT_FOUND, Implementation

        from mcp_hub import bridge

        outcomes = [ConnectionError("hub restarting"), ExceptionGroup("wrapped", [MCPError(METHOD_NOT_FOUND, "nope")])]
        attempts = []

        async def listen_once(*_args):
            attempts.append(True)
            raise outcomes.pop(0)

        async def no_sleep(_seconds):
            return None

        with patch.object(bridge, "_listen_once", listen_once), patch.object(bridge.asyncio, "sleep", no_sleep):
            asyncio.run(asyncio.wait_for(bridge.hold_presence("url", "token", Implementation(name="x", version="1")), 5))
        self.assertEqual(len(attempts), 2)

    def test_presence_returns_against_a_hub_without_the_activity_ui(self) -> None:
        import socket
        import tempfile
        from pathlib import Path

        import uvicorn
        from mcp.types import Implementation

        from mcp_hub.audit import AuditLog
        from mcp_hub.bridge import hold_presence
        from mcp_hub.config import HubConfig
        from mcp_hub.http import create_http_app
        from mcp_hub.server import HubRuntime, create_mcp_server

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        audit = Mock(spec=AuditLog)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = HubRuntime(HubConfig(Path(tmp) / "hub.json", {}, ()), audit)
            app = create_http_app(create_mcp_server(runtime), "token", port=port)

            async def exercise() -> None:
                http_server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
                task = asyncio.create_task(http_server.serve())
                try:
                    while not http_server.started:
                        await asyncio.sleep(0.02)
                    await asyncio.wait_for(hold_presence(
                        f"http://127.0.0.1:{port}/mcp", "token", Implementation(name="bridge", version="1"),
                    ), 10)
                finally:
                    http_server.should_exit = True
                    await task

            asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
