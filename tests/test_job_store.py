from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


class FakeProtector:
    def protect(self, data: bytes) -> bytes:
        return b"enc:" + bytes(byte ^ 0xA5 for byte in data)

    def unprotect(self, data: bytes) -> bytes:
        if not data.startswith(b"enc:"):
            raise ValueError("not protected")
        return bytes(byte ^ 0xA5 for byte in data[4:])


class JobStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        from mcp_hub.jobs.store import JobStore

        self.temp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        self.store = JobStore(
            Path(self.temp.name) / "jobs.sqlite3",
            protector=FakeProtector(),
            clock=lambda: self.now,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_encrypts_request_summary_and_details_and_returns_bounded_pages(self) -> None:
        job_id = self.store.create_job(project="orders", question="Investigate ORD-1", env="prod")
        self.store.transition_job(job_id, "running", event="job.started")
        self.store.save_summary(job_id, "The service timed out.")
        self.store.save_encrypted_result(job_id, "logs-1", {"message": "sample-sensitive-log", "line": 1})
        raw_db = (Path(self.temp.name) / "jobs.sqlite3").read_bytes()

        self.assertNotIn(b"Investigate ORD-1", raw_db)
        self.assertNotIn(b"sample-sensitive-log", raw_db)
        self.assertEqual(self.store.get_job(job_id)["summary"], "The service timed out.")
        page = self.store.get_detail_page(job_id, "logs-1", offset=0, limit=12)
        self.assertEqual(len(page["content"]), 12)
        self.assertGreater(page["next_offset"], 0)

    def test_rejects_invalid_state_transitions_and_marks_running_jobs_interrupted(self) -> None:
        queued = self.store.create_job(project="orders", question="check")
        with self.assertRaises(ValueError):
            self.store.transition_job(queued, "completed", event="job.completed")

        active = self.store.create_job(project="orders", question="check")
        self.store.transition_job(active, "running", event="job.started")
        interrupted = self.store.mark_running_interrupted()
        self.assertEqual(interrupted, 1)
        self.assertEqual(self.store.get_job(active)["state"], "interrupted")

    def test_purges_detail_blobs_after_thirty_days(self) -> None:
        job_id = self.store.create_job(project="orders", question="check")
        self.store.save_encrypted_result(job_id, "source-1", {"value": "kept"})
        self.now += timedelta(days=31)

        self.assertEqual(self.store.purge_expired(), 1)
        with self.assertRaises(KeyError):
            self.store.get_job(job_id)
        with self.assertRaises(KeyError):
            self.store.get_detail_page(job_id, "source-1", offset=0, limit=100)

    def test_activity_calls_persist_safe_metadata_and_can_be_aggregated(self) -> None:
        call_id = self.store.begin_tool_call(
            server_id="mcp-server-logs", tool="query_logs", client="Antigravity", agent_role="logs",
        )
        self.now += timedelta(seconds=2)
        self.store.finish_tool_call(call_id, "succeeded", duration_ms=2000)
        raw_db = (Path(self.temp.name) / "jobs.sqlite3").read_bytes()

        report = self.store.activity_report(
            datetime(2026, 10, 28, tzinfo=timezone.utc),
            datetime(2026, 10, 29, tzinfo=timezone.utc),
        )

        self.assertEqual(report["summary"]["total_calls"], 1)
        self.assertEqual(report["summary"]["total_duration_ms"], 2000)
        self.assertEqual(report["by_child"][0]["server_id"], "mcp-server-logs")
        self.assertEqual(report["by_tool"][0]["tool"], "query_logs")
        self.assertEqual(report["by_client"][0]["client"], "Antigravity")
        self.assertEqual(report["events"][0]["agent_role"], "logs")
        self.assertNotIn(b"arguments", raw_db)
        columns = {
            row["name"] for row in self.store._connection.execute("PRAGMA table_info(tool_calls)").fetchall()
        }
        self.assertFalse({"arguments", "result", "prompt"} & columns)

    def test_activity_pagination_reports_whether_more_rows_exist(self) -> None:
        for tool in ("first_tool", "second_tool"):
            call_id = self.store.begin_tool_call(
                server_id="mcp-server-logs", tool=tool, client="Codex",
            )
            self.store.finish_tool_call(call_id, "succeeded", duration_ms=10)

        start = datetime(2026, 10, 28, tzinfo=timezone.utc)
        end = datetime(2026, 10, 29, tzinfo=timezone.utc)
        first_page = self.store.activity_report(start, end, offset=0, limit=1)
        last_page = self.store.activity_report(start, end, offset=1, limit=1)

        self.assertEqual(len(first_page["events"]), 1)
        self.assertTrue(first_page["has_more"])
        self.assertEqual(len(last_page["events"]), 1)
        self.assertFalse(last_page["has_more"])

    def test_activity_report_rejects_an_unbounded_offset(self) -> None:
        with self.assertRaises(ValueError):
            self.store.activity_report(
                datetime(2026, 10, 28, tzinfo=timezone.utc),
                datetime(2026, 10, 29, tzinfo=timezone.utc),
                offset=2**63,
            )

    def test_running_calls_are_marked_interrupted_and_expire_after_thirty_days(self) -> None:
        self.store.begin_tool_call(
            server_id="mcp-jira-sync", tool="check_connection", client="Codex",
        )
        self.store.mark_tool_calls_interrupted()
        report = self.store.activity_report(
            datetime(2026, 10, 28, tzinfo=timezone.utc),
            datetime(2026, 10, 29, tzinfo=timezone.utc),
        )
        self.assertEqual(report["events"][0]["state"], "interrupted")

        self.now += timedelta(days=31)
        self.store.purge_expired()
        expired = self.store.activity_report(
            datetime(2026, 10, 28, tzinfo=timezone.utc),
            datetime(2026, 10, 29, tzinfo=timezone.utc),
        )
        self.assertEqual(expired["summary"]["total_calls"], 0)


if __name__ == "__main__":
    unittest.main()
