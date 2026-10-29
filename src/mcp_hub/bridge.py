from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import Implementation
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    GetPromptRequestParams,
    GetPromptResult,
    ListPromptsResult,
    ListToolsResult,
    PaginatedRequestParams,
)

from .service_config import load_service_config
from .server import normalize_client_name


def bridge_client_info(client_name: str | None = None) -> Implementation:
    name = client_name if client_name is not None else os.environ.get("MCP_HUB_CLIENT_NAME")
    return Implementation(name=normalize_client_name(name), version="0.1.0")


def create_bridge_server(remote: Any) -> Server:
    async def list_tools(
        _ctx: ServerRequestContext,
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        return await remote.list_tools(cursor=params.cursor if params else None)

    async def call_tool(_ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
        return await remote.call_tool(params.name, params.arguments or {})

    async def list_prompts(
        _ctx: ServerRequestContext,
        params: PaginatedRequestParams | None,
    ) -> ListPromptsResult:
        return await remote.list_prompts(cursor=params.cursor if params else None)

    async def get_prompt(_ctx: ServerRequestContext, params: GetPromptRequestParams) -> GetPromptResult:
        return await remote.get_prompt(params.name, params.arguments or {})

    return Server(
        "mcp-hub-stdio-bridge",
        instructions="Forwards MCP requests to the authenticated local mcp-hub service.",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_prompts=list_prompts,
        on_get_prompt=get_prompt,
    )


async def serve_bridge() -> None:
    config = load_service_config(create=False)
    url = f"http://{config.host}:{config.port}/mcp"
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {config.token}"},
        timeout=60.0,
    ) as http_client:
        async with Client(
            streamable_http_client(url, http_client=http_client),
            client_info=bridge_client_info(),
        ) as remote:
            await remote.list_tools()
            server = create_bridge_server(remote)
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    asyncio.run(serve_bridge())
