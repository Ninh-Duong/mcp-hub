from __future__ import annotations

import asyncio
import functools
import logging
import os
import re
import sys
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path
from time import perf_counter
from typing import Any

from mcp_hub.audit import AuditLog, describe_exception
from mcp_hub.child_logs import CappedLog
from mcp_hub.children import Backoff, open_stdio_client
from mcp_hub.config import HUB_ROOT, HubConfig, ServerConfig, ToolRoute
from mcp_hub.server import HubRuntime

FAKE_CHILD = HUB_ROOT / "tests" / "fake_stdio_child.py"
FAST = Backoff(initial=0.05, maximum=0.2)
SLOW = Backoff(initial=30.0, maximum=60.0)  # no retry within a test
WAIT_SECONDS = 20.0


class RecordingAudit(AuditLog):
    """Collects audit events in memory instead of writing the shared audit logger."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._secrets: set[str] = set()
        self.events: list[tuple[str, dict[str, Any]]] = []

    def write(self, event: str, level: int = logging.INFO, **fields: Any) -> None:
        self.events.append((event, fields))

    def named(self, event: str, server: str | None = None) -> list[dict[str, Any]]:
        return [fields for name, fields in self.events
                if name == event and (server is None or fields.get("server") == server)]


def child(server_id: str, connect_timeout: float = 30.0, **env: str) -> ServerConfig:
    return ServerConfig(
        server_id, sys.executable, (str(FAKE_CHILD),), HUB_ROOT, dict(env), (),
        connect_timeout_seconds=connect_timeout,
    )


def route(server_id: str) -> ToolRoute:
    return ToolRoute("feature", server_id, "echo_unicode", f"{server_id}__echo")


async def wait_until(predicate, timeout: float = WAIT_SECONDS) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.02)
    await asyncio.wait_for(poll(), timeout)


def process_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT: still running
    finally:
        kernel32.CloseHandle(handle)


class SupervisedChildTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.audit = RecordingAudit(self.tmp / "audit.jsonl")
        self.stack = AsyncExitStack()
        await self.stack.__aenter__()

    async def asyncTearDown(self) -> None:
        await self.stack.aclose()

    async def start(self, *servers: ServerConfig, backoff: Backoff = FAST, opener=None) -> HubRuntime:
        config = HubConfig(
            self.tmp / "hub.json", {server.server_id: server for server in servers},
            tuple(route(server.server_id) for server in servers),
        )
        runtime = HubRuntime(config, self.audit)
        runtime.child_backoff = backoff
        runtime.child_log_dir = self.tmp / "children"
        if opener is not None:
            runtime.child_opener = opener
        await asyncio.wait_for(runtime.connect(self.stack), WAIT_SECONDS)
        return runtime

    @staticmethod
    async def echo(runtime: HubRuntime, server_id: str, text: str, request_id: str = "r"):
        return await runtime.call_child_tool(f"{server_id}__echo", {"text": text}, request_id=request_id)

    async def test_exited_child_is_reconnected_and_serves_calls_again(self) -> None:
        runtime = await self.start(child("fake", FAKE_CHILD_HOOKS="1"))
        self.assertFalse((await self.echo(runtime, "fake", "before")).is_error)
        self.assertTrue((await self.echo(runtime, "fake", "exit")).is_error)
        await wait_until(lambda: self.audit.named("server.reconnected", "fake"))
        await wait_until(lambda: runtime.child_states()["fake"] == "connected")
        self.assertEqual(self.audit.named("server.disconnected", "fake")[0]["error_type"], "ConnectionClosed")
        result = await self.echo(runtime, "fake", "after")
        self.assertFalse(result.is_error)
        self.assertEqual(result.content[0].text, "naïve café 🌺 after")
        self.assertEqual([tool.name for tool in runtime.public_tools], ["hub__status", "fake__echo"])

    async def test_calls_while_reconnecting_fail_fast_as_unavailable(self) -> None:
        runtime = await self.start(child("fake", FAKE_CHILD_HOOKS="1"), backoff=SLOW)
        await self.echo(runtime, "fake", "exit")
        await wait_until(lambda: runtime.child_states()["fake"] == "reconnecting")
        started = perf_counter()
        result = await self.echo(runtime, "fake", "during", request_id="r-9")
        self.assertLess(perf_counter() - started, 0.5)
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0].text, "Child MCP fake is reconnecting; retry shortly. request_id=r-9")
        failed = self.audit.named("tool.call.failed", "fake")
        self.assertEqual(failed[-1]["error_type"], "ChildUnavailable")
        self.assertEqual(runtime.clients, {})

    async def test_hanging_child_times_out_while_others_connect(self) -> None:
        hang = child("hang", connect_timeout=1.0, FAKE_CHILD_HANG_INITIALIZE="1")
        connecting = asyncio.ensure_future(self.start(hang, child("fake"), backoff=SLOW))
        await asyncio.sleep(0.3)
        self.assertFalse(connecting.done())
        runtime = await connecting
        self.assertEqual(runtime.child_states(), {"hang": "reconnecting", "fake": "connected"})
        self.assertEqual(self.audit.named("server.connect.failed", "hang")[0]["error_type"], "TimeoutError")
        self.assertEqual([route["tool"] for route in self.audit.named("route.skipped", "hang")], ["echo_unicode"])
        self.assertFalse((await self.echo(runtime, "fake", "ok")).is_error)
        pid = int(re.search(r"pid=(\d+)", (self.tmp / "children" / "hang.log").read_text(encoding="utf-8"))[1])
        await wait_until(lambda: not process_alive(pid), timeout=5)

    async def test_invalid_utf8_on_stdout_is_replaced_and_stderr_is_logged(self) -> None:
        runtime = await self.start(child("fake", FAKE_CHILD_HOOKS="1", FAKE_CHILD_STDERR="hello from child"))
        with self.assertLogs("mcp.client.stdio", level=logging.ERROR):  # the replaced line fails to parse
            result = await self.echo(runtime, "fake", "badutf8")
        self.assertFalse(result.is_error)
        self.assertFalse((await self.echo(runtime, "fake", "next")).is_error)
        self.assertEqual(runtime.child_states()["fake"], "connected")
        self.assertEqual(self.audit.named("server.disconnected"), [])
        log = self.tmp / "children" / "fake.log"
        await wait_until(lambda: log.is_file() and "hello from child" in log.read_text(encoding="utf-8"))

    async def test_strict_decoding_failure_is_contained_in_the_child_task(self) -> None:
        strict = functools.partial(open_stdio_client, encoding_error_handler="strict")
        runtime = await self.start(child("fake", FAKE_CHILD_HOOKS="1"), opener=strict)
        self.assertTrue((await self.echo(runtime, "fake", "badutf8-exit")).is_error)
        await wait_until(lambda: self.audit.named("server.reconnected", "fake"))
        await wait_until(lambda: runtime.child_states()["fake"] == "connected")
        # The transport's task-group failure is reported by its leaf cause, not "unhandled errors in a TaskGroup".
        self.assertEqual(self.audit.named("server.disconnected", "fake")[0]["error_type"], "UnicodeDecodeError")
        self.assertFalse((await self.echo(runtime, "fake", "still serving")).is_error)

    async def test_shutdown_stops_children_without_reconnecting(self) -> None:
        runtime = await self.start(child("fake"))
        self.assertEqual(runtime.child_states(), {"fake": "connected"})
        await self.stack.aclose()
        self.assertEqual(runtime.clients, {})
        events = len(self.audit.events)
        await asyncio.sleep(0.3)
        self.assertEqual(self.audit.events[events:], [])

    async def test_failed_first_attempt_is_retried_in_the_background(self) -> None:
        broken = ServerConfig("broken", sys.executable, ("-c", "raise SystemExit(2)"), HUB_ROOT, {}, ())
        runtime = await self.start(broken)
        self.assertEqual(runtime.child_states(), {"broken": "reconnecting"})
        self.assertTrue(self.audit.named("server.connect.failed", "broken"))
        await wait_until(lambda: self.audit.named("server.reconnect.failed", "broken"))
        retry = self.audit.named("server.reconnect.failed", "broken")[0]
        self.assertEqual(retry["retry_in_seconds"], FAST.delay(1))
        self.assertEqual([tool.name for tool in runtime.public_tools], ["hub__status"])


class CappedLogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "children" / "child.log"

    def test_rotates_before_a_write_would_pass_the_cap(self) -> None:
        log = CappedLog(self.path, max_bytes=100)
        log.write(b"a" * 60)
        log.write(b"b" * 60)
        log.close()
        self.assertEqual(self.path.with_name("child.log.1").read_bytes(), b"a" * 60)
        self.assertEqual(self.path.read_bytes(), b"b" * 60)

    def test_oversized_file_is_rotated_at_open_and_long_writes_keep_the_tail(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"x" * 150)
        log = CappedLog(self.path, max_bytes=100)
        log.write(b"0123456789" * 15)
        log.close()
        self.assertEqual(len(self.path.with_name("child.log.1").read_bytes()), 150)
        self.assertEqual(self.path.read_bytes(), (b"0123456789" * 15)[-100:])

    def test_writes_after_close_are_dropped(self) -> None:
        log = CappedLog(self.path, max_bytes=100)
        log.close()
        log.write(b"late")
        self.assertFalse(self.path.exists())


class DescribeExceptionTest(unittest.TestCase):
    def test_exception_group_is_unwrapped_to_its_first_leaf(self) -> None:
        group = ExceptionGroup("unhandled errors in a TaskGroup", [
            ExceptionGroup("inner", [UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")]),
            ValueError("second"),
        ])
        error_type, message = describe_exception(group)
        self.assertEqual(error_type, "UnicodeDecodeError")
        self.assertIn("invalid start byte", message)

    def test_plain_exception_is_described_as_is(self) -> None:
        self.assertEqual(describe_exception(TimeoutError("slow")), ("TimeoutError", "slow"))


class BackoffTest(unittest.TestCase):
    def test_delay_doubles_up_to_the_cap(self) -> None:
        self.assertEqual([Backoff().delay(n) for n in range(8)], [1, 2, 4, 8, 16, 32, 60, 60])
        self.assertEqual(Backoff().delay(10_000), 60)


if __name__ == "__main__":
    unittest.main()
