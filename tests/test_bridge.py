from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import unittest

import anyio
from mcp import Client
from mcp.server import Server
from mcp.shared.memory import create_client_server_memory_streams
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool


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


if __name__ == "__main__":
    unittest.main()
