from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server import Server, ServerRequestContext
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler, ToolsListChanged
from . import progress
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    GetPromptRequestParams,
    GetPromptResult,
    ListPromptsResult,
    ListToolsResult,
    PaginatedRequestParams,
)

if TYPE_CHECKING:
    from mcp.server.session import ServerSession

BRIDGE_INSTRUCTIONS = "Forwards MCP requests to the authenticated local mcp-hub service."
ProgressSender = progress.ProgressSender


def upstream_instructions(remote: Any) -> str:
    """The instructions the Hub sent in its initialize result, or the bridge one-liner when it sent none."""
    return getattr(remote, "instructions", None) or BRIDGE_INSTRUCTIONS


def progress_relay(ctx: ServerRequestContext) -> ProgressSender | None:
    """A sender that re-sends Hub progress to the stdio client under its own token; None when it sent no token."""
    token = progress.progress_token(ctx.meta)
    return None if token is None else progress.progress_sender(ctx, token)


def upstream_call_options(ctx: ServerRequestContext) -> dict[str, Any]:
    """Keyword arguments for the Hub call; each is passed only when the stdio client sent what it carries."""
    options: dict[str, Any] = {}
    sender = progress_relay(ctx)
    if sender is not None:
        options["progress_callback"] = sender
    meta = progress.forwarded_meta(ctx.meta)
    if meta:
        options["meta"] = meta
    return options


class DownstreamLink:
    """How the bridge reaches its stdio client.

    2026-era clients get tool list changes from a subscriptions/listen stream that
    `ListenHandler` serves from `bus`. 2025-era clients get the session notification.
    The bus is local to this bridge process; decision D6 (one bus) concerns the Hub process.
    The Hub debounces its own changes, so the bridge sends each change at once.
    """

    def __init__(self) -> None:
        self.bus = InMemorySubscriptionBus()
        self._session: ServerSession | None = None

    def bind(self, ctx: ServerRequestContext) -> None:
        self._session = ctx.session

    async def tools_changed(self) -> None:
        """Announce one tool list change on both paths."""
        await self.bus.publish(ToolsListChanged())
        if self._session is not None:
            await self._session.send_tool_list_changed()


def create_bridge_server(remote: Any, link: DownstreamLink | None = None) -> Server:
    """A stdio MCP server that forwards tools and prompts to the Hub client `remote`."""
    downstream = link if link is not None else DownstreamLink()

    async def list_tools(
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        downstream.bind(ctx)
        return await remote.list_tools(cursor=params.cursor if params else None)

    async def call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
        downstream.bind(ctx)
        return await remote.call_tool(params.name, params.arguments or {}, **upstream_call_options(ctx))

    async def list_prompts(
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None,
    ) -> ListPromptsResult:
        downstream.bind(ctx)
        return await remote.list_prompts(cursor=params.cursor if params else None)

    async def get_prompt(ctx: ServerRequestContext, params: GetPromptRequestParams) -> GetPromptResult:
        downstream.bind(ctx)
        return await remote.get_prompt(params.name, params.arguments or {})

    return Server(
        "mcp-hub-stdio-bridge",
        instructions=upstream_instructions(remote),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_prompts=list_prompts,
        on_get_prompt=get_prompt,
        on_subscriptions_listen=ListenHandler(downstream.bus),
    )
