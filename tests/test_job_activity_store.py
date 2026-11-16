"""The activity mixin of the job store (jobs/activity_store.py): an empty store reports no calls, and the mixin is part of
JobStore. Tool-call records and reports are covered end to end in tests/test_activity.py."""
from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mcp_hub.jobs.activity_store import MAX_ACTIVITY_OFFSET, ActivityStoreMixin
from mcp_hub.jobs.store import JobStore
from tests.helpers.fakes import FakeProtector


class ActivityStoreMixinTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = JobStore(Path(self.temp.name) / "jobs.sqlite3", protector=FakeProtector())
        self.addCleanup(self.store.close)

    def test_job_store_is_built_from_the_activity_mixin(self) -> None:
        self.assertTrue(issubclass(JobStore, ActivityStoreMixin))

    def test_an_empty_store_reports_no_calls(self) -> None:
        end = datetime.now(UTC)
        report = self.store.activity_report(end - timedelta(hours=1), end)
        self.assertEqual(report["summary"]["total_calls"], 0)

    def test_the_offset_limit_is_the_documented_one(self) -> None:
        self.assertEqual(MAX_ACTIVITY_OFFSET, 1_000_000)


if __name__ == "__main__":
    unittest.main()
