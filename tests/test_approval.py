from __future__ import annotations

import tempfile
import unittest
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.test_job_store import FakeProtector


class _Runtime:
    public_tools = []
    audit = type("Audit", (), {"redact_content": staticmethod(lambda value, limit=2000: value[:limit])})()


class ApprovalStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        from mcp_hub.jobs.store import JobStore

        self.temp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        self.store = JobStore(
            Path(self.temp.name) / "jobs.sqlite3", protector=FakeProtector(), clock=lambda: self.now,
        )
        self.job_id = self.store.create_job(project="orders", question="write")
        self.store.transition_job(self.job_id, "running", event="job.started")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def _runner(self):
        from mcp_hub.agents.config import AGENT_ROLES, AgentConfig
        from mcp_hub.jobs.projects import ProjectConfig
        from mcp_hub.jobs.runner import JobRunner

        agents = {role: AgentConfig(role, None, None) for role in AGENT_ROLES}
        return JobRunner(
            store=self.store, runtime=_Runtime(), projects={"orders": ProjectConfig("orders", ("dev",))}, agents=agents,
        )

    def test_code_is_one_time_bound_to_exact_pending_call(self) -> None:
        code = self.store.create_pending_call(
            self.job_id, "logs__export_openobserve_logs", {"env": "prod", "org": "safe-test"}, agent="logs",
        )
        self.assertNotIn(code, repr(self.store.get_pending_call(self.job_id)))
        self.assertEqual(self.store.get_pending_call(self.job_id)["agent"], "logs")
        self.assertFalse(self.store.approve_pending_call(self.job_id, "wrong-code"))
        self.assertTrue(self.store.approve_pending_call(self.job_id, code))
        self.assertFalse(self.store.approve_pending_call(self.job_id, code))
        self.assertEqual(self.store.get_job(self.job_id)["state"], "running")
        self.assertEqual(self.store.consume_approved_call(self.job_id), (
            "logs__export_openobserve_logs", {"env": "prod", "org": "safe-test"},
        ))

    def test_expired_code_is_not_accepted_but_dashboard_can_approve(self) -> None:
        code = self.store.create_pending_call(self.job_id, "jira__sync_ticket", {"issue_key": "EX-1"})
        self.now += timedelta(minutes=11)
        self.assertFalse(self.store.approve_pending_call(self.job_id, code))
        self.assertTrue(self.store.approve_pending_call(self.job_id, dashboard=True))

    def test_dashboard_preview_shows_full_arguments_and_masks_only_secrets(self) -> None:
        arguments = {
            "issue_key": "EX-1", "email": "user@example.com", "summary": "Fix checkout",
            "api_token": "plain-secret-value", "nested": {"password": "hunter-two", "note": "Bearer abc.def.ghi"},
        }
        self.store.create_pending_call(self.job_id, "jira__update_ticket", arguments, agent="jira")
        preview = self._runner().pending_approval_preview(self.job_id)

        shown = json.loads(preview["arguments_preview"])
        self.assertEqual(shown["issue_key"], "EX-1")
        self.assertEqual(shown["email"], "user@example.com")
        self.assertEqual(shown["summary"], "Fix checkout")
        self.assertEqual(shown["api_token"], "[REDACTED]")
        self.assertEqual(shown["nested"]["password"], "[REDACTED]")
        self.assertNotIn("abc.def.ghi", preview["arguments_preview"])
        self.assertIn("\n  ", preview["arguments_preview"])  # pretty-printed
        self.assertFalse(preview["truncated"])
        self.assertEqual(preview["agent"], "jira")

    def test_preview_is_not_cut_at_2000_characters_and_marks_a_cut_past_its_cap(self) -> None:
        from mcp_hub.jobs.runner import MAX_PREVIEW_CHARS

        self.store.create_pending_call(self.job_id, "jira__update_ticket", {"description": "x" * 10_000})
        medium = self._runner().pending_approval_preview(self.job_id)
        self.assertIn("x" * 10_000, medium["arguments_preview"])
        self.assertFalse(medium["truncated"])

        self.store.transition_job(self.job_id, "cancelled", event="job.cancelled")
        job_id = self.store.create_job(project="orders", question="big write")
        self.store.transition_job(job_id, "running", event="job.started")
        self.store.create_pending_call(job_id, "jira__update_ticket", {"description": "y" * (MAX_PREVIEW_CHARS + 10)})
        large = self._runner().pending_approval_preview(job_id)
        self.assertTrue(large["truncated"])
        self.assertGreater(large["total_chars"], MAX_PREVIEW_CHARS)
        self.assertIn("[truncated: first", large["arguments_preview"])
        self.assertLess(len(large["arguments_preview"]), MAX_PREVIEW_CHARS + 200)

    def test_restart_expires_approval_waits_older_than_the_job_window(self) -> None:
        self.store.create_pending_call(self.job_id, "jira__sync_ticket", {"issue_key": "EX-1", "force": True})
        young = self.store.create_job(project="orders", question="young")
        self.store.transition_job(young, "running", event="job.started")
        self.store.create_pending_call(young, "jira__sync_ticket", {"issue_key": "EX-2", "force": True})

        self.now += timedelta(minutes=10)
        self.assertEqual(self.store.expire_awaiting_approval(timedelta(minutes=15)), [])
        self.now += timedelta(minutes=6)
        self.assertEqual(self.store.expire_awaiting_approval(timedelta(minutes=15), job_id=young), [young])

        self.assertEqual(self.store.get_job(young)["state"], "partial")
        self.assertIsNone(self.store.get_pending_call(young))
        self.assertIn("approval window ended", self.store.get_job(young)["summary"])
        events = [event["event"] for event in self.store.list_events(young)]
        self.assertEqual(events[-1], "approval.expired")
        self.assertEqual(self.store.get_job(self.job_id)["state"], "awaiting_approval")  # filtered by job_id
        self.assertEqual(self.store.expire_awaiting_approval(timedelta(minutes=15)), [self.job_id])


if __name__ == "__main__":
    unittest.main()
