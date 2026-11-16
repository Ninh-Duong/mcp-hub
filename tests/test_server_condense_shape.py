"""Condensing keeps a result's shape (FR-001, FR-002): a tool with an output schema, structured content or a
non-text block is returned as it was; a condensed text result carries its full-result ID in `_meta`; a result the
store cannot hold is returned uncondensed."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jsonschema import Draft202012Validator
from mcp.types import CallToolResult, ImageContent, TextContent, Tool

from mcp_hub.audit import AuditLog
from mcp_hub.batch import BatchCall, BatchItem
from mcp_hub.config import HubConfig, ToolRoute
from mcp_hub.results.full_results import FullResultStore
from mcp_hub.results.shape import CONDENSED_META_KEY
from mcp_hub.server import HubRuntime
from mcp_hub.settings import Settings

ROUTE = ToolRoute("feature", "logs-server", "query", "logs__query")
OUTPUT_SCHEMA = {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}
LARGE = "log line with information\n" * 400  # 10800 characters: above condense_min_chars and the batch pack limit


class _Condenser:
    """Keeps 5 of 100 lines, whatever the text."""

    async def condense(self, text: str, task: str | None) -> SimpleNamespace:
        return SimpleNamespace(text="condensed summary", kept_lines=5, total_lines=100, source="fake", lossless=False)


class CondenseShapeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = AuditLog(self.root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        self.runtime = HubRuntime(HubConfig(self.root / "hub.json", {}, (ROUTE,)), self.audit, Settings())
        self.runtime.routes["logs__query"] = ROUTE
        self.runtime._read_only.add("logs__query")
        self.runtime.full_results = FullResultStore(max_entries=10, ttl_seconds=60.0)
        self.runtime.condenser = _Condenser()

    def declare(self, output_schema: dict[str, Any] | None) -> None:
        """Give the child's tool the output schema a child would declare (None: no schema)."""
        tool = Tool(
            name="query", input_schema={"type": "object"}, output_schema=output_schema,
            annotations={"readOnlyHint": True},
        )
        self.runtime._discovered["logs-server"] = {"query": tool}

    async def condense(self, result: CallToolResult) -> CallToolResult:
        return await self.runtime._maybe_condense("logs__query", result, "find errors", "req-1")

    def audit_events(self) -> list[dict[str, Any]]:
        path = self.root / "audit.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        return [json.loads(line) for line in lines]

    async def test_output_schema_result_passes_through_and_validates(self) -> None:
        self.declare(OUTPUT_SCHEMA)
        result = CallToolResult(content=[TextContent(type="text", text=LARGE)], structured_content={"count": 3})
        res = await self.condense(result)
        self.assertEqual(res, result)
        Draft202012Validator(OUTPUT_SCHEMA).validate(res.structured_content)

    async def test_structured_content_without_output_schema_passes_through(self) -> None:
        self.declare(None)
        result = CallToolResult(content=[TextContent(type="text", text=LARGE)], structured_content={"count": 3})
        self.assertEqual(await self.condense(result), result)

    async def test_non_text_block_passes_through(self) -> None:
        self.declare(None)
        image = ImageContent(type="image", data="AAAA", mime_type="image/png")
        result = CallToolResult(content=[TextContent(type="text", text=LARGE), image])
        self.assertEqual(await self.condense(result), result)

    async def test_condensed_text_result_carries_its_full_result_id_in_meta(self) -> None:
        self.declare(None)
        res = await self.condense(CallToolResult(content=[TextContent(type="text", text=LARGE)]))
        footer = res.content[0].text
        rid = footer.split('result_id="')[1].split('"')[0]
        self.assertEqual(res.meta[CONDENSED_META_KEY], {"result_id": rid, "kept": 5, "total": 100})
        self.assertIn("[Condensed by mcp-hub: kept 5 of 100 lines.", footer)
        self.assertIsNotNone(self.runtime.full_results.page(rid))

    async def test_result_the_store_cannot_hold_is_returned_uncondensed(self) -> None:
        self.declare(None)
        self.runtime.full_results = FullResultStore(max_entries=10, ttl_seconds=60.0, max_bytes=100)
        result = CallToolResult(content=[TextContent(type="text", text=LARGE)])
        self.assertEqual(await self.condense(result), result)
        self.assertNotIn("tool.result.condensed", [event["event"] for event in self.audit_events()])

    async def test_batch_packing_leaves_an_output_schema_item_unchanged(self) -> None:
        self.declare(OUTPUT_SCHEMA)
        result = CallToolResult(content=[TextContent(type="text", text=LARGE)], structured_content={"count": 3})
        call = BatchCall(index=0, tool="logs__query", arguments={}, server_id="logs-server")
        packed = self.runtime._pack_batch_items([BatchItem(call, result, True, 1)], "find errors", "req-1")
        self.assertEqual(packed[0].result, result)
        Draft202012Validator(OUTPUT_SCHEMA).validate(packed[0].result.structured_content)


if __name__ == "__main__":
    unittest.main()
