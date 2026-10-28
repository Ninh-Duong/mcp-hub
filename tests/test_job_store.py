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


if __name__ == "__main__":
    unittest.main()
