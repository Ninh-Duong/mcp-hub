from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterable, Awaitable, Callable
from contextlib import suppress

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.subscriptions import ListenNotSupportedError
from mcp.server.stdio import stdio_server
from mcp.server.subscriptions import ToolsListChanged
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import ServerEvent
from mcp.types import METHOD_NOT_FOUND, Implementation

from .bridge_proxy import DownstreamLink, create_bridge_server
from .service_config import load_service_config
from .clients import normalize_client_name
from .settings import BridgeSettings

logger = logging.getLogger(__name__)
# The bridge runs without the Hub config, so its tunables take the BridgeSettings defaults.
PRESENCE_RETRY_SECONDS = BridgeSettings().presence_retry_seconds
# Connect and write limits only: a child tool may run longer than 60 s (its own timeoutSeconds applies).
BRIDGE_TIMEOUT = httpx2.Timeout(BridgeSettings().connect_timeout_seconds, read=None)


def hub_http_client(token: str) -> httpx2.AsyncClient:
    """An HTTP client for the Hub on 127.0.0.1 that ignores proxy settings, so no proxy ever sees the token."""
    return httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=BRIDGE_TIMEOUT, trust_env=False)


def bridge_client_info(client_name: str | None = None) -> Implementation:
    name = client_name if client_name is not None else os.environ.get("MCP_HUB_CLIENT_NAME")
    return Implementation(name=normalize_client_name(name), version="0.1.0")


def is_method_not_found(exc: BaseException) -> bool:
    """True when the Hub rejected a request as unknown, possibly wrapped in an exception group."""
    if isinstance(exc, MCPError):
        return exc.code == METHOD_NOT_FOUND
    if isinstance(exc, BaseExceptionGroup):
        return any(is_method_not_found(inner) for inner in exc.exceptions)
    return False


async def relay_tool_changes(
    events: AsyncIterable[ServerEvent],
    on_tools_changed: Callable[[], Awaitable[None]] | None,
) -> None:
    """Call `on_tools_changed` once for each tools/list_changed event; other event kinds are ignored."""
    async for event in events:
        if on_tools_changed is not None and isinstance(event, ToolsListChanged):
            await on_tools_changed()


async def _listen_once(
    url: str,
    token: str,
    client_info: Implementation,
    on_tools_changed: Callable[[], Awaitable[None]] | None = None,
) -> None:
    # No read timeout: the stream stays idle until the Hub ends it.
    async with hub_http_client(token) as http_client:
        async with Client(streamable_http_client(url, http_client=http_client), client_info=client_info) as presence:
            async with presence.listen(tools_list_changed=True) as subscription:
                await relay_tool_changes(subscription, on_tools_changed)


async def hold_presence(
    url: str,
    token: str,
    client_info: Implementation,
    on_tools_changed: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Keep a listen stream open so the desktop status app shows this client as connected, and relay tool changes.

    Stops when the Hub does not serve presence (it runs without the activity UI).
    """
    while True:
        try:
            await _listen_once(url, token, client_info, on_tools_changed)
        except ListenNotSupportedError:
            logger.warning("The Hub does not support subscriptions/listen; tool list changes are not relayed.")
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
    async with hub_http_client(config.token) as http_client:
        async with Client(
            streamable_http_client(url, http_client=http_client),
            client_info=client_info,
        ) as remote:
            await remote.list_tools()
            link = DownstreamLink()
            server = create_bridge_server(remote, link)
            presence = asyncio.create_task(hold_presence(url, config.token, client_info, link.tools_changed))
            try:
                async with stdio_server() as (read_stream, write_stream):
                    await server.run(read_stream, write_stream, server.create_initialization_options())
            finally:
                presence.cancel()
                with suppress(asyncio.CancelledError):
                    await presence


def main() -> None:
    asyncio.run(serve_bridge())
