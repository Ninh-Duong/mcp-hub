"""jobs/approvals.py through JobRunner.approve_job (T123, US2, FR-019).

A correct one-time code runs the stored write once. A wrong code, an unknown job, or an expired code runs nothing, and
the pending write stays pending. The job API has no rejection: a pending write ends only by an approval or by expiry,
so the tests do not expect a reject call.
"""
from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from mcp_hub.jobs.approvals import ApprovalMixin
from tests.helpers.jobs import _Runtime, _runner

WRITE_TOOL = "jira__sync_ticket"
WRITE_ARGUMENTS = {"issue_key": "EX-1", "force": True}


class ApproveJobTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
        self.runtime = _Runtime([])

    def _pending_write(self, runner) -> tuple[str, str]:
        job_id = runner.store.create_job(project="orders", question="sync the ticket")
        runner.store.transition_job(job_id, "running", event="job.started")
        code = runner.store.create_pending_call(job_id, WRITE_TOOL, WRITE_ARGUMENTS, agent="jira")
        return job_id, code

    def _approve(self, runner, job_id: str, code: str) -> dict:
        async def exercise() -> dict:
            result = await runner.approve_job(job_id, code)
            task = runner._tasks.get(job_id)
            if task is not None:
                await task
            return result

        return asyncio.run(exercise())

    def test_a_correct_code_runs_the_stored_write_once(self) -> None:
        with _runner(self.runtime, {}, clock=lambda: self.now) as runner:
            job_id, code = self._pending_write(runner)
            result = self._approve(runner, job_id, code)
            self.assertEqual(result, {"job_id": job_id, "approved": True})
            self.assertEqual(self.runtime.calls, [(WRITE_TOOL, WRITE_ARGUMENTS)])
            self.assertIsNone(runner.store.get_pending_call(job_id))

    def test_a_wrong_code_runs_nothing_and_the_write_stays_pending(self) -> None:
        with _runner(self.runtime, {}, clock=lambda: self.now) as runner:
            job_id, _code = self._pending_write(runner)
            result = self._approve(runner, job_id, "000000")
            self.assertEqual(result, {"job_id": job_id, "approved": False})
            self.assertEqual(self.runtime.calls, [])
            self.assertIsNotNone(runner.store.get_pending_call(job_id))

    def test_an_expired_code_runs_nothing(self) -> None:
        with _runner(self.runtime, {}, clock=lambda: self.now) as runner:
            job_id, code = self._pending_write(runner)
            self.now += timedelta(minutes=11)  # the approval code lives for 10 minutes
            result = self._approve(runner, job_id, code)
            self.assertFalse(result["approved"])
            self.assertEqual(self.runtime.calls, [])

    def test_an_unknown_job_is_not_approved(self) -> None:
        with _runner(self.runtime, {}, clock=lambda: self.now) as runner:
            result = self._approve(runner, "job-does-not-exist", "000000")
            self.assertEqual(result, {"job_id": "job-does-not-exist", "approved": False})
            self.assertEqual(self.runtime.calls, [])

    def test_the_approval_mixin_is_part_of_the_runner(self) -> None:
        from mcp_hub.jobs.runner import JobRunner

        self.assertTrue(issubclass(JobRunner, ApprovalMixin))

    def test_admin_can_list_approve_and_reject_pending_writes(self) -> None:
        async def exercise() -> None:
            with _runner(self.runtime, {}, clock=lambda: self.now) as runner:
                approve_id, _ = self._pending_write(runner)
                reject_id, _ = self._pending_write(runner)
                pending = await runner.list_pending_approvals()
                self.assertEqual([item["job_id"] for item in pending], [approve_id, reject_id])
                self.assertEqual(pending[0]["argument_keys"], ["force", "issue_key"])
                self.assertNotIn("EX-1", repr(pending))

                approved = await runner.resolve_pending_approval(approve_id, approve=True)
                self.assertEqual(approved, {"job_id": approve_id, "action": "approve", "applied": True})
                if approve_id in runner._tasks:
                    await runner._tasks[approve_id]
                self.assertEqual(self.runtime.calls, [(WRITE_TOOL, WRITE_ARGUMENTS)])

                rejected = await runner.resolve_pending_approval(reject_id, approve=False)
                self.assertEqual(rejected, {"job_id": reject_id, "action": "reject", "applied": True})
                self.assertEqual(runner.store.get_job(reject_id)["state"], "partial")
                self.assertEqual(await runner.list_pending_approvals(), [])

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
