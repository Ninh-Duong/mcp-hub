"""Offline runtime with the real pilot playbook and fake child tools."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from mcp.types import CallToolRequestParams, CallToolResult, TextContent, Tool

from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.planning.playbooks import load_playbooks
from mcp_hub.projects import LogTarget, ProjectConfig
from mcp_hub.server import HubRuntime
from mcp_hub.settings import Settings, PolicySettings


class FakeWorkflowClient:
    def __init__(self) -> None:
        self.calls = []
        self.failure = False
        self.result = None

    async def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, arguments))
        return self.result or CallToolResult(content=[TextContent(type="text", text="evidence")], is_error=self.failure)


def runtime_fixture(root: Path, mode: str = "enforce"):
    playbooks = load_playbooks()
    pilot = playbooks["rcid-bug"]
    servers = {sid: ServerConfig(sid, "python", (), root, {}, ()) for sid in pilot.servers}
    routes = tuple(ToolRoute(name.split("__")[0], "mcp-read-source" if name.startswith("source__") else "mcp-server-logs",
                             name.split("__")[1], name) for name in pilot.tools)
    settings = Settings(policy=PolicySettings(mode=mode, confirm_playbook="off"))
    audit = MagicMock()
    project = ProjectConfig("orders", ("dev", "prod"), "dev", logs={"dev": LogTarget("org", "stream", "request_id")})
    runtime = HubRuntime(HubConfig(root / "hub.json", servers, routes), audit, settings,
                         projects={"orders": project}, playbooks=playbooks)
    client = FakeWorkflowClient()
    for sid in servers:
        tools = {}
        for route in routes:
            if route.server_id == sid:
                props = {"env": {"type": "string"}} if route.feature == "logs" and route.tool_name != "read_ai_context" else {}
                tools[route.tool_name] = Tool(name=route.tool_name, input_schema={"type": "object", "properties": props},
                                              annotations={"readOnlyHint": True})
        runtime.child_connected(sid, client, tools)
    return runtime, client


async def call(runtime, name, arguments):
    return await runtime.call_tool(SimpleNamespace(request_id="test"), CallToolRequestParams(name=name, arguments=arguments))


async def create_plan(runtime):
    result = await call(runtime, "hub__plan", {"task": "Find request failure", "playbook": "rcid-bug", "project": "orders",
                                             "env": "dev", "rcid": "request-1"})
    if result.is_error:
        raise AssertionError(result.content)
    return re.search(r'hub_plan_id="([^"]+)"', result.content[0].text)[1]
