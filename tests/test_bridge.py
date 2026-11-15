from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
import unittest
from unittest.mock import Mock, patch

import anyio
from mcp import Client
from mcp.shared.memory import create_client_server_memory_streams
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from mcp_hub.bridge import bridge_client_info


async def wait_until_started(server, task: asyncio.Task) -> None:
    """Poll a uvicorn server until it accepts connections; stop early if its task already ended."""
    while not server.started:
        if task.done():
            raise RuntimeError("the test HTTP server stopped before it started")
        await asyncio.sleep(0.02)


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


class BridgeProxyModuleTest(unittest.TestCase):
    def test_forwarding_handlers_live_in_bridge_proxy(self) -> None:
        from mcp_hub import bridge, bridge_proxy

        self.assertIs(bridge.create_bridge_server, bridge_proxy.create_bridge_server)
        self.assertEqual(bridge_proxy.create_bridge_server.__module__, "mcp_hub.bridge_proxy")


class FakeRemoteWithInstructions(FakeRemoteClient):
    instructions = "Call sample__echo to echo text."


def run_downstream(remote, check, link=None, message_handler=None, mode="auto") -> None:
    """Serve `remote` through the bridge over memory streams and run `check(client)` as the stdio client."""
    from mcp_hub.bridge_proxy import create_bridge_server

    async def exercise() -> None:
        server = create_bridge_server(remote, link)
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
                    async with Client(transport(), message_handler=message_handler, mode=mode) as client:
                        await check(client)
                finally:
                    task_group.cancel_scope.cancel()

    asyncio.run(exercise())


class BridgeInstructionsTest(unittest.TestCase):
    def test_downstream_client_gets_the_hub_instructions(self) -> None:
        seen = {}

        async def check(client) -> None:
            seen["instructions"] = client.instructions

        run_downstream(FakeRemoteWithInstructions(), check)
        self.assertEqual(seen["instructions"], "Call sample__echo to echo text.")

    def test_downstream_client_gets_the_one_liner_when_the_hub_sent_none(self) -> None:
        seen = {}

        async def check(client) -> None:
            seen["instructions"] = client.instructions

        run_downstream(FakeRemoteClient(), check)
        self.assertEqual(seen["instructions"], "Forwards MCP requests to the authenticated local mcp-hub service.")


class FakeRemoteWithProgress(FakeRemoteClient):
    def __init__(self) -> None:
        self.metas = []

    async def call_tool(self, name: str, arguments: dict[str, str], progress_callback=None, meta=None):
        self.metas.append(meta)
        if progress_callback is not None:
            for step in range(1, 4):
                await progress_callback(float(step), 3.0, f"step {step}")
        return CallToolResult(content=[TextContent(type="text", text=f"{name}:{arguments['text']}")])


class BridgeProgressTest(unittest.TestCase):
    def test_hub_progress_reaches_the_stdio_client_in_order(self) -> None:
        seen = []

        async def check(client) -> None:
            async def on_progress(progress, total, message) -> None:
                seen.append((progress, total, message))

            await client.call_tool("sample__echo", {"text": "x"}, progress_callback=on_progress)

        run_downstream(FakeRemoteWithProgress(), check)
        self.assertEqual(seen, [(1.0, 3.0, "step 1"), (2.0, 3.0, "step 2"), (3.0, 3.0, "step 3")])

    def test_hub_receives_the_request_meta_without_connection_keys(self) -> None:
        remote = FakeRemoteWithProgress()

        async def check(client) -> None:
            async def on_progress(progress, total, message) -> None:
                return None

            await client.call_tool(
                "sample__echo", {"text": "x"}, progress_callback=on_progress, meta={"example.com/trace": "abc"},
            )

        run_downstream(remote, check)
        forwarded = remote.metas[0]
        self.assertEqual(forwarded["example.com/trace"], "abc")
        self.assertNotIn("progress_token", forwarded)
        self.assertNotIn("io.modelcontextprotocol/protocolVersion", forwarded)
        self.assertNotIn("io.modelcontextprotocol/clientInfo", forwarded)
        self.assertNotIn("io.modelcontextprotocol/clientCapabilities", forwarded)


async def tool_events(*events):
    for event in events:
        yield event


