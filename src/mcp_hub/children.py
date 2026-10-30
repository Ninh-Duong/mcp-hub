"""Per-child supervision of stdio child MCPs.

Each enabled child runs in its own asyncio task that owns its MCP client, transport
task groups, and stderr log, so a child failure (a crash, a closed session, a reader
error) ends only that task's session and never reaches the service task or another
child. The supervisor publishes a live client through `on_connected`, withdraws it
through `on_lost`, and reconnects with exponential backoff until it is stopped.

States: `connecting` (first attempt), `connected`, `reconnecting` (after a failed
attempt or a lost session).
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, AsyncIterator, Literal, TextIO

import anyio
from mcp import Client, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.types import Tool

from .audit import AuditLog, describe_exception
from .child_logs import CappedLog, pipe_into
from .config import ServerConfig

CONNECTING = "connecting"
CONNECTED = "connected"
RECONNECTING = "reconnecting"
# error_type of `server.disconnected` when the session ended without an exception (e.g. the child exited).
CONNECTION_CLOSED = "ConnectionClosed"

ChildOpener = Callable[[ServerConfig, TextIO, Callable[[], None]], AbstractAsyncContextManager[Client]]
ConnectedCallback = Callable[[str, Client, dict[str, Tool]], None]
LostCallback = Callable[[str], None]


class ChildUnavailable(RuntimeError):
    """A routed call reached a child that has no live session (connecting or reconnecting)."""


@dataclass(frozen=True)
class Backoff:
    """Reconnect delays: `initial` seconds, doubled after each failed attempt, at most `maximum`.

    A session counts as a failed attempt unless it stayed up for `maximum` seconds, so a
    child that crashes right after connecting is not restarted every `initial` seconds.
    """

    initial: float = 1.0
    maximum: float = 60.0

    def delay(self, failures: int) -> float:
        return min(self.maximum, self.initial * 2 ** min(failures, 32))


def child_environment(config: ServerConfig) -> dict[str, str]:
    """The environment a child process gets: the MCP SDK's safe defaults, its `inheritEnv` names, and its `env`."""
    env = get_default_environment()
    env.update({name: os.environ[name] for name in config.inherit_env if name in os.environ})
    env.update(config.env)
    return env


def open_stdio_client(
    config: ServerConfig,
    errlog: TextIO,
    on_closed: Callable[[], None],
    *,
    encoding_error_handler: Literal["strict", "ignore", "replace"] = "replace",
) -> Client:
    """A not-yet-entered MCP client for one stdio child; `on_closed` fires when its session ends.

    Undecodable child stdout is replaced instead of crashing the transport reader.
    """
    params = StdioServerParameters(
        command=config.command,
        args=list(config.args),
        env=child_environment(config),
        cwd=str(config.cwd),
        encoding_error_handler=encoding_error_handler,
    )
    return Client(_watched(stdio_client(params, errlog=errlog), on_closed))


@asynccontextmanager
async def _watched(transport: AbstractAsyncContextManager[Any], on_closed: Callable[[], None]) -> AsyncIterator[Any]:
    async with transport as (read_stream, write_stream):
        yield _ClosingReadStream(read_stream, on_closed), write_stream


class _ClosingReadStream:
    """A transport read stream that reports when the session stops reading it (EOF, error, or close)."""

    def __init__(self, inner: Any, on_closed: Callable[[], None]) -> None:
        self._inner = inner
        self._on_closed = on_closed

    @property
    def last_context(self) -> Any:
        return getattr(self._inner, "last_context", None)

    async def receive(self) -> Any:
        return await self._inner.receive()

    def __aiter__(self) -> _ClosingReadStream:
        return self

    async def __anext__(self) -> Any:
        return await self._inner.__anext__()

    async def aclose(self) -> None:
        self._on_closed()
        await self._inner.aclose()

    async def __aenter__(self) -> _ClosingReadStream:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None,
    ) -> None:
        await self.aclose()


async def list_all_tools(client: Client) -> dict[str, Tool]:
    found: dict[str, Tool] = {}
    page = await client.list_tools()
    while True:
        for tool in page.tools:
            found[tool.name] = tool
        cursor = getattr(page, "next_cursor", None)
        if not cursor:
            return found
        page = await client.list_tools(cursor=cursor)


