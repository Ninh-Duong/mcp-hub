"""Progress and `_meta` forwarding shared by the Hub server and the stdio bridge. Leaf module: no mcp_hub imports.

A client's `_meta` has connection keys (the SDK sets them per connection) and a progress token. The Hub never forwards
the connection keys or the token to a child; the child's progress is relayed under the client's own token.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from mcp.types import CLIENT_CAPABILITIES_META_KEY, CLIENT_INFO_META_KEY, PROTOCOL_VERSION_META_KEY

# The SDK's _meta key for the progress token (RequestParamsMeta.progress_token).
PROGRESS_TOKEN_KEY = "progress_token"
# The client's connection keys; the Hub connection has its own.
CONNECTION_META_KEYS = frozenset({PROTOCOL_VERSION_META_KEY, CLIENT_INFO_META_KEY, CLIENT_CAPABILITIES_META_KEY})

ProgressSender = Callable[[float, float | None, str | None], Awaitable[None]]


def progress_token(meta: Mapping[str, Any] | None) -> Any | None:
    """The client's progress token, or None when it sent none."""
    return (meta or {}).get(PROGRESS_TOKEN_KEY)


def forwarded_meta(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """The downstream request's _meta without the connection keys and the progress token."""
    skip = CONNECTION_META_KEYS | {PROGRESS_TOKEN_KEY}
    return {key: value for key, value in (meta or {}).items() if key not in skip}


def progress_sender(ctx: Any, token: Any) -> ProgressSender:
    """A sender that re-sends progress to the client under its own token, on the request's own stream."""

    async def send(progress: float, total: float | None, message: str | None) -> None:
        await ctx.session.send_progress_notification(
            token, progress, total, message, related_request_id=ctx.request_id,
        )

    return send
