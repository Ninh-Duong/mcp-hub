from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from mcp.types import CallToolResult, TextContent

from tests.test_job_store import FakeProtector


class IssueWorkflowTest(unittest.TestCase):
    def test_missing_environment_waits_for_input_and_does_not_run_agents(self) -> None:
        from mcp_hub.agents.config import AGENT_ROLES, AgentConfig
        from mcp_hub.jobs.projects import ProjectConfig
        from mcp_hub.jobs.runner import JobRunner
        from mcp_hub.jobs.store import JobStore

        class Runtime:
            public_tools = []

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite3", protector=FakeProtector())
            agents = {role: AgentConfig(role, None, None) for role in AGENT_ROLES}
            runner = JobRunner(
                store=store,
                runtime=Runtime(),
                projects={"orders": ProjectConfig("orders", ("dev", "stg", "prod"))},
                agents=agents,
            )

            async def exercise() -> tuple[str, str]:
                submitted = await runner.submit_issue(project="orders", question="Why did the request fail?")
                self.assertEqual(submitted["status"], "awaiting_input")
                self.assertEqual(runner.get_job(submitted["job_id"])["state"], "awaiting_input")
                with self.assertRaises(ValueError):
                    await runner.provide_input(submitted["job_id"], "production")
                await runner.provide_input(submitted["job_id"], "prod")
                await runner.wait(submitted["job_id"])
                return submitted["job_id"], store.get_job(submitted["job_id"])["state"]

            try:
                job_id, state = asyncio.run(exercise())
                self.assertEqual(state, "partial")
                job = runner.get_job(job_id)
                self.assertIsNotNone(job["summary"])
                self.assertTrue(any(event["event"] == "agent.unavailable" for event in job["events"]))
            finally:
                store.close()

    def test_approval_after_restart_executes_only_persisted_operation(self) -> None:
        from mcp_hub.agents.config import AGENT_ROLES, AgentConfig
        from mcp_hub.jobs.projects import ProjectConfig
        from mcp_hub.jobs.runner import JobRunner
        from mcp_hub.jobs.store import JobStore

        class Runtime:
            public_tools = []
            audit = type("Audit", (), {"redact_content": staticmethod(lambda value, limit=2000: value[:limit])})()

            def __init__(self):
                self.calls = []

            async def call_child_tool(self, name, arguments, request_id, **metadata):
                self.calls.append((name, arguments, request_id, metadata))
                return CallToolResult(content=[TextContent(type="text", text="written")])

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite3", protector=FakeProtector())
            job_id = store.create_job(project="orders", question="sync ticket", env="dev")
            store.transition_job(job_id, "running", event="job.started")
            store.create_pending_call(
                job_id, "jira__sync_ticket", {"issue_key": "EX-1", "summary": "approved"}, agent="jira",
            )
            runtime = Runtime()
            agents = {role: AgentConfig(role, None, None) for role in AGENT_ROLES}
            runner = JobRunner(
                store=store, runtime=runtime,
                projects={"orders": ProjectConfig("orders", ("dev",))}, agents=agents,
            )
            try:
                async def wait_for_approved_write():
                    self.assertTrue(await runner.approve_from_dashboard(job_id))
                    await runner.wait(job_id)

                asyncio.run(wait_for_approved_write())
                self.assertEqual(runtime.calls, [(
                    "jira__sync_ticket", {"issue_key": "EX-1", "summary": "approved"}, f"job:{job_id}",
                    {"client_name": "investigation", "agent_role": "jira"},
                )])
                self.assertEqual(store.get_job(job_id)["state"], "partial")
                self.assertIsNotNone(store.get_detail_page(job_id, "approved-write", offset=0, limit=100)["content"])
            finally:
                store.close()

    def test_every_write_in_one_job_waits_for_its_own_approval(self) -> None:
        from mcp_hub.agents.config import AGENT_ROLES, AgentConfig
        from mcp_hub.jobs.projects import ProjectConfig
        from mcp_hub.jobs.runner import JobRunner
        from mcp_hub.jobs.store import JobStore

        class Runtime:
            public_tools = []

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite3", protector=FakeProtector())
            job_id = store.create_job(project="orders", question="sync two tickets", env="dev")
            store.transition_job(job_id, "running", event="job.started")
            runner = JobRunner(
                store=store, runtime=Runtime(), projects={"orders": ProjectConfig("orders", ("dev",))},
                agents={role: AgentConfig(role, None, None) for role in AGENT_ROLES},
            )

            async def waiting_for(task: asyncio.Task, issue_key: str) -> bool:
                for _ in range(200):
                    pending = store.get_pending_call(job_id, include_arguments=True)
                    if pending and pending["arguments"] == {"issue_key": issue_key}:
                        return not task.done()
                    if task.done():
                        return False
                    await asyncio.sleep(0.005)
                return False

            async def exercise() -> list[dict]:
                async def two_writes() -> list[dict]:
                    return [
                        await runner._await_approval(job_id, "jira", "jira__sync_ticket", {"issue_key": key})
                        for key in ("EX-1", "EX-2")
                    ]

                task = asyncio.create_task(two_writes())
                runner._tasks[job_id] = task
                for key in ("EX-1", "EX-2"):
                    self.assertTrue(await waiting_for(task, key), f"{key} did not wait for approval")
                    self.assertTrue(await runner.approve_from_dashboard(job_id))
                return await asyncio.wait_for(task, 5)

            try:
                self.assertEqual(asyncio.run(exercise()), [{"issue_key": "EX-1"}, {"issue_key": "EX-2"}])
            finally:
                store.close()

    def test_hub_exposes_job_tools_with_bounded_schemas(self) -> None:
        from mcp_hub.jobs.runner import JobRunner

        names = {tool.name for tool in JobRunner.tool_definitions()}
        self.assertEqual(names, {
            "hub__list_projects", "hub__submit_issue", "hub__get_job", "hub__get_job_detail",
            "hub__provide_job_input", "hub__approve_job", "hub__cancel_job",
        })

    def test_project_discovery_returns_aliases_and_environments_not_roots(self) -> None:
        from mcp_hub.agents.config import AGENT_ROLES, AgentConfig
        from mcp_hub.jobs.projects import ProjectConfig
        from mcp_hub.jobs.runner import JobRunner
        from mcp_hub.jobs.store import JobStore

        class Runtime:
            public_tools = []

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite3", protector=FakeProtector())
            agents = {role: AgentConfig(role, None, None) for role in AGENT_ROLES}
            runner = JobRunner(
                store=store, runtime=Runtime(),
                projects={"orders": ProjectConfig("orders", ("dev", "prod"), "dev")}, agents=agents,
            )
            try:
                self.assertEqual(runner.list_projects(), [{
                    "alias": "orders", "default_env": "dev", "allowed_envs": ["dev", "prod"],
                }])
            finally:
                store.close()

    def test_provider_prompts_redact_user_and_configured_secrets(self) -> None:
        from types import SimpleNamespace

        from mcp_hub.agents.config import AGENT_ROLES, AgentConfig
        from mcp_hub.agents.providers import ModelTurn
        from mcp_hub.audit import redact
        from mcp_hub.jobs.projects import ProjectConfig
        from mcp_hub.jobs.runner import JobRunner
        from mcp_hub.jobs.store import JobStore

        configured_secret = "configured-child-secret"
        question = (
            "Check Authorization: Bearer test-bearer-secret, analyst@example.com, "
            "api_key=test-api-secret, and " + configured_secret
        )

        class Audit:
            def __init__(self):
                self.secrets = set()

            def add_secrets(self, values):
                self.secrets.update(value for value in values if value)

            def redact_content(self, value, *, limit=1000):
                return redact(value, self.secrets, limit=limit)

        class Runtime:
            public_tools = [SimpleNamespace(
                name="logs__query_logs", input_schema={"type": "object"},
                annotations=SimpleNamespace(read_only_hint=True),
            )]

            def __init__(self):
                self.audit = Audit()

        class Provider:
            def __init__(self):
                self.messages = []

            async def complete(self, messages, tools, **kwargs):
                self.messages = messages
                return ModelTurn("No matching evidence.")

        providers = {"logs": Provider(), "coordinator": Provider()}
        agents = {role: AgentConfig(role, None, None) for role in AGENT_ROLES}
        agents["logs"] = AgentConfig("logs", "openai", "test-model", configured_secret)
        agents["coordinator"] = AgentConfig("coordinator", "openai", "test-model", "coordinator-secret")

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite3", protector=FakeProtector())
            runner = JobRunner(
                store=store,
                runtime=Runtime(),
                projects={"orders": ProjectConfig("orders", ("dev",))},
                agents=agents,
                provider_factory=lambda config: providers[config.name],
            )
            try:
                async def exercise():
                    submitted = await runner.submit_issue(project="orders", question=question, env="dev")
                    await runner.wait(submitted["job_id"])

                asyncio.run(exercise())
                for provider in providers.values():
                    content = provider.messages[-1]["content"]
                    for secret in (
                        "test-bearer-secret", "analyst@example.com", "test-api-secret", configured_secret,
                    ):
                        self.assertNotIn(secret, content)
            finally:
                store.close()

    def test_queued_jobs_are_resumed_after_service_restart(self) -> None:
        from mcp_hub.agents.config import AGENT_ROLES, AgentConfig
        from mcp_hub.jobs.projects import ProjectConfig
        from mcp_hub.jobs.runner import JobRunner
        from mcp_hub.jobs.store import JobStore

        class Runtime:
            public_tools = []
            audit = type("Audit", (), {"redact_content": staticmethod(lambda value, limit=2000: value[:limit])})()

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite3", protector=FakeProtector())
            job_id = store.create_job(project="orders", question="queued after restart", env="dev")
            agents = {role: AgentConfig(role, None, None) for role in AGENT_ROLES}
            runner = JobRunner(
                store=store, runtime=Runtime(),
                projects={"orders": ProjectConfig("orders", ("dev",))}, agents=agents,
            )
            try:
                async def resume():
                    self.assertEqual(runner.resume_queued_jobs(), 1)
                    await runner.wait(job_id)

                asyncio.run(resume())
                self.assertEqual(store.get_job(job_id)["state"], "partial")
            finally:
                store.close()

    def test_identical_tool_calls_in_one_job_reuse_the_first_result(self) -> None:
        from types import SimpleNamespace

        from mcp_hub.agents.config import AGENT_ROLES, AgentConfig
        from mcp_hub.agents.providers import ModelToolCall, ModelTurn
        from mcp_hub.jobs.projects import ProjectConfig
        from mcp_hub.jobs.runner import JobRunner
        from mcp_hub.jobs.store import JobStore

        read_only = SimpleNamespace(read_only_hint=True)

        class Runtime:
            public_tools = [SimpleNamespace(name="logs__query_logs", input_schema={"type": "object"}, annotations=read_only)]
            audit = type("Audit", (), {"redact_content": staticmethod(lambda value, limit=2000: value[:limit])})()

            def __init__(self):
                self.calls = []

            async def call_child_tool(self, name, arguments, request_id, **metadata):
                self.calls.append((name, arguments))
                return CallToolResult(content=[TextContent(type="text", text=f"rows-{len(self.calls)}")])

        class Provider:
            """Asks for the same query twice (argument order differs), then a different one, then answers."""

            def __init__(self):
                self.turns = [
                    ModelTurn("", (ModelToolCall("c1", "logs__query_logs", {"query": "a", "env": "dev"}),)),
                    ModelTurn("", (ModelToolCall("c2", "logs__query_logs", {"env": "dev", "query": "a"}),)),
                    ModelTurn("", (ModelToolCall("c3", "logs__query_logs", {"query": "b", "env": "dev"}),)),
                    ModelTurn("Found it."),
                ]
                self.messages = []

            async def complete(self, messages, tools, **kwargs):
                self.messages = messages
                return self.turns.pop(0)

        provider = Provider()
        agents = {role: AgentConfig(role, None, None) for role in AGENT_ROLES}
        agents["logs"] = AgentConfig("logs", "openai", "test-model", "logs-secret")
        runtime = Runtime()
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite3", protector=FakeProtector())
            runner = JobRunner(
                store=store, runtime=runtime, projects={"orders": ProjectConfig("orders", ("dev",))},
                agents=agents, provider_factory=lambda config: provider,
            )
            try:
                async def exercise():
                    submitted = await runner.submit_issue(project="orders", question="Why?", env="dev")
                    await runner.wait(submitted["job_id"])
                    return submitted["job_id"]

                job_id = asyncio.run(exercise())
                self.assertEqual(runtime.calls, [
                    ("logs__query_logs", {"query": "a", "env": "dev"}),
                    ("logs__query_logs", {"query": "b", "env": "dev"}),
                ])
                job = runner.get_job(job_id)
                events = [event["event"] for event in job["events"]]
                self.assertEqual(events.count("tool.call.succeeded"), 2)
                self.assertEqual(events.count("tool.call.reused"), 1)
                self.assertEqual(len({ref["source_id"] for ref in job["evidence_refs"]}), 2)
                tool_messages = [message for message in provider.messages if message["role"] == "tool"]
                self.assertIn("rows-1", tool_messages[0]["content"])
                self.assertIn("already ran in this job", tool_messages[1]["content"])
                self.assertIn("rows-2", tool_messages[2]["content"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
