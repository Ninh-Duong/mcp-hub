from __future__ import annotations

import logging
import os
from contextlib import AsyncExitStack
from time import perf_counter
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

from .audit import AuditLog, redact
from .config import HubConfig, ServerConfig, ToolRoute, load_config


class HubRuntime:
    def __init__(self, config: HubConfig, audit: AuditLog) -> None:
        self.config = config
        self.audit = audit
        self.clients: dict[str, Client] = {}
        self.routes: dict[str, ToolRoute] = {route.exposed_name: route for route in config.routes}
        self.public_tools: list[Tool] = []

    async def connect(self, stack: AsyncExitStack) -> None:
        discovered: dict[str, dict[str, Tool]] = {}
        for server_id, server_config in self.config.servers.items():
            self.audit.add_secrets(server_config.env.values())
            self.audit.add_secrets(server_config.secret_values)
            client = await self._connect_server(stack, server_config)
            self.clients[server_id] = client
            discovered[server_id] = await self._list_all_tools(client)
            self.audit.write(
                "server.connected",
                server=server_id,
                tools_count=len(discovered[server_id]),
            )

        for route in self.config.routes:
            child_tool = discovered[route.server_id].get(route.tool_name)
            if child_tool is None:
                raise RuntimeError(f"Configured tool was not found: {route.server_id}/{route.tool_name}")
            description = child_tool.description or ""
            public_description = f"[{route.feature} via {route.server_id}] {description}".strip()
            fields: dict[str, Any] = {
                "name": route.exposed_name,
                "description": public_description,
                "input_schema": child_tool.input_schema,
            }
            if getattr(child_tool, "title", None):
                fields["title"] = child_tool.title
            for field in ("output_schema", "annotations", "icons"):
                value = getattr(child_tool, field, None)
                if value is not None:
                    fields[field] = value
            self.public_tools.append(Tool(**fields))

        self.audit.write(
            "hub.tools.registered",
            tool_count=len(self.public_tools),
            tools=[tool.name for tool in self.public_tools],
        )

    async def _connect_server(self, stack: AsyncExitStack, config: ServerConfig) -> Client:
        child_env = {name: os.environ[name] for name in config.inherit_env if name in os.environ}
        child_env.update(config.env)
        params = StdioServerParameters(
            command=config.command,
            args=list(config.args),
            env=child_env,
            cwd=str(config.cwd),
        )
        child_stderr = open(os.devnull, "w", encoding="utf-8")
        stack.callback(child_stderr.close)
        client = Client(stdio_client(params, errlog=child_stderr))
        try:
            await stack.enter_async_context(client)
            return client
        except Exception as exc:
            self.audit.write(
                "server.connect.failed",
                level=logging.ERROR,
                server=config.server_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise RuntimeError(f"Could not connect to child MCP '{config.server_id}' ({type(exc).__name__})") from exc

    @staticmethod
    async def _list_all_tools(client: Client) -> dict[str, Tool]:
        found: dict[str, Tool] = {}
        page = await client.list_tools()
        while True:
            for tool in page.tools:
                found[tool.name] = tool
            cursor = getattr(page, "next_cursor", None)
            if not cursor:
                return found
            page = await client.list_tools(cursor=cursor)

    async def list_tools(
        self,
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        self.audit.write(
            "hub.tools.listed",
            request_id=str(ctx.request_id) if ctx.request_id is not None else None,
            tool_count=len(self.public_tools),
        )
        return ListToolsResult(tools=self.public_tools)

    async def call_tool(self, ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
        request_id = str(ctx.request_id) if ctx.request_id is not None else "unknown"
        route = self.routes.get(params.name)
        if route is None:
            self.audit.write(
                "tool.call.denied",
                level=logging.WARNING,
                request_id=request_id,
                tool=params.name,
            )
            return CallToolResult(
                content=[TextContent(type="text", text="This tool is not enabled in the Hub configuration.")],
                is_error=True,
            )

        started = perf_counter()
        self.audit.write(
            "tool.call.started",
            request_id=request_id,
            feature=route.feature,
            server=route.server_id,
            tool=route.tool_name,
        )
        try:
            result = await self.clients[route.server_id].call_tool(route.tool_name, params.arguments or {})
        except Exception as exc:
            self.audit.write(
                "tool.call.failed",
                level=logging.ERROR,
                request_id=request_id,
                feature=route.feature,
                server=route.server_id,
                tool=route.tool_name,
                duration_ms=round((perf_counter() - started) * 1000),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return CallToolResult(
                content=[TextContent(type="text", text=f"Child MCP call failed. See Hub log request_id={request_id}.")],
                is_error=True,
            )

        error_summary = _result_error_summary(result)
        failed = bool(getattr(result, "is_error", False)) or error_summary.startswith(("❌", "Error:"))
        self.audit.write(
            "tool.call.failed" if failed else "tool.call.succeeded",
            level=logging.ERROR if failed else logging.INFO,
            request_id=request_id,
            feature=route.feature,
            server=route.server_id,
            tool=route.tool_name,
            duration_ms=round((perf_counter() - started) * 1000),
            **({"error_summary": error_summary} if failed and error_summary else {}),
        )
        return result


def _result_error_summary(result: CallToolResult) -> str:
    blocks = getattr(result, "content", ()) or ()
    for block in blocks:
        if isinstance(block, TextContent) and block.text.strip():
            return redact(block.text.strip())
    return ""


async def serve() -> None:
    audit = AuditLog()
    try:
        config = load_config()
    except Exception as exc:
        audit.write("config.load.failed", level=logging.ERROR, error_type=type(exc).__name__, error=str(exc))
        raise

    runtime = HubRuntime(config, audit)
    async with AsyncExitStack() as stack:
        await runtime.connect(stack)
        server = Server(
            "mcp-hub",
            instructions="Config-driven MCP hub. Only the listed, allowlisted child tools are available.",
            on_list_tools=runtime.list_tools,
            on_call_tool=runtime.call_tool,
        )
        audit.write(
            "hub.started",
            config=str(config.config_path),
            servers=list(config.servers),
            tool_count=len(runtime.public_tools),
        )
        try:
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
        finally:
            audit.write("hub.stopped")


def main() -> None:
    import asyncio

    asyncio.run(serve())
