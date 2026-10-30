from __future__ import annotations

import asyncio

from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    GetPromptRequestParams,
    GetPromptResult,
    ListPromptsResult,
    ListToolsResult,
    PaginatedRequestParams,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
    Tool,
)


async def list_tools(_ctx: ServerRequestContext, _params: PaginatedRequestParams | None) -> ListToolsResult:
    return ListToolsResult(tools=[Tool(
        name="echo_unicode",
        description="Echo a string with Unicode text.",
        inputSchema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )])


async def call_tool(_ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=f"naïve café 🌺 {params.arguments['text']}")])


async def list_prompts(_ctx: ServerRequestContext, _params: PaginatedRequestParams | None) -> ListPromptsResult:
    return ListPromptsResult(prompts=[Prompt(
        name="child-guide",
        description="Fake child prompt for stdio integration tests.",
        arguments=[PromptArgument(name="task", required=True)],
    )])


async def get_prompt(_ctx: ServerRequestContext, params: GetPromptRequestParams) -> GetPromptResult:
    task = (params.arguments or {}).get("task", "")
    return GetPromptResult(messages=[PromptMessage(role="user", content=TextContent(type="text", text=f"Child task: {task}"))])


async def main() -> None:
    server = Server(
        "fake-child",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_prompts=list_prompts,
        on_get_prompt=get_prompt,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
