from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from mcp.types import CallToolResult, TextContent

from mcp_hub.agents.config import AgentConfig
from mcp_hub.agents.providers import ModelProvider, ModelToolCall, ModelTurn
from mcp_hub.agents.specialists import SpecialistExecutor
from mcp_hub.extensions import Condensed, PlanAssistant, PlanContext, PlanHints, ResultCondenser
from mcp_hub.jobs.runner import JobRunner
from mcp_hub.jobs.store import JobStore
from mcp_hub.projects import ProjectConfig


class _PlainProtector:
    available = True

    def protect(self, data: bytes) -> bytes:
        return data

    def unprotect(self, data: bytes) -> bytes:
        return data


class _FakeAssistant(PlanAssistant):
    def __init__(self, hints: PlanHints | None = None, should_raise: bool = False) -> None:
        self._hints = hints
        self.should_raise = should_raise

    async def hints(self, context: PlanContext) -> PlanHints | None:
        if self.should_raise:
            raise RuntimeError("Assistant failed")
        return self._hints


class _FakeCondenser(ResultCondenser):
    def __init__(self, should_raise: bool = False) -> None:
        self.should_raise = should_raise
        self.called = False

    async def condense(self, text: str, task: str | None = None) -> Condensed:
        self.called = True
        if self.should_raise:
            raise RuntimeError("Condenser failed")
        return Condensed(
            text="condensed summary line",
            kept_lines=5,
            total_lines=100,
            source="rules",
        )


class _FakeProvider(ModelProvider):
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)

    async def complete(self, messages: list[dict], tools: list) -> ModelTurn:
        return self.turns.pop(0)


class JobOrchestratorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name) / "jobs.sqlite3", protector=_PlainProtector())
        self.runtime = SimpleNamespace(
            routes={"sql__run_select": SimpleNamespace(server_id="mcp-sqlserver")},
            public_tools=[],
            audit=None,
        )
        self.runner = JobRunner(
            store=self.store,
            runtime=self.runtime,
            projects={"orders": ProjectConfig("orders", ("dev",), default_env="dev")},
            agents={
                role: AgentConfig(role, "openai", "test-model", "test-key")
                for role in ("coordinator", "logs", "source", "jira", "pr", "sql")
            },
        )

    async def asyncTearDown(self) -> None:
        await self.runner.close()
        self.store.close()
        self.tmp.cleanup()

    async def test_plan_job_without_assistant_uses_default_roles_and_off_event(self) -> None:
        job_id = self.store.create_job(project="orders", question="Check logs for errors")
        req = self.store.get_request(job_id)
        roles, new_req = await self.runner._plan_job(job_id, req)

        self.assertEqual(roles, ["logs", "source"])
        self.assertEqual(req, new_req)
        events = self.store.list_events(job_id)
        planned_event = next(e for e in events if e["event"] == "job.planned")
        self.assertEqual(planned_event["tool"], "off")

    async def test_plan_job_with_assistant_hinting_sql_adds_role(self) -> None:
        assistant = _FakeAssistant(
            PlanHints(servers=("mcp-sqlserver",), source="bitnet")
        )
        self.runner.plan_assistant = assistant
        job_id = self.store.create_job(project="orders", question="Check logs for errors")
        req = self.store.get_request(job_id)
        roles, _ = await self.runner._plan_job(job_id, req)

        self.assertIn("sql", roles)
        events = self.store.list_events(job_id)
        planned_event = next(e for e in events if e["event"] == "job.planned")
        self.assertEqual(planned_event["tool"], "bitnet")

    async def test_plan_job_with_raising_assistant_uses_fallback(self) -> None:
        self.runner.plan_assistant = _FakeAssistant(should_raise=True)
        job_id = self.store.create_job(project="orders", question="Check logs")
        req = self.store.get_request(job_id)
        roles, _ = await self.runner._plan_job(job_id, req)

        self.assertEqual(roles, ["logs", "source"])
        events = self.store.list_events(job_id)
        planned_event = next(e for e in events if e["event"] == "job.planned")
        self.assertEqual(planned_event["tool"], "fallback")

    async def test_plan_job_fills_window_only_when_absent(self) -> None:
        s = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
        u = datetime(2026, 11, 8, 4, 0, tzinfo=timezone.utc)
        self.runner.plan_assistant = _FakeAssistant(PlanHints(since=s, until=u, source="bitnet"))

        # Case 1: absent window
        job1 = self.store.create_job(project="orders", question="Check logs")
        req1 = self.store.get_request(job1)
        _, planned_req1 = await self.runner._plan_job(job1, req1)
        self.assertEqual(planned_req1["since"], s.isoformat())
        self.assertEqual(planned_req1["until"], u.isoformat())
        stored_req1 = self.store.get_request(job1)
        self.assertEqual(stored_req1["since"], s.isoformat())

        # Case 2: present window
        job2 = self.store.create_job(
            project="orders", question="Check logs",
            since="2026-11-01T00:00:00+00:00", until="2026-11-01T02:00:00+00:00",
        )
        req2 = self.store.get_request(job2)
        _, planned_req2 = await self.runner._plan_job(job2, req2)
        self.assertEqual(planned_req2["since"], "2026-11-01T00:00:00+00:00")
        self.assertEqual(planned_req2["until"], "2026-11-01T02:00:00+00:00")


