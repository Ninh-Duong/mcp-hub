"""Investigation-job hardening: tool offer, tool-call failures, time limit, slots, queue, restart, evidence."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp.types import CallToolResult, TextContent

from tests.test_job_store import FakeProtector


READ_ONLY = SimpleNamespace(read_only_hint=True)
WRITE = SimpleNamespace(read_only_hint=False)


def _tool(name: str, annotations=READ_ONLY, schema: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, input_schema=schema or {"type": "object"}, annotations=annotations)


def _turn(*calls: tuple[str, str, dict]):
    from mcp_hub.agents.providers import ModelToolCall, ModelTurn

    return ModelTurn("", tuple(ModelToolCall(call_id, name, arguments) for call_id, name, arguments in calls))


async def _hang():
    await asyncio.sleep(30)


class _Audit:
    def redact_content(self, value, *, limit=1000):
        return value[:limit]


class _Runtime:
    def __init__(self, tools, handler=None):
        self.public_tools = tools
        self.audit = _Audit()
        self.calls: list[tuple[str, dict]] = []
        self.handler = handler

    async def call_child_tool(self, name, arguments, request_id, **metadata):
        self.calls.append((name, arguments))
        if self.handler is not None:
            return await self.handler(name, arguments)
        return CallToolResult(content=[TextContent(type="text", text=f"{name} rows")])


class _Provider:
    """Returns the scripted turns in order (a callable turn is awaited), then a final answer."""

    def __init__(self, *turns, final: str = "Found the cause."):
        self.turns = list(turns)
        self.final = final
        self.messages: list[dict] = []
        self.tools: list = []

    async def complete(self, messages, tools, **kwargs):
        from mcp_hub.agents.providers import ModelTurn

        self.messages, self.tools = list(messages), list(tools)
        if not self.turns:
            return ModelTurn(self.final)
        turn = self.turns.pop(0)
        return await turn() if callable(turn) else turn

    def tool_messages(self) -> list[dict]:
        return [message for message in self.messages if message["role"] == "tool"]


@contextmanager
def _runner(runtime, providers: dict, *, clock=None):
    from mcp_hub.agents.config import AGENT_ROLES, AgentConfig
    from mcp_hub.jobs.projects import ProjectConfig
    from mcp_hub.jobs.runner import JobRunner
    from mcp_hub.jobs.store import JobStore

    agents = {role: AgentConfig(role, None, None) for role in AGENT_ROLES}
    for role in providers:
        agents[role] = AgentConfig(role, "openai", "test-model", f"{role}-test-key")
    with tempfile.TemporaryDirectory() as directory:
        options = {"clock": clock} if clock else {}
        store = JobStore(Path(directory) / "jobs.sqlite3", protector=FakeProtector(), **options)
        try:
            yield JobRunner(
                store=store, runtime=runtime, projects={"orders": ProjectConfig("orders", ("dev",))},
                agents=agents, provider_factory=lambda config: providers[config.name],
            )
        finally:
            store.close()


async def _state_reached(store, job_id: str, state: str) -> bool:
    for _ in range(400):
        if store.get_job(job_id)["state"] == state:
            return True
        await asyncio.sleep(0.005)
    return False


async def _run_one(runner, **request) -> str:
    submitted = await runner.submit_issue(project="orders", question=request.pop("question", "Why?"), env="dev", **request)
    await asyncio.wait_for(runner.wait(submitted["job_id"]), 10)
    return submitted["job_id"]


def _events(runner, job_id: str) -> list[str]:
    return [event["event"] for event in runner.store.list_events(job_id, limit=500)]


class ToolOfferTest(unittest.TestCase):
    def test_write_tools_are_not_offered_and_a_named_one_is_rejected(self) -> None:
        runtime = _Runtime([_tool("logs__query_logs"), _tool("logs__export_logs", WRITE)])
        provider = _Provider(_turn(("c1", "logs__export_logs", {})))
        with _runner(runtime, {"logs": provider}) as runner:
            job_id = asyncio.run(_run_one(runner))
            self.assertEqual(runtime.calls, [])
            self.assertEqual([tool.name for tool in provider.tools], ["logs__query_logs"])
            [message] = provider.tool_messages()
            self.assertTrue(message["content"].startswith("Error: This tool is not offered"))
            self.assertNotIn("job.awaiting_approval", _events(runner, job_id))
            self.assertIn("agent.completed", _events(runner, job_id))

    def test_cache_sync_is_offered_and_a_forced_sync_waits_for_approval(self) -> None:
        runtime = _Runtime([_tool("jira__read_ticket"), _tool("jira__sync_ticket", WRITE)])
        provider = _Provider(
            _turn(("c1", "jira__sync_ticket", {"issue_key": "EX-1"})),
            _turn(("c2", "jira__sync_ticket", {"issue_key": "EX-1", "force": True})),
        )
        with _runner(runtime, {"jira": provider}) as runner:
            async def exercise() -> str:
                submitted = await runner.submit_issue(project="orders", question="Why?", env="dev", issue_key="EX-1")
                job_id = submitted["job_id"]
                self.assertTrue(await _state_reached(runner.store, job_id, "awaiting_approval"))
                self.assertEqual(len(runtime.calls), 1)  # the incremental sync ran without approval
                self.assertTrue(await runner.approve_from_dashboard(job_id))
                await asyncio.wait_for(runner.wait(job_id), 5)
                return job_id

            job_id = asyncio.run(exercise())
            self.assertEqual(runtime.calls[-1], ("jira__sync_ticket", {"issue_key": "EX-1", "force": True}))
            self.assertEqual(sorted(tool.name for tool in provider.tools), ["jira__read_ticket", "jira__sync_ticket"])
            self.assertIn("job.approved", _events(runner, job_id))


class ToolFailureTest(unittest.TestCase):
    def test_failed_calls_become_error_messages_and_successful_evidence_is_kept(self) -> None:
        async def handler(name, arguments):
            if name == "logs__boom":
                raise RuntimeError("child-detail-not-for-the-model")
            if name == "logs__flaky":
                return CallToolResult(content=[TextContent(type="text", text="upstream 503")], is_error=True)
            return CallToolResult(content=[TextContent(type="text", text="rows-ok")])

        schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
        runtime = _Runtime([_tool("logs__query_logs", schema=schema), _tool("logs__boom"), _tool("logs__flaky")], handler)
        provider = _Provider(_turn(
            ("c1", "logs__nope", {}), ("c2", "logs__query_logs", {}), ("c3", "logs__boom", {}),
            ("c4", "logs__flaky", {}), ("c5", "logs__query_logs", {"query": "a"}),
        ))
        with _runner(runtime, {"logs": provider}) as runner:
            job_id = asyncio.run(_run_one(runner))
            contents = [message["content"] for message in provider.tool_messages()]
            self.assertEqual([message["tool_call_id"] for message in provider.tool_messages()], ["c1", "c2", "c3", "c4", "c5"])
            self.assertIn("not offered", contents[0])
            self.assertIn("do not match the tool schema", contents[1])
            self.assertIn("Child call failed (RuntimeError)", contents[2])
            self.assertNotIn("child-detail-not-for-the-model", contents[2])
            self.assertIn("returned an error result", contents[3])
            self.assertIn("rows-ok", contents[4])
            job = runner.get_job(job_id)
            self.assertEqual(sorted(ref["tool"] for ref in job["evidence_refs"]), ["logs__flaky", "logs__query_logs"])
            self.assertIn("logs: Found the cause.", job["summary"])
            self.assertIn("agent.completed", _events(runner, job_id))
            self.assertNotIn("agent.failed", _events(runner, job_id))

    def test_call_cap_is_reported_to_the_model(self) -> None:
        from mcp_hub.jobs import runner as runner_module

        runtime = _Runtime([_tool("logs__query_logs")])
        provider = _Provider(_turn(("c1", "logs__query_logs", {"q": "a"}), ("c2", "logs__query_logs", {"q": "b"})))
        with patch.object(runner_module, "MAX_CHILD_CALLS", 1), _runner(runtime, {"logs": provider}) as runner:
            job_id = asyncio.run(_run_one(runner))
            contents = [message["content"] for message in provider.tool_messages()]
            self.assertIn("rows", contents[0])
            self.assertIn("1 child-tool call limit", contents[1])
            self.assertEqual(len(runtime.calls), 1)
            self.assertEqual(len(runner.get_job(job_id)["evidence_refs"]), 1)

    def test_provider_failure_keeps_the_specialists_stored_evidence(self) -> None:
        async def fail():
            raise RuntimeError("provider down")

        runtime = _Runtime([_tool("logs__query_logs")])
        provider = _Provider(_turn(("c1", "logs__query_logs", {})), fail)
        with _runner(runtime, {"logs": provider}) as runner:
            job_id = asyncio.run(_run_one(runner))
            job = runner.get_job(job_id)
            self.assertEqual(job["state"], "partial")
            self.assertIn("agent.failed", _events(runner, job_id))
            self.assertIn(f"logs evidence: [{job['evidence_refs'][0]['source_id']}]", job["summary"])


class TimeLimitTest(unittest.TestCase):
    def _hanging_logs(self) -> tuple[_Runtime, _Provider]:
        return _Runtime([_tool("logs__query_logs")]), _Provider(_turn(("c1", "logs__query_logs", {})), _hang)

    def test_time_limit_keeps_findings_and_runs_the_coordinator(self) -> None:
        from mcp_hub.jobs import runner as runner_module

        runtime, logs = self._hanging_logs()
        coordinator = _Provider(final="Root cause in logs.")
        with patch.object(runner_module, "MAX_JOB_SECONDS", 0.3), \
                _runner(runtime, {"logs": logs, "coordinator": coordinator}) as runner:
            job_id = asyncio.run(_run_one(runner))
            job = runner.get_job(job_id)
            self.assertEqual(job["state"], "partial")
            self.assertIn("Root cause in logs.", job["summary"])
            self.assertIn("15-minute time limit", job["summary"])
            payload = json.loads(coordinator.messages[-1]["content"])
            logs_finding = next(item for item in payload["findings"] if item["agent"] == "logs")
            self.assertEqual([ref["source_id"] for ref in logs_finding["evidence"]],
                             [ref["source_id"] for ref in job["evidence_refs"]])
            self.assertIn("agent.timed_out", _events(runner, job_id))

    def test_time_limit_without_a_coordinator_lists_stored_evidence(self) -> None:
        from mcp_hub.jobs import runner as runner_module

        runtime, logs = self._hanging_logs()
        with patch.object(runner_module, "MAX_JOB_SECONDS", 0.3), _runner(runtime, {"logs": logs}) as runner:
            job_id = asyncio.run(_run_one(runner))
            job = runner.get_job(job_id)
            self.assertEqual(job["state"], "partial")
            self.assertIn(f"[{job['evidence_refs'][0]['source_id']}]", job["summary"])
            self.assertIn("stopped at the 15-minute job time limit", job["summary"])


class SlotAndQueueTest(unittest.TestCase):
    def test_a_job_waiting_for_approval_does_not_hold_an_active_slot(self) -> None:
        from mcp_hub.jobs import runner as runner_module

        runtime = _Runtime([_tool("jira__sync_ticket", WRITE)])
        jira = _Provider(_turn(("c1", "jira__sync_ticket", {"issue_key": "EX-1", "force": True})))
        with patch.object(runner_module, "MAX_ACTIVE_JOBS", 1), _runner(runtime, {"jira": jira}) as runner:
            async def exercise() -> tuple[str, str]:
                first = (await runner.submit_issue(project="orders", question="Sync", env="dev", issue_key="EX-1"))["job_id"]
                self.assertTrue(await _state_reached(runner.store, first, "awaiting_approval"))
                second = await asyncio.wait_for(_run_one(runner, question="Other"), 5)
                self.assertEqual(runner.store.get_job(first)["state"], "awaiting_approval")
                self.assertTrue(await runner.approve_from_dashboard(first))
                await asyncio.wait_for(runner.wait(first), 5)
                return first, second

            first, second = asyncio.run(exercise())
            self.assertEqual(runner.store.get_job(second)["state"], "partial")
            self.assertIn(runner.store.get_job(first)["state"], {"partial", "completed"})
            self.assertEqual(runtime.calls, [("jira__sync_ticket", {"issue_key": "EX-1", "force": True})])

    def test_queue_is_capped_and_reports_a_clear_error(self) -> None:
        from mcp_hub.jobs import runner as runner_module

        with patch.object(runner_module, "MAX_QUEUED_JOBS", 2), _runner(_Runtime([]), {}) as runner:
            for _ in range(2):
                runner.store.create_job(project="orders", question="waiting", env="dev")
            waiting = runner.store.create_job(project="orders", question="no env", state="awaiting_input")

            async def exercise():
                full = await runner.call_tool("hub__submit_issue", {"project": "orders", "question": "Q", "env": "dev"})
                needs_input = await runner.call_tool("hub__provide_job_input", {"job_id": waiting, "env": "dev"})
                return full, needs_input

            full, needs_input = asyncio.run(exercise())
            for result in (full, needs_input):
                self.assertTrue(result.is_error)
                self.assertIn("investigation queue is full (2 queued jobs)", result.content[0].text)
            self.assertEqual(len(runner.store.list_job_ids_by_state("queued")), 2)
            self.assertEqual(runner.store.get_job(waiting)["state"], "awaiting_input")

    def test_cancel_while_waiting_for_a_slot_leaves_no_task_entry(self) -> None:
        from mcp_hub.jobs.runner import MAX_ACTIVE_JOBS

        with _runner(_Runtime([]), {}) as runner:
            async def exercise():
                for _ in range(MAX_ACTIVE_JOBS):
                    await runner._slots.acquire()
                job_id = (await runner.submit_issue(project="orders", question="Q", env="dev"))["job_id"]
                task = runner._tasks[job_id]
                await asyncio.sleep(0.02)
                self.assertFalse(task.done())
                await runner.cancel_job(job_id)
                await asyncio.gather(task, return_exceptions=True)
                return job_id, task

            job_id, task = asyncio.run(exercise())
            self.assertTrue(task.cancelled())
            self.assertNotIn(job_id, runner._tasks)
            self.assertNotIn(job_id, runner._job_slots)
            self.assertEqual(runner.store.get_job(job_id)["state"], "cancelled")


class OffloadAndRestartTest(unittest.TestCase):
    def test_store_work_runs_off_the_event_loop(self) -> None:
        offloaded: list[str] = []
        original = asyncio.to_thread

        async def recording(function, *args, **kwargs):
            offloaded.append(function.__name__)
            return await original(function, *args, **kwargs)

        runtime = _Runtime([_tool("logs__query_logs")])
        provider = _Provider(_turn(("c1", "logs__query_logs", {})))
        with _runner(runtime, {"logs": provider}) as runner, patch("asyncio.to_thread", recording):
            job_id = asyncio.run(_run_one(runner))
            asyncio.run(runner.call_tool("hub__get_job", {"job_id": job_id}))
        self.assertLessEqual(
            {"create_job", "get_request", "save_encrypted_result", "append_event", "get_job", "_finish_job"},
            set(offloaded),
        )

    def test_restart_settles_expired_approvals_and_times_out_the_younger_ones(self) -> None:
        start = datetime(2026, 10, 28, tzinfo=timezone.utc)
        clock = SimpleNamespace(now=start)
        with _runner(_Runtime([]), {}, clock=lambda: clock.now) as runner:
            store = runner.store
            old = store.create_job(project="orders", question="old", env="dev")
            clock.now = start + timedelta(minutes=10)
            young = store.create_job(project="orders", question="young", env="dev")
            for job_id in (old, young):
                store.transition_job(job_id, "running", event="job.started")
                store.create_pending_call(job_id, "jira__sync_ticket", {"issue_key": "EX-1", "force": True})
            clock.now = start + timedelta(minutes=24, seconds=59.9)

            async def exercise() -> int:
                waiting = runner.watch_pending_approvals()
                self.assertEqual(store.get_job(old)["state"], "partial")
                self.assertEqual(store.get_job(young)["state"], "awaiting_approval")
                self.assertIsNone(runner.get_approval_code(young))  # MCP codes do not survive a restart
                clock.now = start + timedelta(minutes=26)
                await asyncio.wait_for(asyncio.gather(*runner._timers), 5)
                return waiting

            self.assertEqual(asyncio.run(exercise()), 1)
            for job_id in (old, young):
                self.assertEqual(store.get_job(job_id)["state"], "partial")
                self.assertEqual(_events(runner, job_id)[-1], "approval.expired")
                self.assertIsNone(store.get_pending_call(job_id))

    def test_get_job_lists_every_evidence_ref_beyond_the_event_page(self) -> None:
        from mcp_hub.jobs.runner import EVENT_PAGE_LIMIT

        with _runner(_Runtime([]), {}) as runner:
            job_id = runner.store.create_job(project="orders", question="busy", env="dev")
            for index in range(EVENT_PAGE_LIMIT + 20):
                runner.store.append_event(job_id, "agent.started", agent="logs")
            for index in range(5):
                runner.store.append_event(job_id, "tool.call.succeeded", agent="logs", tool="logs__query_logs",
                                          source_id=f"logs-{index}")
            runner.store.append_event(job_id, "tool.call.reused", agent="logs", tool="logs__query_logs")
            job = runner.get_job(job_id)
        self.assertEqual(len(job["events"]), EVENT_PAGE_LIMIT)
        self.assertEqual([ref["source_id"] for ref in job["evidence_refs"]], [f"logs-{index}" for index in range(5)])


if __name__ == "__main__":
    unittest.main()
