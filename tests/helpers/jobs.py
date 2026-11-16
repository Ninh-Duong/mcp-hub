"""Shared test fakes (jobs). Test modules import them from here instead of from each other."""
from __future__ import annotations

from __future__ import annotations
import tempfile
from contextlib import contextmanager
from pathlib import Path
from mcp.types import CallToolResult, TextContent
from tests.helpers.fakes import FakeProtector


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


@contextmanager
def _runner(runtime, providers: dict, *, clock=None, **limits):
    from mcp_hub.agents.config import AGENT_ROLES, AgentConfig
    from mcp_hub.projects import ProjectConfig
    from mcp_hub.jobs.runner import JobRunner
    from mcp_hub.jobs.store import JobStore
    from mcp_hub.settings import JobSettings

    agents = {role: AgentConfig(role, None, None) for role in AGENT_ROLES}
    for role in providers:
        agents[role] = AgentConfig(role, "openai", "test-model", f"{role}-test-key")
    with tempfile.TemporaryDirectory() as directory:
        options = {"clock": clock} if clock else {}
        store = JobStore(Path(directory) / "jobs.sqlite3", protector=FakeProtector(), **options)
        try:
            yield JobRunner(
                store=store, runtime=runtime, projects={"orders": ProjectConfig("orders", ("dev",))},
                agents=agents, provider_factory=lambda config: providers[config.name], limits=JobSettings(**limits),
            )
        finally:
            store.close()
