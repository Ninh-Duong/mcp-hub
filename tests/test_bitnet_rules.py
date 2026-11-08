from __future__ import annotations

import unittest
from datetime import datetime, timezone

from mcp_hub.bitnet.rules import fold, parse_rules
from mcp_hub.extensions import PlanContext


class BitnetRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
        self.playbooks = {
            "rcid-bug": "when you have a request ID",
            "jira-ticket-bug": "when you have a Jira ticket key",
            "pr-review": "when reviewing a pull request",
        }
        self.servers = (
            "mcp-server-logs",
            "mcp-sqlserver",
            "mcp-jira-sync",
            "mcp-bitbucket-pr",
            "mcp-source",
        )
        self.envs = ("dev", "stg", "prod")

    def _context(self, task: str, envs: tuple[str, ...] | None = None) -> PlanContext:
        return PlanContext(
            task=task,
            playbooks=self.playbooks,
            servers=self.servers,
            envs=envs if envs is not None else self.envs,
            now=self.now,
        )

    def test_fold_normalizes_text(self) -> None:
        self.assertEqual(fold("Production"), "production")
        self.assertEqual(fold("H\u00f4m nay"), "hom nay")
        self.assertEqual(fold("\u0111\u00e3 xong"), "da xong")

    def test_check_production_hom_nay(self) -> None:
        res = parse_rules(self._context("check production hom nay"))
        self.assertEqual(res.hints.env, "prod")
        self.assertEqual(res.hints.since, datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(res.hints.until, datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc))
        self.assertNotIn("env", res.unresolved)
        self.assertNotIn("window", res.unresolved)

    def test_loi_sql_orders_api_dev_hom_qua(self) -> None:
        res = parse_rules(self._context("loi sql orders-api dev hom qua"))
        self.assertEqual(res.hints.env, "dev")
        self.assertIn("mcp-server-logs", res.hints.servers)
        self.assertIn("mcp-sqlserver", res.hints.servers)
        # Yesterday UTC: 2026-11-07 00:00:00 to 2026-11-08 00:00:00
        self.assertEqual(res.hints.since, datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(res.hints.until, datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc))

    def test_compare_dev_and_prod_leaves_env_unresolved(self) -> None:
        res = parse_rules(self._context("compare dev and prod"))
        self.assertIsNone(res.hints.env)
        self.assertIn("env", res.unresolved)

    def test_last_6h_errors(self) -> None:
        res = parse_rules(self._context("last 6h errors"))
        self.assertEqual(res.hints.since, datetime(2026, 11, 8, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(res.hints.until, datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc))
        self.assertNotIn("window", res.unresolved)
        self.assertIn("mcp-server-logs", res.hints.servers)

    def test_last_30_days_window_unresolved(self) -> None:
        res = parse_rules(self._context("last 30 days"))
        self.assertIsNone(res.hints.since)
        self.assertIsNone(res.hints.until)
        self.assertIn("window", res.unresolved)

    def test_jira_key_resolves_playbook(self) -> None:
        res = parse_rules(self._context("ABC-123 crash"))
        self.assertEqual(res.hints.playbook, "jira-ticket-bug")
        self.assertNotIn("playbook", res.unresolved)

    def test_rcid_and_pr_playbook_resolutions(self) -> None:
        res_rcid = parse_rules(self._context("check rcid: 12345678-1234-1234-1234-123456789abc"))
        self.assertEqual(res_rcid.hints.playbook, "rcid-bug")

        res_pr = parse_rules(self._context("check /pull-requests/456"))
        self.assertEqual(res_pr.hints.playbook, "pr-review")

    def test_hello_leaves_everything_unresolved(self) -> None:
        res = parse_rules(self._context("hello"))
        self.assertEqual(res.unresolved, frozenset({"playbook", "servers", "env", "window"}))

    def test_accented_input_matches_same_as_unaccented(self) -> None:
        unaccented = parse_rules(self._context("check production hom nay"))
        accented = parse_rules(self._context("check production h\u00f4m nay"))
        self.assertEqual(unaccented, accented)

    def test_env_not_in_context_envs_dropped(self) -> None:
        # Context allows only dev and stg; prod is mentioned
        res = parse_rules(self._context("check production hom nay", envs=("dev", "stg")))
        self.assertIsNone(res.hints.env)
        self.assertIn("env", res.unresolved)


    def test_window_days_and_week(self) -> None:
        res_days = parse_rules(self._context("last 2 days"))
        self.assertEqual(res_days.hints.since, datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc))

        res_week = parse_rules(self._context("this week errors"))
        self.assertEqual(res_week.hints.since, datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc))

        res_over = parse_rules(self._context("last 200h"))
        self.assertIsNone(res_over.hints.since)
        self.assertIn("window", res_over.unresolved)


if __name__ == "__main__":
    unittest.main()
