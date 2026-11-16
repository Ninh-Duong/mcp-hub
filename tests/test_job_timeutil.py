"""jobs/timeutil.py: UTC helpers the job store uses for timestamps (success and failure paths)."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta, timezone

from mcp_hub.jobs.timeutil import utc_now, utc_timestamp


class UtcNowTest(unittest.TestCase):
    def test_utc_now_is_timezone_aware_utc(self) -> None:
        now = utc_now()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.utcoffset(), timedelta(0))


class UtcTimestampTest(unittest.TestCase):
    def test_an_aware_datetime_is_written_in_utc(self) -> None:
        offset = timezone(timedelta(hours=7))
        value = datetime(2026, 11, 16, 10, 30, tzinfo=offset)
        self.assertEqual(utc_timestamp(value), "2026-11-16T03:30:00+00:00")

    def test_utc_input_keeps_its_value(self) -> None:
        value = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        self.assertEqual(utc_timestamp(value), "2026-01-02T03:04:05+00:00")


if __name__ == "__main__":
    unittest.main()
