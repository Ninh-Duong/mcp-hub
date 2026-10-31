"""HubRuntime call paths: allowlist denial, child timeout, and which child values become audit secrets."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from mcp.types import CallToolResult, TextContent, Tool

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute, is_sensitive_key
from mcp_hub.server import HubRuntime

ROUTE = ToolRoute("feature", "child", "read", "child__read")


class SlowClient:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls: list[str] = []

    async def call_tool(self, name: str, arguments: dict) -> CallToolResult:
        self.calls.append(name)
        await asyncio.sleep(self.delay)
        return CallToolResult(content=[TextContent(type="text", text="ok")])


class _RuntimeCase(unittest.IsolatedAsyncioTestCase):
    timeout_seconds = 60.0

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = AuditLog(self.root / "audit.jsonl")
        self.addCleanup(self.audit.close)  # runs first: release the file before the directory is removed
        child = ServerConfig("child", "python", (), self.root, {}, (), timeout_seconds=self.timeout_seconds)
        self.runtime = HubRuntime(HubConfig(self.root / "hub.json", {"child": child}, (ROUTE,)), self.audit)

    def connect(self, client: SlowClient) -> None:
        tool = Tool(name="read", input_schema={"type": "object"}, annotations={"readOnlyHint": True})
        # Outside `connect`, publishing a child rebuilds its routes as after a reconnect.
        self.runtime.child_connected("child", client, {"read": tool})

    def events(self) -> list[dict]:
        lines = (self.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]


class AllowlistDenialTest(_RuntimeCase):
    async def test_a_tool_outside_the_allowlist_is_denied_without_reaching_the_child(self) -> None:
        client = SlowClient()
        self.connect(client)

        result = await self.runtime.call_child_tool("child__write", {"key": "A-1"}, request_id="r-1")

        self.assertTrue(result.is_error)
        self.assertIn("not enabled", result.content[0].text)
        self.assertEqual(client.calls, [])
        denied = [event for event in self.events() if event["event"] == "tool.call.denied"]
        self.assertEqual(denied, [{**denied[0], "request_id": "r-1", "tool": "child__write"}])
        self.assertNotIn("A-1", (self.root / "audit.jsonl").read_text(encoding="utf-8"))


class ChildTimeoutTest(_RuntimeCase):
    timeout_seconds = 0.1

    async def test_a_child_slower_than_its_timeout_fails_with_timeout_error(self) -> None:
        self.connect(SlowClient(delay=5))

        result = await asyncio.wait_for(self.runtime.call_child_tool("child__read", {}, request_id="r-2"), 5)

        self.assertTrue(result.is_error)
        self.assertIn("request_id=r-2", result.content[0].text)
        failed = [event for event in self.events() if event["event"] == "tool.call.failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["error_type"], "TimeoutError")
        self.assertEqual((failed[0]["server"], failed[0]["tool"]), ("child", "read"))


class AuditSecretScopeTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_values_under_sensitive_names_become_audit_secrets(self) -> None:
        root = Path(tempfile.mkdtemp())
        child = ServerConfig(
            "child", "python", (), root,
            env={"JIRA_API_TOKEN": "env-token-value", "PYTHONPATH": "plain-python-path"},
            inherit_env=("PATH", "MCP_HUB_TEST_PASSWORD"),
            secret_values=("nested-secret-value",),
        )
        audit = MagicMock()
        runtime = HubRuntime(HubConfig(root / "hub.json", {"child": child}, ()), audit)
        runtime.child_opener = MagicMock(side_effect=ValueError("not started"))
        runtime.child_log_dir = root / "children"
        env = {"PATH": "plain-search-path", "MCP_HUB_TEST_PASSWORD": "inherited-password"}
        with patch.dict("os.environ", env):
            async with AsyncExitStack() as stack:
                await runtime.connect(stack)
            # The mock keeps the lazy iterables; read them while the environment is still patched.
            added = {value for call in audit.add_secrets.call_args_list for value in call.args[0]} - {""}
        self.assertEqual(added, {"nested-secret-value", "env-token-value", "inherited-password"})

    def test_sensitive_key_names(self) -> None:
        for name in ("API_TOKEN", "jira_password", "OPENAI_API_KEY", "client-secret", "ACCESS_KEY", "email"):
            with self.subTest(name=name):
                self.assertTrue(is_sensitive_key(name))
        for name in ("PATH", "SYSTEMROOT", "USERPROFILE", "TEMP", "PYTHONPATH", "KEYBOARD", "MONKEY_MODE"):
            with self.subTest(name=name):
                self.assertFalse(is_sensitive_key(name))


if __name__ == "__main__":
    unittest.main()
