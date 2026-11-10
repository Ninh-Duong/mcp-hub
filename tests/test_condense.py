from __future__ import annotations

import json
import unittest

from mcp_hub.condense import (
    RuleCondenser,
    compact_json_payload,
    format_capped,
    group_lines,
    head_tail_preview,
    template_of,
)


class CoreCondenseTest(unittest.IsolatedAsyncioTestCase):
    def test_single_overlong_line_is_sliced_never_zero_bytes(self) -> None:
        line = "alpha-" + ("x" * 12000) + "-omega"
        out = format_capped([line], max_chars=9000)
        self.assertLessEqual(len(out), 9000)
        self.assertTrue(out.startswith("alpha-"))
        self.assertIn("truncated", out)
        self.assertFalse(out.startswith("[... 1 more lines omitted]"))

    def test_head_tail_preview_preserves_edges_within_budget(self) -> None:
        text = ("START-" * 100) + ("m" * 10000) + ("-END" * 100)
        preview = head_tail_preview(text, max_chars=2000)
        self.assertLessEqual(len(preview), 2000)
        self.assertTrue(preview.startswith("START-"))
        self.assertTrue(preview.endswith("-END"))
        self.assertIn("chars omitted", preview)

    def test_compact_json_lossless_table_keeps_null_columns(self) -> None:
        rows = [
            {"id": i, "name": f"Table_{i}", "schema": "dbo", "emptyCol": None, "extraLongPad": "p" * 250}
            for i in range(30)
        ]
        raw = json.dumps({"success": True, "tables": rows}, separators=(",", ":"))
        self.assertGreater(len(raw), 9000)

        res = compact_json_payload(raw, max_chars=9000)
        self.assertIsNotNone(res)
        compact_text, lossless = res
        self.assertTrue(lossless)
        self.assertLessEqual(len(compact_text), 9000)

        parsed = json.loads(compact_text)
        table = parsed["tables"]
        self.assertEqual(table["_count"], 30)
        self.assertIn("emptyCol", table["_null_cols"])
        self.assertEqual(len(table["_rows"]), 30)

    def test_lossy_json_condense_prioritizes_task_keywords_and_errors(self) -> None:
        rows = [
            {"id": i, "tenant": "999", "payload": "padding-" * 120}
            if i != 42
            else {"id": 42, "tenant": "976", "payload": "AlphaTenant SampleFilter error payload " + ("z" * 400)}
            for i in range(60)
        ]
        raw = json.dumps({"rows": rows}, separators=(",", ":"))
        res = compact_json_payload(raw, max_chars=4500, task="check tenant 976 AlphaTenant")
        self.assertIsNotNone(res)
        compact_text, lossless = res
        self.assertFalse(lossless)
        self.assertLessEqual(len(compact_text), 4500)
        self.assertIn("976", compact_text)
        self.assertIn("AlphaTenant", compact_text)

    async def test_rule_condenser_handles_multiline_and_huge_inputs(self) -> None:
        condenser = RuleCondenser(max_chars=1000)
        raw_lines = [f"2026-11-10T10:00:0{i % 9}Z info item {i}" for i in range(120)]
        raw_lines.append("2026-11-10T10:05:00Z fatal error connecting to upstream")
        res = await condenser.condense("\n".join(raw_lines), "find error")
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "rules")
        self.assertIn("fatal error", res.text)
        self.assertIn("req", template_of("req 12345678"))
        grouped, total = group_lines("\n".join(raw_lines))
        self.assertEqual(total, 121)
        self.assertLess(len(grouped), 121)

    async def test_rule_condenser_offloads_over_100k_text(self) -> None:
        condenser = RuleCondenser(max_chars=4000)
        huge_json = json.dumps({"rows": [{"id": i, "msg": "x" * 200} for i in range(600)]}, separators=(",", ":"))
        self.assertGreater(len(huge_json), 100_000)
        res = await condenser.condense(huge_json, "inspect")
        self.assertIsNotNone(res)
        self.assertFalse(res.lossless)
        self.assertLessEqual(len(res.text), 4000)


if __name__ == "__main__":
    unittest.main()
