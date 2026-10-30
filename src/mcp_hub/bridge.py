from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.subscriptions import ListenNotSupportedError
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from mcp.types import METHOD_NOT_FOUND, Implementation
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

PRESENCE_RETRY_SECONDS = 5.0
# Connect and write limits only: a child tool may run longer than 60 s (its own timeoutSeconds applies).
BRIDGE_TIMEOUT = httpx2.Timeout(60.0, read=None)


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


def is_method_not_found(exc: BaseException) -> bool:
    """True when the Hub rejected a request as unknown, possibly wrapped in an exception group."""
    if isinstance(exc, MCPError):
        return exc.code == METHOD_NOT_FOUND
    if isinstance(exc, BaseExceptionGroup):
        return any(is_method_not_found(inner) for inner in exc.exceptions)
    return False


async def _listen_once(url: str, token: str, client_info: Implementation) -> None:
    # No read timeout: the stream stays idle until the Hub ends it.
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=BRIDGE_TIMEOUT) as http_client:
        async with Client(streamable_http_client(url, http_client=http_client), client_info=client_info) as presence:
            async with presence.listen(tools_list_changed=True) as subscription:
                async for _event in subscription:
                    pass


async def hold_presence(url: str, token: str, client_info: Implementation) -> None:
    """Keep a listen stream open so the Hub dashboard shows this client as connected.

    Stops when the Hub does not serve presence (it runs without the activity UI).
    """
    while True:
        try:
            await _listen_once(url, token, client_info)
        except ListenNotSupportedError:
            return
        except Exception as exc:
            if is_method_not_found(exc):
                return
            # Hub restarting or briefly unreachable; retry below.
        await asyncio.sleep(PRESENCE_RETRY_SECONDS)


async def serve_bridge() -> None:
    config = load_service_config(create=False)
    url = f"http://{config.host}:{config.port}/mcp"
    client_info = bridge_client_info()
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {config.token}"},
        timeout=BRIDGE_TIMEOUT,
    ) as http_client:
        async with Client(
            streamable_http_client(url, http_client=http_client),
            client_info=client_info,
        ) as remote:
            await remote.list_tools()
            server = create_bridge_server(remote)
            presence = asyncio.create_task(hold_presence(url, config.token, client_info))
            try:
                async with stdio_server() as (read_stream, write_stream):
                    await server.run(read_stream, write_stream, server.create_initialization_options())
            finally:
                presence.cancel()
                with suppress(asyncio.CancelledError):
                    await presence


def main() -> None:
    asyncio.run(serve_bridge())
