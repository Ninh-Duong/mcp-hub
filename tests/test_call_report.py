from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parents[1]


def load_report_script():
    spec = importlib.util.spec_from_file_location("call_report", HUB_ROOT / "scripts" / "call_report.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while the script loads
    spec.loader.exec_module(module)
    return module


BASE = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)


def line(seconds: float, event: str, request_id: str, tool: str, feature: str = "logs") -> str:
    stamp = (BASE + timedelta(seconds=seconds)).isoformat()
    return json.dumps({"timestamp": stamp, "event": event, "request_id": request_id, "feature": feature, "tool": tool})


def call(start: float, length: float, request_id: str, tool: str, feature: str = "logs") -> list[str]:
    return [line(start, "tool.call.started", request_id, tool, feature),
            line(start + length, "tool.call.succeeded", request_id, tool, feature)]


class CallReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.report = load_report_script()
        # Task 1: snapshots -> query, 8 s of thinking between, twice. Then a batch of two reads.
        self.lines = [
            *call(0, 1, "1", "list_snapshots"), *call(9, 1, "2", "query"),
            *call(18, 1, "3", "list_snapshots"), *call(27, 1, "4", "query"),
            *call(36, 2, "5", "hub__batch", "hub"), *call(36, 1, "5.1", "query"), *call(36, 2, "5.2", "read", "jira"),
            # A later task, far away in time.
            *call(1000, 1, "9", "list_snapshots"),
            "not json", json.dumps({"event": "tool.call.started"}),
        ]

    def test_report_measures_tool_time_against_thinking_and_counts_batch_items(self) -> None:
        calls = self.report.read_calls(self.lines)
        self.assertEqual(len(calls), 8)
        self.assertEqual(calls[1].tool, "logs__query")
        tasks = self.report.split_tasks(calls)
        self.assertEqual([len(task) for task in tasks], [5, 1])  # batch items are not separate turns
        self.assertEqual(self.report.turn_gaps(tasks), [8.0, 8.0, 8.0, 8.0])
        text = "\n".join(self.report.report(calls))
        self.assertIn("6 top-level, 2 inside hub__batch", text)
        self.assertIn("median 8.0s over 4 gaps", text)
        self.assertIn("2x  logs__list_snapshots -> logs__query", text)
        self.assertNotIn("1x", text)

    def test_main_reads_a_log_file_and_handles_a_missing_or_empty_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "mcp-hub.jsonl"
            log.write_text("\n".join(self.lines), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(self.report.main(["--log", str(log), "--top", "3"]), 0)
            self.assertIn("Most repeated tool sequences:", output.getvalue())
            with redirect_stdout(io.StringIO()) as missing:
                self.assertEqual(self.report.main(["--log", str(Path(tmp) / "none.jsonl")]), 1)
            self.assertIn("FileNotFoundError", missing.getvalue())
        self.assertEqual(self.report.report([]), ["No completed tool calls in the log."])


if __name__ == "__main__":
    unittest.main()
