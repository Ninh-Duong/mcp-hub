"""`hub__batch`: independent read-only child tool calls in one MCP request, run in parallel.

A client's own thinking between two sequential calls costs far more than a call itself, so a batch saves one
model turn per call it merges. Every call still goes through the Hub's normal path (allowlist, audit,
redaction, result cache, the child's timeout). Only read-only tools may be batched: a write runs on its own.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool

from .settings import BatchSettings

BATCH_TOOL = "hub__batch"
MIN_BATCH_CALLS = 2
MAX_BATCH_RESULT_CHARS = 200_000
# Limits come from settings.batch: the most calls in one batch, the most one child serves at once (so a batch
# never floods one upstream), and the time the whole batch may take.
_DEFAULTS = BatchSettings()

ChildCall = Callable[[str, dict[str, Any], str], Awaitable[CallToolResult]]

_CALL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "description": "A read-only Hub tool name, <prefix>__<tool>."},
        "arguments": {"type": "object", "description": "That tool's arguments."},
    },
    "required": ["tool"],
    "additionalProperties": False,
}
_DESCRIPTION = (
    "Run {low}-{high} independent read-only Hub tool calls in parallel in one request, e.g. the same log query "
    "for dev, stg, and prod, or a ticket read plus a log snapshot. Results come back in order, each headed "
    "'[i] <tool>: ok|error (<ms> ms)'. Use it only when no call needs another call's result; write tools are "
    "refused here and run on their own."
)


class BatchRefused(ValueError):
    """The batch as a whole is invalid; nothing ran. The message is safe to show to the client."""


@dataclass(frozen=True)
class BatchCall:
    index: int
    tool: str
    arguments: dict[str, Any]
    server_id: str


@dataclass(frozen=True)
class BatchItem:
    call: BatchCall
    result: CallToolResult | None
    ok: bool
    duration_ms: int


def batch_tool(max_calls: int = _DEFAULTS.max_calls) -> Tool:
    calls = {"type": "array", "minItems": MIN_BATCH_CALLS, "maxItems": max_calls, "items": _CALL_SCHEMA}
    schema = {"type": "object", "properties": {"calls": calls}, "required": ["calls"], "additionalProperties": False}
    return Tool(
        name=BATCH_TOOL, description=_DESCRIPTION.format(low=MIN_BATCH_CALLS, high=max_calls), input_schema=schema,
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )


def parse_calls(
    arguments: Mapping[str, Any], servers: Mapping[str, str], read_only: frozenset[str],
    max_calls: int = _DEFAULTS.max_calls, *, builtin_tools: frozenset[str] = frozenset(),
) -> list[BatchCall]:
    """Validate the whole batch before anything runs. `servers` maps each enabled tool to its child."""
    calls = arguments.get("calls") if isinstance(arguments, Mapping) else None
    if not isinstance(calls, list) or not MIN_BATCH_CALLS <= len(calls) <= max_calls:
        raise BatchRefused(f"'calls' must list {MIN_BATCH_CALLS} to {max_calls} tool calls.")
    parsed = []
    for index, call in enumerate(calls, 1):
        if not isinstance(call, Mapping) or not isinstance(call.get("tool"), str):
            raise BatchRefused(f"Call {index} needs a 'tool' name.")
        tool, call_arguments = call["tool"], call.get("arguments", {})
        if not isinstance(call_arguments, Mapping):
            raise BatchRefused(f"Call {index} ({tool}): 'arguments' must be an object.")
        if tool in builtin_tools:
            parsed.append(BatchCall(index, tool, dict(call_arguments), "mcp-hub"))
            continue
        if tool not in servers:
            raise BatchRefused(f"Call {index}: this tool is not enabled in the Hub configuration.")
        if tool not in read_only:
            raise BatchRefused(f"Call {index} ({tool}) may write; call it on its own, not in {BATCH_TOOL}.")
        parsed.append(BatchCall(index, tool, dict(call_arguments), servers[tool]))
    return parsed


async def run_batch(
    calls: list[BatchCall],
    call_child: ChildCall,
    request_id: str,
    succeeded: Callable[[CallToolResult], bool],
    *,
    timeout: float = _DEFAULTS.timeout_seconds,
    per_child: int = _DEFAULTS.max_per_child,
) -> list[BatchItem]:
    """Run every call at once (at most `per_child` per child); a call still running at `timeout` is cancelled."""
    limits = {server_id: asyncio.Semaphore(per_child) for server_id in {call.server_id for call in calls}}

    async def run(call: BatchCall) -> BatchItem:
        started = perf_counter()
        async with limits[call.server_id]:
            result = await call_child(call.tool, call.arguments, f"{request_id}.{call.index}")
        return BatchItem(call, result, succeeded(result), int((perf_counter() - started) * 1000))

    tasks = [asyncio.create_task(run(call), name=f"mcp-hub-batch-{request_id}-{call.index}") for call in calls]
    try:
        await asyncio.wait(tasks, timeout=timeout)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return [_item(call, task, timeout) for call, task in zip(calls, tasks)]


def _item(call: BatchCall, task: asyncio.Task, timeout: float) -> BatchItem:
    if task.cancelled() or task.exception() is not None:
        return BatchItem(call, None, False, int(timeout * 1000))
    return task.result()


def batch_result(items: list[BatchItem], limit: int = MAX_BATCH_RESULT_CHARS) -> CallToolResult:
    """One header block per call, then that call's own content; text past `limit` is cut and says so."""
    content: list[Any] = []
    budget = limit
    for item in items:
        state = "ok" if item.ok else "error"
        content.append(TextContent(type="text", text=f"[{item.call.index}] {item.call.tool}: {state} ({item.duration_ms} ms)"))
        if item.result is None:
            content.append(TextContent(type="text", text="Timed out before the batch limit; call it on its own."))
            continue
        for block in item.result.content or ():
            if not isinstance(block, TextContent):
                content.append(block)
                continue
            text = block.text if len(block.text) <= budget else block.text[:budget] + "\n[cut: batch result limit reached]"
            budget = max(0, budget - len(block.text))
            content.append(TextContent(type="text", text=text))
    return CallToolResult(content=content, is_error=not any(item.ok for item in items))
