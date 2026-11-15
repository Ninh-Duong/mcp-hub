from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from mcp.server import Server, ServerRequestContext
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler, ToolsListChanged
from mcp.types import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    PROTOCOL_VERSION_META_KEY,
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
# The SDK's _meta key for the progress token (RequestParamsMeta.progress_token).
PROGRESS_TOKEN_KEY = "progress_token"
# _meta keys that describe the stdio connection; the Hub connection has its own.
CONNECTION_META_KEYS = frozenset({PROTOCOL_VERSION_META_KEY, CLIENT_INFO_META_KEY, CLIENT_CAPABILITIES_META_KEY})

ProgressSender = Callable[[float, float | None, str | None], Awaitable[None]]


def upstream_instructions(remote: Any) -> str:
    """The instructions the Hub sent in its initialize result, or the bridge one-liner when it sent none."""
    return getattr(remote, "instructions", None) or BRIDGE_INSTRUCTIONS


def forwarded_meta(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """The downstream request's _meta without the connection's own keys and the progress token."""
    skip = CONNECTION_META_KEYS | {PROGRESS_TOKEN_KEY}
    return {key: value for key, value in (meta or {}).items() if key not in skip}


def progress_relay(ctx: ServerRequestContext) -> ProgressSender | None:
    """A sender that re-sends Hub progress to the stdio client under its own token; None when it sent no token."""
    token = (ctx.meta or {}).get(PROGRESS_TOKEN_KEY)
    if token is None:
        return None

    async def send(progress: float, total: float | None, message: str | None) -> None:
        await ctx.session.send_progress_notification(
            token, progress, total, message, related_request_id=ctx.request_id,
        )

    return send


def upstream_call_options(ctx: ServerRequestContext) -> dict[str, Any]:
    """Keyword arguments for the Hub call; each is passed only when the stdio client sent what it carries."""
    options: dict[str, Any] = {}
    progress = progress_relay(ctx)
    if progress is not None:
        options["progress_callback"] = progress
    meta = forwarded_meta(ctx.meta)
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
