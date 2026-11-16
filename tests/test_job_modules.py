"""Unit checks for the job modules split out of runner.py: tools (schemas, deterministic arguments), common (store
offload and task cancel), investigation (which roles a question gets), approvals (refusals of a wrong or unknown code).
"""
from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace

from mcp_hub.jobs.approvals import ApprovalMixin
from mcp_hub.jobs.common import JobSlot, cancel_all, store_call
from mcp_hub.jobs.investigation import InvestigationMixin, roles_for
from mcp_hub.jobs.tools import build_deterministic_args, limit_text, object_schema
from tests.helpers.jobs import _runner, _Runtime


class ToolsTest(unittest.TestCase):
    def test_object_schema_closes_the_object_and_lists_required_names(self) -> None:
        schema = object_schema({"x": {"type": "string"}}, ("x",))
        self.assertEqual(schema, {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"],
                                  "additionalProperties": False})
        self.assertNotIn("required", object_schema({}, ()))

    def test_limit_text_names_whole_minutes_and_seconds(self) -> None:
        self.assertEqual(limit_text(900), "15-minute")
        self.assertEqual(limit_text(90), "90-second")

    def test_logs_arguments_come_from_the_project_target_for_the_env(self) -> None:
        target = SimpleNamespace(organization="org-1", stream="orders")
        project = SimpleNamespace(logs={"dev": target})
        props = {"organization": {}, "stream": {}, "env": {}, "query": {}}
        args = build_deterministic_args("logs", props, project, {"env": "dev", "question": "why"})
        self.assertEqual(args, {"organization": "org-1", "stream": "orders", "env": "dev", "query": "why"})

    def test_logs_without_a_target_for_the_env_get_no_arguments(self) -> None:
        project = SimpleNamespace(logs={"dev": SimpleNamespace(organization="o", stream="s")})
        self.assertIsNone(build_deterministic_args("logs", {"env": {}}, project, {"env": "prod"}))
        self.assertIsNone(build_deterministic_args("logs", {"env": {}}, None, {"env": "dev"}))

    def test_sql_arguments_carry_the_question_and_env(self) -> None:
        args = build_deterministic_args("sql", {"query": {}, "env": {}}, None, {"question": "count", "env": "stg"})
        self.assertEqual(args, {"query": "count", "env": "stg"})

    def test_other_roles_are_not_given_deterministic_arguments(self) -> None:
        self.assertEqual(build_deterministic_args("jira", {"query": {}}, None, {"question": "x"}), {})


class CommonTest(unittest.TestCase):
    def test_store_call_runs_the_operation_on_another_thread(self) -> None:
        loop_thread = threading.get_ident()

        def record() -> int:
            return threading.get_ident()

        seen = asyncio.run(store_call(record))
        self.assertNotEqual(seen, loop_thread)

    def test_cancel_all_cancels_pending_tasks_and_skips_finished_ones(self) -> None:
        async def exercise():
            pending = asyncio.ensure_future(asyncio.sleep(30))
            done = asyncio.get_running_loop().create_future()
            done.set_result(None)  # already finished: cancel_all must leave it alone
            await cancel_all([pending, done])
            return pending, done

        pending, done = asyncio.run(exercise())
        self.assertTrue(pending.cancelled())
        self.assertFalse(done.cancelled())

    def test_a_job_slot_releases_its_semaphore_once(self) -> None:
        async def exercise() -> int:
            slots = asyncio.Semaphore(1)
            slot = JobSlot(slots)
            await slot.acquire()
            slot.release()
            slot.release()  # a second release must not add a slot
            await slots.acquire()
            return slots._value  # noqa: SLF001 - the number of free slots after the acquire

        self.assertLessEqual(asyncio.run(exercise()), 0)


class InvestigationTest(unittest.TestCase):
    def test_a_plain_question_gets_the_logs_and_source_roles(self) -> None:
        self.assertEqual(roles_for({"question": "why did the order fail"}), ["logs", "source"])

    def test_an_issue_key_adds_jira_and_a_pull_request_adds_pr(self) -> None:
        roles = roles_for({"question": "see the pull request", "issue_key": "ABC-1"})
        self.assertEqual(roles, ["logs", "source", "jira", "pr"])

    def test_a_database_question_adds_sql(self) -> None:
        self.assertIn("sql", roles_for({"question": "which table holds the rows"}))

    def test_the_investigation_mixin_is_part_of_the_runner(self) -> None:
        from mcp_hub.jobs.runner import JobRunner

        self.assertTrue(issubclass(JobRunner, InvestigationMixin))
        self.assertTrue(issubclass(JobRunner, ApprovalMixin))


class ApprovalsTest(unittest.TestCase):
    def test_an_unknown_job_is_not_approved(self) -> None:
        with _runner(_Runtime([]), {}) as runner:
            result = asyncio.run(runner.approve_job("job-does-not-exist", "000000"))
        self.assertEqual(result, {"job_id": "job-does-not-exist", "approved": False})


if __name__ == "__main__":
    unittest.main()