class BridgeListChangedTest(unittest.TestCase):
    def test_tools_event_reaches_a_2025_stdio_client_as_one_session_notification(self) -> None:
        from mcp.shared.subscriptions import PromptsListChanged, ToolsListChanged

        from mcp_hub.bridge import relay_tool_changes
        from mcp_hub.bridge_proxy import DownstreamLink

        link = DownstreamLink()
        notifications = []

        async def message_handler(message) -> None:
            if getattr(message, "method", None) == "notifications/tools/list_changed":
                notifications.append("notifications/tools/list_changed")

        async def check(client) -> None:
            await client.list_tools()
            await relay_tool_changes(tool_events(PromptsListChanged(), ToolsListChanged()), link.tools_changed)
            with anyio.fail_after(5):
                while not notifications:
                    await anyio.sleep(0.01)
            await anyio.sleep(0.1)

        run_downstream(FakeRemoteClient(), check, link=link, message_handler=message_handler, mode="legacy")
        self.assertEqual(notifications, ["notifications/tools/list_changed"])

    def test_tools_event_reaches_a_2026_stdio_client_through_subscriptions_listen(self) -> None:
        from mcp.server.subscriptions import ToolsListChanged
        from mcp.shared.subscriptions import PromptsListChanged

        from mcp_hub.bridge import relay_tool_changes
        from mcp_hub.bridge_proxy import DownstreamLink

        link = DownstreamLink()
        received = []

        async def check(client) -> None:
            async with client.listen(tools_list_changed=True) as subscription:
                await relay_tool_changes(tool_events(PromptsListChanged(), ToolsListChanged()), link.tools_changed)
                with anyio.fail_after(5):
                    received.append(await subscription.__anext__())
                with anyio.move_on_after(0.2):
                    received.append(await subscription.__anext__())

        run_downstream(FakeRemoteClient(), check, link=link)
        self.assertEqual(received, [ToolsListChanged()])

    def test_hub_without_listen_support_logs_once_and_presence_stops(self) -> None:
        from mcp.client.subscriptions import ListenNotSupportedError
        from mcp.types import Implementation

        from mcp_hub import bridge

        attempts = []

        async def listen_once(*_args):
            attempts.append(True)
            raise ListenNotSupportedError("2025-11-25")

        with patch.object(bridge, "_listen_once", listen_once), self.assertLogs("mcp_hub.bridge", "WARNING") as logs:
            asyncio.run(asyncio.wait_for(
                bridge.hold_presence("url", "token", Implementation(name="x", version="1"), None), 5,
            ))
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(logs.records), 1)


class BridgeTunablesTest(unittest.TestCase):
    def test_constants_equal_the_bridge_settings_defaults(self) -> None:
        from mcp_hub import bridge
        from mcp_hub.settings import BridgeSettings

        defaults = BridgeSettings()
        self.assertEqual(bridge.PRESENCE_RETRY_SECONDS, defaults.presence_retry_seconds)
        self.assertEqual(bridge.BRIDGE_TIMEOUT.connect, defaults.connect_timeout_seconds)
        self.assertEqual(defaults.presence_retry_seconds, 5.0)
        self.assertEqual(defaults.connect_timeout_seconds, 60.0)
        self.assertIsNone(bridge.BRIDGE_TIMEOUT.read)

    def test_constants_take_their_values_from_bridge_settings(self) -> None:
        import importlib
        from types import SimpleNamespace

        from mcp_hub import bridge

        tuned = SimpleNamespace(presence_retry_seconds=7.5, connect_timeout_seconds=42.0)
        try:
            with patch("mcp_hub.settings.BridgeSettings", return_value=tuned):
                reloaded = importlib.reload(bridge)
                self.assertEqual(reloaded.PRESENCE_RETRY_SECONDS, 7.5)
                self.assertEqual(reloaded.BRIDGE_TIMEOUT.connect, 42.0)
                self.assertIsNone(reloaded.BRIDGE_TIMEOUT.read)
        finally:
            importlib.reload(bridge)


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
                relayed = asyncio.Event()

                async def on_tools_changed() -> None:
                    relayed.set()

                async def announce_until_relayed() -> None:
                    """Announce a tool-set change until the presence stream relays one; the stream may still be opening."""
                    for attempt in range(10):
                        await runtime.announce_tools(f"tools-changed-{attempt}")
                        try:
                            await asyncio.wait_for(relayed.wait(), 1.5)
                        except TimeoutError:
                            continue
                        return
                    raise AssertionError("the presence stream never relayed the tool change")

                presence = None
                try:
                    await asyncio.wait_for(wait_until_started(http_server, task), 10)
                    # D9: the Hub serves subscriptions/listen without the activity UI, so presence waits and relays changes.
                    presence = asyncio.create_task(hold_presence(
                        f"http://127.0.0.1:{port}/mcp", "token", Implementation(name="bridge", version="1"),
                        on_tools_changed,
                    ))
                    await asyncio.wait_for(announce_until_relayed(), 20)
                    self.assertFalse(presence.done())
                finally:
                    if presence is not None:
                        presence.cancel()
                        await asyncio.gather(presence, return_exceptions=True)
                    http_server.should_exit = True
                    await task

            asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
