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
                    self.assertTrue(runner.approve_from_dashboard(job_id))
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
            public_tools = [SimpleNamespace(name="logs__query_logs", input_schema={"type": "object"})]

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


if __name__ == "__main__":
    unittest.main()
