"""The server instructions are built with the instruction budget (FR-020): at most 4096 characters, starting with the
summary, listing the tools per child, and without live connection counts."""
from __future__ import annotations

from tests.helpers.fakes import plain_tool as _tool
import tempfile
import unittest
from pathlib import Path


from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.instructions import INSTRUCTIONS_SUMMARY
from mcp_hub.server import HubRuntime, server_instructions
from mcp_hub.settings import Settings




class InstructionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = AuditLog(self.root / "audit.jsonl")
        self.addCleanup(self.audit.close)

    def runtime(self, tool_count: int = 2) -> HubRuntime:
        child = ServerConfig("child", "python", (), self.root, {}, (), timeout_seconds=60.0)
        routes = tuple(ToolRoute("feature", "child", f"tool{i}", f"child__tool{i}") for i in range(tool_count))
        runtime = HubRuntime(HubConfig(self.root / "hub.json", {"child": child}, routes), self.audit, Settings())
        runtime.child_connected("child", object(), {f"tool{i}": _tool(f"tool{i}") for i in range(tool_count)})
        return runtime

    def test_instructions_start_with_the_summary_and_list_the_tools(self) -> None:
        text = server_instructions(self.runtime(), with_jobs=False)
        self.assertTrue(text.startswith(INSTRUCTIONS_SUMMARY))
        self.assertIn("- child: child__tool0, child__tool1", text)

    def test_instructions_have_no_live_connection_counts(self) -> None:
        runtime = self.runtime()
        before = server_instructions(runtime, with_jobs=True)
        runtime.clients["child"] = object()
        after = server_instructions(runtime, with_jobs=True)
        self.assertEqual(before, after)
        self.assertNotIn("connected children", after)
        self.assertNotIn("Detected siblings", after)

    def test_instructions_stay_within_the_budget_with_many_tools(self) -> None:
        text = server_instructions(self.runtime(tool_count=400), with_jobs=True)
        self.assertLessEqual(len(text), 4096)
        self.assertTrue(text.startswith(INSTRUCTIONS_SUMMARY))

    def test_a_cut_rules_section_names_client_rules_not_the_tool_list(self) -> None:
        text = server_instructions(self.runtime(), with_jobs=True)
        self.assertRegex(text, r"\+\d+ more lines in docs/client-rules\.md$")
        self.assertNotIn("see tools/list", text)


if __name__ == "__main__":
    unittest.main()
