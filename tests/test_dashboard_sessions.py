from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone


class DashboardSessionsTest(unittest.TestCase):
    def test_pairing_token_is_short_lived_single_use_and_creates_dashboard_session(self) -> None:
        from mcp_hub.dashboard.sessions import DashboardSessions

        now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        sessions = DashboardSessions(clock=lambda: now)
        pairing = sessions.issue_pairing_token()
        self.assertTrue(sessions.is_pairing_valid(pairing))
        cookie = sessions.consume_pairing_token(pairing)
        self.assertIsNotNone(cookie)
        self.assertTrue(sessions.is_session_valid(cookie))
        self.assertFalse(sessions.is_pairing_valid(pairing))
        self.assertIsNone(sessions.consume_pairing_token(pairing))

    def test_expired_pairing_token_is_rejected(self) -> None:
        from mcp_hub.dashboard.sessions import DashboardSessions

        now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        sessions = DashboardSessions(clock=lambda: now)
        pairing = sessions.issue_pairing_token()
        now += timedelta(minutes=3)
        self.assertFalse(sessions.is_pairing_valid(pairing))
        self.assertIsNone(sessions.consume_pairing_token(pairing))


if __name__ == "__main__":
    unittest.main()