class SpecialistCondenserTest(unittest.IsolatedAsyncioTestCase):
    async def test_condenser_shrinks_large_content_and_appends_note(self) -> None:
        condenser = _FakeCondenser()
        saved = {}

        def fake_save(job_id: str, source_id: str, data: any) -> None:
            saved[source_id] = data

        large_text = "log line row\n" * 600  # > 6000 chars
        tool_call = ModelToolCall(call_id="call-1", name="logs__query", arguments={})
        turns = [
            ModelTurn(text="", tool_calls=[tool_call]),
            ModelTurn(text="All done", tool_calls=[]),
        ]
        provider = _FakeProvider(turns)

        async def fake_call(*args) -> CallToolResult:
            return CallToolResult(content=[TextContent(type="text", text=large_text)], is_error=False)

        executor = SpecialistExecutor(
            runtime=SimpleNamespace(public_tools=[SimpleNamespace(name="logs__query", annotations=SimpleNamespace(read_only_hint=True))], audit=None),
            provider=provider,
            save_result=fake_save,
            call_tool=fake_call,
            condenser=condenser,
            condense_min_chars=6000,
        )

        res = await executor.run("job-1", "logs", "Analyze logs")
        self.assertTrue(condenser.called)
        self.assertEqual(res.summary, "All done")
        self.assertEqual(len(saved), 1)

    async def test_condenser_failure_keeps_content(self) -> None:
        condenser = _FakeCondenser(should_raise=True)
        large_text = "log line row\n" * 600
        tool_call = ModelToolCall(call_id="call-1", name="logs__query", arguments={})
        turns = [
            ModelTurn(text="", tool_calls=[tool_call]),
            ModelTurn(text="Done after failure", tool_calls=[]),
        ]
        provider = _FakeProvider(turns)

        async def fake_call(*args) -> CallToolResult:
            return CallToolResult(content=[TextContent(type="text", text=large_text)], is_error=False)

        executor = SpecialistExecutor(
            runtime=SimpleNamespace(public_tools=[SimpleNamespace(name="logs__query", annotations=SimpleNamespace(read_only_hint=True))], audit=None),
            provider=provider,
            save_result=lambda *args: None,
            call_tool=fake_call,
            condenser=condenser,
            condense_min_chars=6000,
        )

        res = await executor.run("job-1", "logs", "Analyze logs")
        self.assertEqual(res.summary, "Done after failure")


if __name__ == "__main__":
    unittest.main()
