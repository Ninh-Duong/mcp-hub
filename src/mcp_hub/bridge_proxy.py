from __future__ import annotations

from typing import Any

from mcp.server import Server, ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    GetPromptRequestParams,
    GetPromptResult,
    ListPromptsResult,
    ListToolsResult,
    PaginatedRequestParams,
)


def create_bridge_server(remote: Any) -> Server:
    """A stdio MCP server that forwards tools and prompts to the Hub client `remote`."""

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
