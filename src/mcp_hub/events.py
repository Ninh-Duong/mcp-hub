"""The Hub's one event bus for `subscriptions/listen` streams.

`HubEvents` owns the SDK's in-memory subscription bus and the listen handler that serves
`subscriptions/listen` on top of it. The server hands `listen_handler` to the MCP server;
other publishers (the activity UI, for one) reuse the same bus instead of registering a
second listen handler.

`tools_changed` publishes one `ToolsListChanged` after a burst of changes: each new
signature restarts a timer of `debounce_seconds`, and only the last one publishes.
"""
from __future__ import annotations

import asyncio

from collections.abc import Callable

from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler, ToolsListChanged


class HubEvents:
    """Debounced `notifications/tools/list_changed` publisher on one subscription bus.

    `on_publish`, when given, is called once after each publish (not once per scheduled change), so an audit line
    written there matches what was sent.
    """

    def __init__(self, debounce_seconds: float, on_publish: Callable[[], None] | None = None) -> None:
        self.bus = InMemorySubscriptionBus()
        self.listen_handler = ListenHandler(self.bus)
        self._debounce_seconds = debounce_seconds
        self._on_publish = on_publish
        self._signature: str | None = None
        self._pending: asyncio.Task[None] | None = None

    def prime(self, signature: str) -> None:
        """Record the current tool signature as the baseline; never publishes."""
        self._signature = signature

    async def tools_changed(self, signature: str) -> bool:
        """Record `signature`; return True when a publish was scheduled (it differs from the last one)."""
        if signature == self._signature:
            return False
        self._signature = signature
        self._cancel_pending()
        self._pending = asyncio.create_task(self._publish_after_debounce())
        return True

    def close(self) -> None:
        """Cancel a pending publish; no event is sent after this."""
        self._cancel_pending()

    def _cancel_pending(self) -> None:
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None

    async def _publish_after_debounce(self) -> None:
        await asyncio.sleep(self._debounce_seconds)
        await self.bus.publish(ToolsListChanged())
        if self._on_publish is not None:
            self._on_publish()
