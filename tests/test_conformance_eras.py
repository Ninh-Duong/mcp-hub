"""The Hub's server conforms in both protocol eras (FR-021, US7).

An SDK client talks in memory to the server that `create_mcp_server` builds, around two fake children. Each check
runs twice: in the SDK's default mode (`mode="auto"`, which negotiates the 2026-07-28 path for an in-process server)
and in `mode="legacy"` (the 2025 initialize handshake). The fake children are SDK servers too, so each Hub call to a
child crosses a real session. Policy is off here: the policy rules have their own tests.

Accepted limitation (overview, "Accepted as documented limitations"): a 2025 client gets no Hub-side
`tools/list_changed`. The legacy check pins that behaviour; a fix changes that assertion on purpose.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from mcp import Client
from mcp.server import Server
from mcp.server.subscriptions import ToolsListChanged
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent, Tool, ToolListChangedNotification

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.server import HubRuntime, create_mcp_server
from mcp_hub.settings import PolicySettings, ProtocolSettings, Settings

ALPHA = "alpha-server"
BETA = "beta-server"
LOOKUP = ToolRoute("feature", BETA, "lookup", "beta-server__lookup")
EXTRA = ToolRoute("feature", BETA, "extra", "beta-server__extra")
COUNT = ToolRoute("feature", ALPHA, "count", "alpha-server__count")
ECHO = ToolRoute("feature", ALPHA, "echo", "alpha-server__echo")
FAIL = ToolRoute("feature", ALPHA, "fail", "alpha-server__fail")
HUB_TOOLS = ["hub__status", "hub__plan", "hub__batch"]
READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}
COUNT_SCHEMA = {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}
DEBOUNCE_SECONDS = 0.01
WINDOW_SECONDS = 0.2
TIMEOUT_SECONDS = 5.0
INSTRUCTIONS_LIMIT = 4096


def _tool(name: str, output_schema: dict[str, Any] | None = None) -> Tool:
    return Tool(name=name, input_schema={"type": "object"}, output_schema=output_schema, annotations=READ_ONLY)


def _text(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)])


class FakeChild:
    """An SDK server that lists `tools` and answers each call with the canned result for its name."""

    def __init__(self, tools: list[Tool], answers: dict[str, CallToolResult]) -> None:
        self.tools = list(tools)
        self.answers = answers
        self.session: Any = None
        self.server = Server("fake-child", on_list_tools=self._list_tools, on_call_tool=self._call_tool)

    async def _list_tools(self, ctx: Any, params: Any) -> ListToolsResult:
        self.session = ctx.session  # kept so the test can send tools/list_changed later
        return ListToolsResult(tools=list(self.tools))

    async def _call_tool(self, ctx: Any, params: CallToolRequestParams) -> CallToolResult:
        return self.answers[params.name]

    def client(self, message_handler: Any) -> Client:
        """The Hub's side of the session to this child, in the 2025 handshake (what a stdio child uses)."""
        return Client(self.server, mode="legacy", message_handler=message_handler)

    async def announce_change(self, tool: Tool) -> None:
        """Add `tool` to the child's list and send tools/list_changed, as a child that changed itself would."""
        self.tools.append(tool)
        await self.session.send_tool_list_changed()


class HubWithFakeChildren(unittest.IsolatedAsyncioTestCase):
    """The Hub, started with two fake children, served by `create_mcp_server`. Test classes below add the checks."""

    def _make_children(self) -> dict[str, FakeChild]:
        return {
            BETA: FakeChild([_tool("lookup")], {"lookup": _text("found")}),
            ALPHA: FakeChild(
                [_tool("count", COUNT_SCHEMA), _tool("echo"), _tool("fail")],
                {
                    "count": CallToolResult(content=[TextContent(type="text", text='{"count": 3}')],
                                            structured_content={"count": 3}),
                    "echo": _text("hi"),
                    "fail": CallToolResult(content=[TextContent(type="text", text="boom")], is_error=True),
                },
            ),
        }

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.audit_path = root / "audit.jsonl"
        self.audit = AuditLog(self.audit_path)
        self.addCleanup(self.audit.close)
        servers = {
            BETA: ServerConfig(BETA, "python", (), root, {}, (), timeout_seconds=60.0),
            ALPHA: ServerConfig(ALPHA, "python", (), root, {}, (), timeout_seconds=60.0),
        }
        config = HubConfig(root / "hub.json", servers, (LOOKUP, EXTRA, COUNT, ECHO, FAIL))
        settings = Settings(
            policy=PolicySettings(mode="off"),
            protocol=ProtocolSettings(list_changed_debounce_seconds=DEBOUNCE_SECONDS),
        )
        self.runtime = HubRuntime(config, self.audit, settings)
        self.runtime.enable_core_condenser()  # as `serve()` does: condensing and the full-result tool are on
        self.runtime.child_log_dir = root / "children"
        self.children = self._make_children()
        self.runtime.child_opener = self._opener
        self.hub_notifications: list[Any] = []
        self.stack = AsyncExitStack()
        self.addAsyncCleanup(self.stack.aclose)
        self.addCleanup(self.runtime.events.close)
        await self.runtime.connect(self.stack)
        self.server = create_mcp_server(self.runtime)

    def _opener(self, config: ServerConfig, errlog: Any, on_closed: Any, *, message_handler: Any) -> Client:
        return self.children[config.server_id].client(message_handler)

    async def _on_hub_message(self, message: Any) -> None:
        if isinstance(message, ToolListChangedNotification):
            self.hub_notifications.append(message)

    @asynccontextmanager
    async def connected(self) -> AsyncIterator[Client]:
        """An SDK client in the era this class names, connected to the Hub's server in memory."""
        async with Client(self.server, mode=self.MODE, message_handler=self._on_hub_message) as client:
            yield client

    def sent_lines(self) -> int:
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        return sum(json.loads(line)["event"] == "tools.list_changed.sent" for line in lines)


