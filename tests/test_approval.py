from __future__ import annotations

import tempfile
import unittest
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.test_job_store import FakeProtector


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

    def test_dashboard_preview_masks_sensitive_argument_but_keeps_key_visible(self) -> None:
        from mcp_hub.agents.config import AGENT_ROLES, AgentConfig
        from mcp_hub.jobs.projects import ProjectConfig
        from mcp_hub.jobs.runner import JobRunner

        class Runtime:
            public_tools = []
            audit = type("Audit", (), {"redact_content": staticmethod(lambda value, limit=2000: value[:limit])})()

        self.store.create_pending_call(
            self.job_id, "jira__sync_ticket", {"issue_key": "EX-1", "email": "private@example.com"}, agent="jira",
        )
        agents = {role: AgentConfig(role, None, None) for role in AGENT_ROLES}
        runner = JobRunner(
            store=self.store, runtime=Runtime(), projects={"orders": ProjectConfig("orders", ("dev",))}, agents=agents,
        )
        preview = runner.pending_approval_preview(self.job_id)
        arguments = json.loads(preview["arguments_preview"])
        self.assertEqual(arguments["issue_key"], "EX-1")
        self.assertEqual(arguments["email"], "[REDACTED]")
        self.assertNotIn("private@example.com", preview["arguments_preview"])


if __name__ == "__main__":
    unittest.main()
