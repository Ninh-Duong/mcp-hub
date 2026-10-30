"""Measure the shared Hub's tool-call latency with the activity UI off (default) and on.

Starts the Hub HTTP app in process on a free loopback port, with tests/fake_stdio_child.py as its only child,
and times sequential `tools/call` round trips from one MCP client. Offline; nothing outside a temp folder is written.

    uv run --no-sync python scripts/bench_call_path.py [--calls 500]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import statistics
import sys
import tempfile
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Any, AsyncIterator

import httpx2
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.http import create_http_app
from mcp_hub.jobs.store import JobStore
from mcp_hub.server import HubRuntime, create_mcp_server

HUB_ROOT = Path(__file__).resolve().parents[1]
FAKE_CHILD = HUB_ROOT / "tests" / "fake_stdio_child.py"
TOKEN = "bench-local-token"
TOOL = "fake__echo_unicode"
WARMUP_CALLS = 20


class PlainProtector:
    """The benchmark stores no secrets; skip DPAPI so it runs anywhere."""

    def protect(self, data: bytes) -> bytes:
        return data

    def unprotect(self, data: bytes) -> bytes:
        return data


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def hub_config(root: Path) -> HubConfig:
    child = ServerConfig("fake", sys.executable, (str(FAKE_CHILD),), HUB_ROOT, {}, ())
    return HubConfig(root / "hub.json", {"fake": child}, (ToolRoute("bench", "fake", "echo_unicode", TOOL),))


@asynccontextmanager
async def serving(app: Any, port: int) -> AsyncIterator[str]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical", access_log=False))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.02)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await task


async def time_calls(url: str, calls: int) -> list[float]:
    """Milliseconds per sequential call, after a short warm-up."""
    durations: list[float] = []
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60.0) as http_client:
        async with Client(
            streamable_http_client(url, http_client=http_client), client_info=Implementation(name="bench", version="1"),
        ) as client:
            for index in range(WARMUP_CALLS + calls):
                started = perf_counter()
                await client.call_tool(TOOL, {"text": f"call {index}"})
                if index >= WARMUP_CALLS:
                    durations.append((perf_counter() - started) * 1000)
    return durations


async def _drain(queue: asyncio.Queue) -> None:
    while True:
        await queue.get()


async def run_mode(root: Path, ui_on: bool, calls: int) -> list[float]:
    audit = AuditLog(root / "audit.jsonl")
    runtime = HubRuntime(hub_config(root), audit)
    store = JobStore(root / f"jobs-{'ui' if ui_on else 'fast'}.sqlite3", protector=PlainProtector())
    try:
        async with AsyncExitStack() as stack:
            await runtime.connect(stack)
            server = create_mcp_server(runtime)
            ui = None
            if ui_on:
                from mcp_hub.activity_ui import install  # the extension, as service.py installs it with -Ui

                ui = install(runtime, store=store, audit=audit)
                ui.attach_server(server)
                ui.start()
                # An open dashboard tab: one live-stream subscriber.
                stack.callback(asyncio.create_task(_drain(ui.tracker.subscribe())).cancel)
            port = free_port()
            app = create_http_app(server, TOKEN, port=port, middleware=ui.middleware() if ui else ())
            async with serving(app, port) as url:
                durations = await time_calls(url, calls)
            if ui is not None:
                await ui.close()
            return durations
    finally:
        store.close()


def report(label: str, durations: list[float]) -> None:
    print(
        f"{label:<14} calls={len(durations)} p50={percentile(durations, 0.5):.2f} ms "
        f"p95={percentile(durations, 0.95):.2f} ms mean={statistics.fmean(durations):.2f} ms"
    )


def _close_audit_handlers() -> None:
    logger = logging.getLogger("mcp_hub.audit")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


async def main_async(calls: int) -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        try:
            report("UI off (fast)", await run_mode(Path(tmp), False, calls))
            report("UI on (-Ui)", await run_mode(Path(tmp), True, calls))
        finally:
            _close_audit_handlers()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--calls", type=int, default=500, help="timed calls per mode (default 500)")
    args = parser.parse_args(argv)
    asyncio.run(main_async(max(1, args.calls)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