class ChildSupervisor:
    """Keeps one child MCP connected in its own task; see the module docstring."""

    def __init__(
        self,
        config: ServerConfig,
        audit: AuditLog,
        on_connected: ConnectedCallback,
        on_lost: LostCallback,
        *,
        log_dir: Path,
        opener: ChildOpener = open_stdio_client,
        backoff: Backoff = Backoff(),
    ) -> None:
        self.config = config
        self.state = CONNECTING
        self._audit = audit
        self._on_connected = on_connected
        self._on_lost = on_lost
        self._opener = opener
        self._backoff = backoff
        self._stderr_log = CappedLog(log_dir / f"{config.server_id}.log")
        self._first_attempt_done = asyncio.Event()
        self._scope = anyio.CancelScope()
        self._task: asyncio.Task[None] | None = None
        self._connected_at = 0.0

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"mcp-hub-child-{self.config.server_id}")

    async def wait_first_attempt(self) -> None:
        """Return once the first connection attempt has connected, failed, or timed out."""
        await self._first_attempt_done.wait()

    async def stop(self) -> None:
        """Close the session and its process; no reconnect is attempted afterwards."""
        self._scope.cancel()
        if self._task is not None:
            with suppress(asyncio.CancelledError):
                await self._task
        self._stderr_log.close()

    async def _run(self) -> None:
        failures = 0
        try:
            with self._scope:
                while True:
                    was_connected = await self._attempt(failures)
                    stable = was_connected and time.monotonic() - self._connected_at >= self._backoff.maximum
                    failures = 0 if stable else failures + 1
                    self.state = RECONNECTING
                    self._first_attempt_done.set()
                    await anyio.sleep(self._backoff.delay(max(failures - 1, 0)))
        finally:
            self._first_attempt_done.set()  # startup never waits on a supervisor that ended

    async def _attempt(self, failures: int) -> bool:
        """One session: start, handshake, list tools, then serve until it ends. Returns whether it connected."""
        lost = anyio.Event()
        stage, connected, error = "connect", False, None
        errlog = pipe_into(self._stderr_log, self.config.server_id)
        try:
            with anyio.fail_after(self.config.connect_timeout_seconds) as deadline:
                async with self._opener(self.config, errlog, lost.set) as client:
                    stage = "list_tools"
                    tools = await list_all_tools(client)
                    deadline.deadline = math.inf
                    connected = True
                    self._publish(client, tools)
                    await lost.wait()
        except Exception as exc:
            error = exc
        finally:
            errlog.close()
            if connected:
                self._on_lost(self.config.server_id)
        if connected:
            self._report_lost(error)
        else:
            self._report_failed(stage, error, failures)
        return connected

    def _publish(self, client: Client, tools: dict[str, Tool]) -> None:
        event = "server.connected" if self.state == CONNECTING else "server.reconnected"
        self.state = CONNECTED
        self._connected_at = time.monotonic()
        self._audit.write(event, server=self.config.server_id, tools_count=len(tools))
        self._first_attempt_done.set()
        self._on_connected(self.config.server_id, client, tools)

    def _report_lost(self, error: BaseException | None) -> None:
        self.state = RECONNECTING
        fields = {"error_type": CONNECTION_CLOSED} if error is None else _error_fields(error)
        self._audit.write("server.disconnected", level=logging.WARNING, server=self.config.server_id, **fields)

    def _report_failed(self, stage: str, error: BaseException | None, failures: int) -> None:
        fields = _error_fields(error) if error is not None else {"error_type": CONNECTION_CLOSED}
        if self.state == CONNECTING:
            event = "server.connect.failed" if stage == "connect" else "server.list_tools.failed"
            self._audit.write(event, level=logging.ERROR, server=self.config.server_id, **fields)
            return
        self._audit.write(
            "server.reconnect.failed", level=logging.WARNING, server=self.config.server_id,
            retry_in_seconds=self._backoff.delay(failures), **fields,
        )


def _error_fields(error: BaseException) -> dict[str, str]:
    error_type, message = describe_exception(error)
    return {"error_type": error_type, "error": message}
