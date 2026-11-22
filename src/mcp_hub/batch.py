"""`hub__batch`: independent read-only child tool calls in one MCP request, run in parallel.

A client's own thinking between two sequential calls costs far more than a call itself, so a batch saves one
model turn per call it merges. Every call still goes through the Hub's normal path (allowlist, audit,
redaction, result cache, the child's timeout). Only read-only tools may be batched: a write runs on its own.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from time import monotonic, perf_counter
from collections.abc import Mapping
from typing import Any
from mcp.types import (
    CallToolResult,
    TextContent,
    Tool,
)
from .results.shape import result_succeeded
from .results.condense import condense_text_rules
from .extensions import ProcessingEvent, emit_processing
from .planning import policy
from .planning.policy import CallFacts
from .results.shape import is_condensable
from .results.runtime import FULL_RESULT_TOOL, error_result, join_text
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
    return [_item(call, task, timeout) for call, task in zip(calls, tasks, strict=True)]


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
    metadata = {str(item.call.index): item.result.meta["mcp-hub/workflow"] for item in items
                if item.result and item.result.meta and "mcp-hub/workflow" in item.result.meta}
    return CallToolResult(content=content, is_error=not any(item.ok for item in items),
                          **({"_meta": {"mcp-hub/workflow": {"items": metadata}}} if metadata else {}))


class BatchMixin:
    """hub__batch on HubRuntime: packing, policy, and the call that runs a batch in parallel."""

    async def _call_batch_core(self, arguments: dict[str, Any], request_id: str, client_name: str) -> CallToolResult:
        """Run independent read-only calls in parallel; each one is audited as `<request_id>.<i>`."""
        fields = {"request_id": request_id, "feature": "hub", "server": "mcp-hub", "tool": BATCH_TOOL}
        servers = {name: route.server_id for name, route in self.routes.items()}
        builtin = frozenset({FULL_RESULT_TOOL}) if self.full_results is not None else frozenset()
        try:
            calls = parse_calls(
                arguments, servers, frozenset(self._read_only), self.settings.batch.max_calls, builtin_tools=builtin,
            )
        except BatchRefused as exc:
            self.audit.write("tool.call.denied", level=logging.WARNING, **fields, reason=str(exc))
            return error_result(str(exc))
        plan_id = arguments.get(policy.PLAN_ID_ARG)
        calls = [replace(call, arguments=policy.split_plan_id(call.arguments)[1]) for call in calls]
        refusal = self._batch_policy(plan_id, calls, fields, builtin)
        if refusal is not None:
            return refusal
        self.audit.write("tool.call.started", **fields, calls=len(calls))
        started = perf_counter()
        plan = self.plans.get(plan_id)

        async def call_child(tool: str, tool_arguments: dict[str, Any], item_id: str) -> CallToolResult:
            if tool == FULL_RESULT_TOOL and self.full_results is not None:
                res = self._get_full_result(tool_arguments, item_id)
                policy.record(plan, tool, tool_arguments, result_succeeded(res))
                return res
            res = await self._workflow_child_call(plan, tool, tool_arguments, item_id, client_name)
            policy.record(plan, tool, tool_arguments, result_succeeded(res))
            return await self._maybe_condense(tool, res, plan.task if plan else None, item_id)

        items = await run_batch(calls, call_child, request_id, result_succeeded,
                                timeout=self.settings.batch.timeout_seconds, per_child=self.settings.batch.max_per_child)
        items = self._pack_batch_items(items, plan.task if plan else None, request_id)
        items = [replace(item, result=self._workflow_result(plan, item.result)) if item.result else item for item in items]
        result = batch_result(items)
        failed = sum(not item.ok for item in items)
        event, level = ("tool.call.failed", logging.ERROR) if result.is_error else ("tool.call.succeeded", logging.INFO)
        self.audit.write(event, level=level, **fields, calls=len(calls), failed=failed,
                         duration_ms=int((perf_counter() - started) * 1000))
        return result

    def _pack_batch_items(self, items: list[BatchItem], task: str | None, request_id: str) -> list[BatchItem]:
        """Shrink oversized un-condensed batch items proportionally to keep the whole batch inline."""
        if self.condenser is None or self.full_results is None:
            return items
        pack_limit = self.settings.batch.pack_chars
        total = sum(len(join_text(item.result.content)) for item in items if item.result and item.ok)
        if total <= pack_limit:
            return items
        eligible = [
            (idx, item, join_text(item.result.content))
            for idx, item in enumerate(items)
            if item.ok and item.result and item.call.tool != FULL_RESULT_TOOL
            and "[Condensed by mcp-hub:" not in join_text(item.result.content)
            and "[Compacted losslessly by mcp-hub.]" not in join_text(item.result.content)
            and len(join_text(item.result.content)) >= 1200
            and is_condensable(self._output_schema(item.call.tool), item.result)
        ]
        if not eligible:
            return items
        fixed = total - sum(len(text) for _, _, text in eligible)
        budget = max(2500, (pack_limit - fixed) // len(eligible))
        packed = list(items)
        for idx, item, text in eligible:
            if len(text) > budget:
                condensed = self._pack_condense(text, budget, task)
                route = self.routes.get(item.call.tool)
                server = route.server_id if route else "unknown"
                new_res = self._finish_condensed(
                    item.result, text, condensed, f"{request_id}.{item.call.index}", server, item.call.tool, 0,
                )
                packed[idx] = BatchItem(item.call, new_res, item.ok, item.duration_ms)
        return packed

    def _pack_condense(self, text: str, budget: int, task: str | None) -> Any:
        if self.processing_observer is None:
            return condense_text_rules(text, budget, task)
        started = monotonic()
        emit_processing(self.processing_observer, ProcessingEvent("condense", "started", "rules"))
        try:
            result = condense_text_rules(text, budget, task)
        except Exception:
            emit_processing(self.processing_observer, ProcessingEvent(
                "condense", "failed", "fallback", duration_ms=int((monotonic() - started) * 1000),
                error_code="unexpected",
            ))
            raise
        emit_processing(self.processing_observer, ProcessingEvent(
            "condense", "succeeded", "rules", duration_ms=int((monotonic() - started) * 1000),
        ))
        return result

    def _batch_policy(
        self, plan_id: Any, calls: list[Any], fields: dict[str, Any],
        builtin_tools: frozenset[str] = frozenset(),
    ) -> CallToolResult | None:
        """Refuse the whole batch when one call breaks the plan (enforce mode); None lets it run."""
        if self.policy_mode != "enforce":
            return None
        items = [
            (self._call_facts.get(call.tool) or CallFacts(call.tool, "mcp-hub", True, False), call.arguments)
            for call in calls
        ]
        found = policy.check_batch(
            self.plans.get(plan_id), items, self._health_tools, plan_id, builtin_tools=builtin_tools,
        )
        if found is None:
            return None
        index, violation = found
        self.audit.write("tool.call.denied", level=logging.WARNING, **fields, reason=f"policy:{violation.rule}")
        return error_result(f"Policy: call {index}: {violation.message}")
