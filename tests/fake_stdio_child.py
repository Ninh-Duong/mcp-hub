"""A minimal stdio child MCP for tests and the call-path benchmark.

Test-only behaviors, controlled by environment variables:
- FAKE_CHILD_STDERR: a line written to stderr at start.
- FAKE_CHILD_HANG_INITIALIZE=1: write `pid=<pid>` to stderr and never answer (not even initialize).
- FAKE_CHILD_HOOKS=1: `echo_unicode` with text `exit` exits the process; `badutf8` writes invalid
  UTF-8 to stdout before answering; `badutf8-exit` writes invalid UTF-8 and exits.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

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


# The protocol pipe, duplicated before the SDK diverts fd 1 to stderr while serving.
_WIRE: int | None = None


def _run_hook(text: str) -> None:
    if text in ("badutf8", "badutf8-exit") and _WIRE is not None:
        os.write(_WIRE, bytes([0xFF, 0xFE]) + b" not utf-8 " + bytes([0xC3]) + b"\n")
    if text in ("exit", "badutf8-exit"):
        os._exit(3)


async def call_tool(_ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
    if os.environ.get("FAKE_CHILD_HOOKS") == "1":
        _run_hook(params.arguments["text"])
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
    global _WIRE
    if os.environ.get("FAKE_CHILD_HOOKS") == "1":
        _WIRE = os.dup(1)
    if os.environ.get("FAKE_CHILD_STDERR"):
        print(os.environ["FAKE_CHILD_STDERR"], file=sys.stderr, flush=True)
    if os.environ.get("FAKE_CHILD_HANG_INITIALIZE") == "1":
        print(f"pid={os.getpid()}", file=sys.stderr, flush=True)
        time.sleep(3600)
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
