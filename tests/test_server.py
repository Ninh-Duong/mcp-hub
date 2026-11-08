from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.types import CallToolRequestParams

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig
from mcp_hub.full_results import FullResultStore
from mcp_hub.server import FULL_RESULT_TOOL, HubRuntime


class ServerFullResultToolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = AuditLog(self.root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        self.config = HubConfig(self.root / "hub.json", {}, ())
        self.runtime = HubRuntime(self.config, self.audit)
        self.ctx = SimpleNamespace(request_id="req-1", session=None)

    async def test_tool_absent_from_list_tools_when_full_results_is_none(self) -> None:
        self.assertIsNone(self.runtime.full_results)
        result = await self.runtime.list_tools(self.ctx, None)
        tool_names = [tool.name for tool in result.tools]
        self.assertNotIn(FULL_RESULT_TOOL, tool_names)

    async def test_tool_present_in_list_tools_when_full_results_is_set(self) -> None:
        self.runtime.full_results = FullResultStore(max_entries=10, ttl_seconds=60.0)
        result = await self.runtime.list_tools(self.ctx, None)
        tool_names = [tool.name for tool in result.tools]
        self.assertIn(FULL_RESULT_TOOL, tool_names)

    async def test_calling_get_full_result_returns_paged_content(self) -> None:
        store = FullResultStore(max_entries=10, ttl_seconds=60.0)
        self.runtime.full_results = store
        rid = store.put("0123456789" * 10)  # 100 chars

        params = CallToolRequestParams(name=FULL_RESULT_TOOL, arguments={"result_id": rid, "offset": 10, "limit": 20})
        res = await self.runtime.call_tool(self.ctx, params)
        self.assertFalse(res.is_error)
        data = json.loads(res.content[0].text)
        self.assertEqual(data["result_id"], rid)
        self.assertEqual(data["offset"], 10)
        self.assertEqual(data["limit"], 20)
        self.assertEqual(data["text"], "01234567890123456789")
        self.assertEqual(data["next_offset"], 30)

        # Audit events check
        lines = (self.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        events = [json.loads(line)["event"] for line in lines]
        self.assertIn("tool.call.started", events)
        self.assertIn("tool.call.succeeded", events)

    async def test_calling_get_full_result_with_unknown_id_returns_error(self) -> None:
        self.runtime.full_results = FullResultStore(max_entries=10, ttl_seconds=60.0)
        params = CallToolRequestParams(name=FULL_RESULT_TOOL, arguments={"result_id": "r-unknown"})
        res = await self.runtime.call_tool(self.ctx, params)
        self.assertTrue(res.is_error)
        self.assertIn("Result expired or unknown", res.content[0].text)


if __name__ == "__main__":
    unittest.main()
