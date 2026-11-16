"""Capture the Hub's external surface as sorted JSON-ready data (the refactor contract, FR-010).

Surfaces: MCP tools (names, descriptions, schemas, annotations) as an SDK client sees them, with one fake child;
HTTP routes; audit event names; settings keys with their defaults; command-line entry points.

Nothing here reads config/private/ or logs/: tool pins go to a temporary private root, the audit log to a temporary
file, and the child is an in-memory SDK server. Run `python -m tests.surface_capture` to rewrite the baseline in
tests/contract_baseline/ (only when a change is intended and explained).
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import tempfile
import tomllib
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from mcp import Client
from mcp.server import Server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent, Tool

from mcp_hub import audit_events
from mcp_hub import config as hub_config
from mcp_hub.audit import AuditLog
from mcp_hub.config import HUB_ROOT, HubConfig, ServerConfig, ToolRoute
from mcp_hub.http import create_http_app
from mcp_hub.server import HubRuntime, create_mcp_server
from mcp_hub.settings import PolicySettings, ProtocolSettings, Settings

BASELINE_DIR = HUB_ROOT / "tests" / "contract_baseline"
ALPHA = "alpha-server"
ROUTE = ToolRoute("feature", ALPHA, "count", "alpha-server__count")
COUNT_SCHEMA = {
    "type": "object",
    "properties": {"count": {"type": "integer", "minimum": 0}, "label": {"type": "string"}},
    "required": ["count"],
}
COUNT_TOOL = Tool(name="count", input_schema=COUNT_SCHEMA, annotations={"readOnlyHint": True, "openWorldHint": False})
# The modules that scripts/hubctl.ps1 and the desktop launcher start with `python -m`.
MODULE_ENTRY_POINTS = ("mcp_hub.desktop", "mcp_hub.preflight", "mcp_hub.service", "mcp_hub.update_runner")


class SurfaceFakeChild:
    """An SDK server that lists one tool and answers it; the Hub sees it as a real child session."""

    def __init__(self) -> None:
        self.server = Server("fake-child", on_list_tools=self._list_tools, on_call_tool=self._call_tool)

    async def _list_tools(self, ctx: Any, params: Any) -> ListToolsResult:
        return ListToolsResult(tools=[COUNT_TOOL])

    async def _call_tool(self, ctx: Any, params: CallToolRequestParams) -> CallToolResult:
        return CallToolResult(content=[TextContent(type="text", text='{"count": 0}')])

    def client(self, message_handler: Any) -> Client:
        return Client(self.server, mode="legacy", message_handler=message_handler)


def _tool_json(tool: Tool) -> dict[str, Any]:
    return json.loads(tool.model_dump_json(by_alias=True, exclude_none=True))


def _build_runtime(root: Path) -> tuple[HubRuntime, AuditLog]:
    audit = AuditLog(root / "audit.jsonl")
    child = ServerConfig(ALPHA, "python", (), root, {}, (), timeout_seconds=60.0)
    config = HubConfig(root / "hub.json", {ALPHA: child}, (ROUTE,))
    settings = Settings(policy=PolicySettings(mode="off"), protocol=ProtocolSettings(list_changed_debounce_seconds=0.01))
    runtime = HubRuntime(config, audit, settings)
    runtime.enable_core_condenser()
    runtime.child_log_dir = root / "children"
    return runtime, audit


async def _list_hub_tools(root: Path) -> list[dict[str, Any]]:
    runtime, audit = _build_runtime(root)
    fake = SurfaceFakeChild()
    runtime.child_opener = lambda config, errlog, on_closed, *, message_handler: fake.client(message_handler)
    try:
        async with AsyncExitStack() as stack:
            await runtime.connect(stack)
            server = create_mcp_server(runtime)
            async with Client(server, mode="auto") as client:
                listed = await client.list_tools()
    finally:
        runtime.events.close()
        audit.close()
    return sorted((_tool_json(tool) for tool in listed.tools), key=lambda tool: tool["name"])


def capture_tools() -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp, patch.object(hub_config, "PRIVATE_ROOT", Path(tmp)):
        return asyncio.run(_list_hub_tools(Path(tmp)))


def _route_rows(routes: list[Any]) -> list[dict[str, Any]]:
    return [
        {"path": getattr(route, "path", ""), "methods": sorted(getattr(route, "methods", None) or []),
         "name": getattr(route, "name", "")}
        for route in routes
    ]


def capture_routes() -> list[dict[str, Any]]:
    """Every route the service serves: the MCP app, the dashboard, the activity UI, and the BitNet setup routes."""
    from mcp_hub.activity_ui.routes import ActivityRoutes
    from mcp_hub.bitnet.processing import ProcessingFeed
    from mcp_hub.bitnet.setup import BitnetSetup
    from mcp_hub.dashboard.api import create_dashboard_routes
    from mcp_hub.settings import BitnetSettings

    app = create_http_app(Server("route-probe"), token="t" * 32, admin_token="a" * 32)
    runtime = SimpleNamespace(audit=None)
    bitnet = BitnetSetup(Path("."), BitnetSettings(), None, processing_feed=ProcessingFeed())
    extra = [*create_dashboard_routes(runtime), *ActivityRoutes(runtime, None, None).routes(), *bitnet.routes()]
    rows = _route_rows(list(app.routes)) + _route_rows(extra)
    return sorted(rows, key=lambda row: (row["path"], row["methods"], row["name"]))


def capture_audit_events() -> dict[str, str]:
    return {name: value for name, value in sorted(vars(audit_events).items()) if name.isupper() and isinstance(value, str)}


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if dataclasses.is_dataclass(value):
        for field in dataclasses.fields(value):
            key = f"{prefix}.{field.name}" if prefix else field.name
            _flatten(key, getattr(value, field.name), out)
    else:
        out[prefix] = value


def capture_settings() -> dict[str, Any]:
    out: dict[str, Any] = {}
    _flatten("", Settings(), out)
    return {key: out[key] for key in sorted(out)}


def capture_entry_points() -> dict[str, Any]:
    pyproject = tomllib.loads((HUB_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    return {"console_scripts": {name: scripts[name] for name in sorted(scripts)}, "modules": list(MODULE_ENTRY_POINTS)}


def capture_surface() -> dict[str, Any]:
    return {
        "tools.json": capture_tools(),
        "routes.json": capture_routes(),
        "audit_events.json": capture_audit_events(),
        "settings.json": capture_settings(),
        "entry_points.json": capture_entry_points(),
    }


def write_baseline(directory: Path = BASELINE_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in capture_surface().items():
        path = directory / name
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for written in write_baseline():
        print(written.relative_to(HUB_ROOT).as_posix())
    sys.exit(0)
