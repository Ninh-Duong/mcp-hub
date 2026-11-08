from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from mcp_hub.extensions import PlanHints
from mcp_hub.plan_merge import fold, merge_plan_arguments, parse_window, roles_for_servers


class PlanMergeTest(unittest.TestCase):
    def test_fold_normalizes_case_and_strips_diacritics(self) -> None:
        self.assertEqual(fold("Production"), "production")
        self.assertEqual(fold("H\u00f4m nay"), "hom nay")
        self.assertEqual(fold("\u0111\u00e3 xong"), "da xong")
        self.assertEqual(fold("\u0110\u01a1n h\u00e0ng"), "don hang")

    def test_parse_window_both_none_returns_none(self) -> None:
        self.assertIsNone(parse_window(None, None, 168))

    def test_parse_window_one_sided_raises_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Both 'since' and 'until' are required"):
            parse_window("2026-11-08T00:00:00Z", None, 168)
        with self.assertRaisesRegex(ValueError, "Both 'since' and 'until' are required"):
            parse_window(None, "2026-11-08T00:00:00Z", 168)

    def test_parse_window_valid_iso_strings_with_z(self) -> None:
        s, u = parse_window("2026-11-08T00:00:00Z", "2026-11-08T12:00:00Z", 24)
        self.assertEqual(s, datetime(2026, 11, 8, 0, 0, tzinfo=UTC))
        self.assertEqual(u, datetime(2026, 11, 8, 12, 0, tzinfo=UTC))

    def test_parse_window_since_after_or_equal_until_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "strictly before"):
            parse_window("2026-11-08T12:00:00Z", "2026-11-08T00:00:00Z", 24)
        with self.assertRaisesRegex(ValueError, "strictly before"):
            parse_window("2026-11-08T12:00:00Z", "2026-11-08T12:00:00Z", 24)

    def test_parse_window_span_exceeding_max_hours_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds limit"):
            parse_window("2026-11-01T00:00:00Z", "2026-11-08T12:00:00Z", 24)

    def test_merge_plan_arguments_caller_keys_win(self) -> None:
        now = datetime.now(UTC)
        hints = PlanHints(
            playbook="rcid-bug",
            servers=("mcp-server-logs",),
            env="stg",
            since=now - timedelta(hours=1),
            until=now,
            source="rules",
        )
        caller = {"env": "prod", "task": "test task"}
        merged, used = merge_plan_arguments(caller, hints)
        self.assertTrue(used)
        self.assertEqual(merged["env"], "prod")  # caller wins
        self.assertEqual(merged["playbook"], "rcid-bug")
        self.assertIn("since", merged)
        self.assertIn("until", merged)

    def test_merge_plan_arguments_playbook_blocks_hinted_servers(self) -> None:
        hints = PlanHints(servers=("mcp-server-logs", "mcp-jira-sync"))
        caller = {"playbook": "jira-ticket-bug", "task": "fix issue"}
        merged, used = merge_plan_arguments(caller, hints)
        self.assertFalse(used)
        self.assertNotIn("servers", merged)


    def test_parse_window_datetime_objects_and_naive(self) -> None:
        dt1 = datetime(2026, 11, 8, 1, 0)
        dt2 = datetime(2026, 11, 8, 2, 0, tzinfo=UTC)
        s, u = parse_window(dt1, dt2, 24)
        self.assertEqual(s.tzinfo, UTC)
        self.assertEqual(u.tzinfo, UTC)

    def test_parse_window_invalid_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid date-time format"):
            parse_window("not-a-date", "2026-11-08T12:00:00Z", 24)
        with self.assertRaisesRegex(ValueError, "expected string or datetime"):
            parse_window(12345, "2026-11-08T12:00:00Z", 24)

    def test_merge_plan_arguments_none_hints(self) -> None:
        args = {"task": "do something"}
        merged, used = merge_plan_arguments(args, None)
        self.assertEqual(merged, args)
        self.assertFalse(used)

    def test_roles_for_servers(self) -> None:
        class FakeRoute:
            def __init__(self, server_id: str) -> None:
                self.server_id = server_id

        routes = {
            "logs__query": FakeRoute("logs-server"),
            "jira__read": FakeRoute("jira-server"),
            "unrelated__tool": FakeRoute("other-server"),
        }
        roles = roles_for_servers(["logs-server", "jira-server"], routes)
        self.assertIn("logs", roles)
        self.assertIn("jira", roles)
        self.assertNotIn("source", roles)


if __name__ == "__main__":
    unittest.main()
