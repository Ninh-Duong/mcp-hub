"""Arguments are checked against the child's input schema in warn mode (FR-012): a mismatch is audited with its field
paths, never its values, and the call still reaches the child. Batch items get the same check."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mcp.types import CallToolRequestParams, CallToolResult, TextContent, Tool

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.server import HubRuntime
from mcp_hub.settings import PolicySettings, Settings, ProtocolSettings

READ = ToolRoute("feature", "child", "read", "child__read")
INPUT_SCHEMA = {
    "type": "object",
    "properties": {"limit": {"type": "integer"}, "filter": {"type": "object", "properties": {"text": {"type": "string"}}}},
}
TOOL = Tool(name="read", input_schema=INPUT_SCHEMA, annotations={"readOnlyHint": True})


class _Child:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any], **kwargs: Any) -> CallToolResult:
        self.calls.append(arguments)
        return CallToolResult(content=[TextContent(type="text", text="ok")])


class ArgumentCheckTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = AuditLog(self.root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        self.child = _Child()
        self.ctx = SimpleNamespace(request_id=3, session=None)

    def runtime(self, mode: str = "warn") -> HubRuntime:
        child = ServerConfig("child", "python", (), self.root, {}, (), timeout_seconds=60.0)
        config = HubConfig(self.root / "hub.json", {"child": child}, (READ,))
        settings = Settings(
            protocol=ProtocolSettings(validate_arguments=mode), policy=PolicySettings(mode="off"),
        )
        runtime = HubRuntime(config, self.audit, settings)
        runtime.child_connected("child", self.child, {"read": TOOL})
        return runtime

    def invalid_events(self) -> list[dict[str, Any]]:
        lines = (self.root / "audit.jsonl").read_text(encoding="utf-8").splitlines() if (self.root / "audit.jsonl").exists() else []
        return [json.loads(line) for line in lines if json.loads(line)["event"] == "tool.arguments.invalid"]

    async def test_a_wrong_type_still_reaches_the_child_and_is_audited_by_field(self) -> None:
        runtime = self.runtime()
        res = await runtime.call_child_tool("child__read", {"limit": "ten"}, request_id="r-1")
        self.assertFalse(res.is_error)
        self.assertEqual(self.child.calls, [{"limit": "ten"}])
        events = self.invalid_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["fields"], ["limit"])
        self.assertEqual(events[0]["server"], "child")
        self.assertEqual(events[0]["tool"], "read")
        self.assertNotIn("ten", json.dumps(events[0]))

    async def test_a_nested_mismatch_is_audited_with_its_dotted_path(self) -> None:
        runtime = self.runtime()
        await runtime.call_child_tool("child__read", {"filter": {"text": 5}}, request_id="r-2")
        self.assertEqual(self.invalid_events()[0]["fields"], ["filter.text"])

    async def test_valid_arguments_write_no_line(self) -> None:
        runtime = self.runtime()
        await runtime.call_child_tool("child__read", {"limit": 10}, request_id="r-3")
        self.assertEqual(self.invalid_events(), [])
        self.assertEqual(self.child.calls, [{"limit": 10}])

    async def test_validation_off_writes_no_line_and_still_calls(self) -> None:
        runtime = self.runtime(mode="off")
        await runtime.call_child_tool("child__read", {"limit": "ten"}, request_id="r-4")
        self.assertEqual(self.invalid_events(), [])
        self.assertEqual(self.child.calls, [{"limit": "ten"}])

    async def test_batch_items_get_the_same_check(self) -> None:
        runtime = self.runtime()
        params = CallToolRequestParams.model_validate({
            "name": "hub__batch",
            "arguments": {"calls": [
                {"tool": "child__read", "arguments": {"limit": "ten"}},
                {"tool": "child__read", "arguments": {"limit": 1}},
            ]},
        })
        await runtime.call_tool(self.ctx, params)
        self.assertEqual(len(self.child.calls), 2)
        self.assertEqual([event["fields"] for event in self.invalid_events()], [["limit"]])


if __name__ == "__main__":
    unittest.main()
