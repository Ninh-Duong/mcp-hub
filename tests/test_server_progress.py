"""A client's progress token reaches the child, and the child's progress comes back under that token; the client's
connection-owned `_meta` keys are not forwarded to the child (FR-010)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mcp.types import CallToolRequestParams, CallToolResult, TextContent, Tool

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.server import HubRuntime
from mcp_hub.settings import PolicySettings, Settings

ROUTE = ToolRoute("feature", "child", "read", "child__read")
TOOL = Tool(name="read", input_schema={"type": "object"}, annotations={"readOnlyHint": True})
CLIENT_INFO_KEY = "io.modelcontextprotocol/clientInfo"


class _ProgressChild:
    """A child that reports the given progress steps when the call carries a progress callback."""

    def __init__(self, steps: list[tuple[float, float | None, str | None]]) -> None:
        self.steps = steps
        self.calls: list[dict[str, Any]] = []

    async def call_tool(
        self, name: str, arguments: dict[str, Any], progress_callback: Any = None, meta: Any = None,
    ) -> CallToolResult:
        self.calls.append({"progress": progress_callback is not None, "meta": meta})
        for step in self.steps if progress_callback is not None else []:
            await progress_callback(*step)
        return CallToolResult(content=[TextContent(type="text", text="ok")])


class _ClientSession:
    """The downstream session: records the progress notifications the Hub sends the client."""

    def __init__(self) -> None:
        self.sent: list[tuple[Any, ...]] = []

    async def send_progress_notification(
        self, progress_token: Any, progress: float, total: float | None = None,
        message: str | None = None, related_request_id: Any = None,
    ) -> None:
        self.sent.append((progress_token, progress, total, message, related_request_id))


class ProgressRelayTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.audit = AuditLog(root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        child = ServerConfig("child", "python", (), root, {}, (), timeout_seconds=60.0)
        settings = Settings(policy=PolicySettings(mode="off"))
        self.runtime = HubRuntime(HubConfig(root / "hub.json", {"child": child}, (ROUTE,)), self.audit, settings)
        self.session = _ClientSession()
        self.ctx = SimpleNamespace(request_id=7, session=self.session)

    def connect(self, child: _ProgressChild) -> None:
        self.runtime.child_connected("child", child, {"read": TOOL})

    def params(self, meta: dict[str, Any] | None) -> CallToolRequestParams:
        raw: dict[str, Any] = {"name": "child__read", "arguments": {}}
        if meta is not None:
            raw["_meta"] = meta
        return CallToolRequestParams.model_validate(raw)

    async def test_child_progress_reaches_the_client_under_its_token(self) -> None:
        child = _ProgressChild([(1.0, 2.0, "half"), (2.0, 2.0, "done")])
        self.connect(child)
        await self.runtime.call_tool(self.ctx, self.params({"progressToken": "tok-1"}))
        self.assertEqual(
            self.session.sent,
            [("tok-1", 1.0, 2.0, "half", 7), ("tok-1", 2.0, 2.0, "done", 7)],
        )
        self.assertTrue(child.calls[0]["progress"])

    async def test_connection_keys_and_the_client_token_are_not_forwarded(self) -> None:
        child = _ProgressChild([])
        self.connect(child)
        meta = {
            "progressToken": "tok-1",
            CLIENT_INFO_KEY: {"name": "client", "version": "1"},
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "team-tag": "kept",
        }
        await self.runtime.call_tool(self.ctx, self.params(meta))
        self.assertEqual(child.calls[0]["meta"], {"team-tag": "kept"})

    async def test_without_a_token_there_is_no_relay(self) -> None:
        child = _ProgressChild([(1.0, None, "ignored")])
        self.connect(child)
        await self.runtime.call_tool(self.ctx, self.params(None))
        self.assertFalse(child.calls[0]["progress"])
        self.assertIsNone(child.calls[0]["meta"])
        self.assertEqual(self.session.sent, [])


if __name__ == "__main__":
    unittest.main()