class EraChecks:
    """Checks that hold in both eras. A test class mixes this in with `HubWithFakeChildren` and sets MODE."""

    MODE: str
    EXPECTED_VERSION: str

    async def test_the_negotiated_protocol_version_is_the_era_under_test(self) -> None:
        async with self.connected() as client:
            self.assertEqual(client.protocol_version, self.EXPECTED_VERSION)

    async def test_tools_list_keeps_annotations_and_a_stable_order(self) -> None:
        expected = [*HUB_TOOLS, LOOKUP.exposed_name, COUNT.exposed_name, ECHO.exposed_name, FAIL.exposed_name]
        async with self.connected() as client:
            first = await client.list_tools(cache_mode="bypass")
            second = await client.list_tools(cache_mode="bypass")
        names = [tool.name for tool in first.tools]
        self.assertEqual(names, [*expected, "hub__get_full_result"])
        self.assertEqual([tool.name for tool in second.tools], names)
        count = next(tool for tool in first.tools if tool.name == COUNT.exposed_name)
        self.assertEqual(count.annotations.model_dump(by_alias=True, exclude_none=True), READ_ONLY)
        self.assertEqual(count.output_schema, COUNT_SCHEMA)

    async def test_a_successful_and_a_failed_call_are_both_returned(self) -> None:
        async with self.connected() as client:
            ok = await client.call_tool(ECHO.exposed_name, {"text": "hi"})
            failed = await client.call_tool(FAIL.exposed_name, {})
        self.assertFalse(ok.is_error)
        self.assertEqual(ok.content[0].text, "hi")
        self.assertTrue(failed.is_error)
        self.assertEqual(failed.content[0].text, "boom")

    async def test_a_structured_result_validates_against_its_output_schema(self) -> None:
        async with self.connected() as client:
            result = await client.call_tool(COUNT.exposed_name, {})
        self.assertEqual(result.structured_content, {"count": 3})
        Draft202012Validator(COUNT_SCHEMA).validate(result.structured_content)

    async def test_the_instructions_fit_the_budget_and_lead_with_the_summary(self) -> None:
        async with self.connected() as client:
            instructions = client.instructions
        self.assertIsNotNone(instructions)
        self.assertLessEqual(len(instructions), INSTRUCTIONS_LIMIT)
        self.assertTrue(instructions.startswith("mcp-hub fronts child MCPs."))

    async def test_prompts_list_one_per_child_in_route_order(self) -> None:
        async with self.connected() as client:
            prompts = await client.list_prompts()
        self.assertEqual([prompt.name for prompt in prompts.prompts], [BETA, ALPHA])


class DefaultEraTest(EraChecks, HubWithFakeChildren):
    """The SDK default: a 2026-07-28 client that receives changes through `subscriptions/listen`."""

    MODE = "auto"
    EXPECTED_VERSION = "2026-07-28"

    async def test_a_child_tool_change_reaches_a_listening_client_once(self) -> None:
        async with self.connected() as client:
            async with client.listen(tools_list_changed=True) as subscription:
                await self.children[BETA].announce_change(_tool("extra"))
                event = await asyncio.wait_for(anext(subscription), TIMEOUT_SECONDS)
                self.assertIsInstance(event, ToolsListChanged)
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(anext(subscription), WINDOW_SECONDS)
            names = [tool.name for tool in (await client.list_tools(cache_mode="bypass")).tools]
        self.assertIn(EXTRA.exposed_name, names)
        self.assertEqual(self.sent_lines(), 1)


class LegacyEraTest(EraChecks, HubWithFakeChildren):
    """The 2025 handshake: the Hub sends no list change on this session (the accepted limitation)."""

    MODE = "legacy"
    EXPECTED_VERSION = "2025-11-25"

    async def test_a_child_tool_change_is_published_once_but_not_sent_to_a_2025_client(self) -> None:
        async with self.connected() as client:
            await self.children[BETA].announce_change(_tool("extra"))
            await asyncio.sleep(WINDOW_SECONDS)
            names = [tool.name for tool in (await client.list_tools(cache_mode="bypass")).tools]
        self.assertEqual(self.sent_lines(), 1)
        self.assertEqual(self.hub_notifications, [])
        self.assertIn(EXTRA.exposed_name, names)


if __name__ == "__main__":
    unittest.main()
