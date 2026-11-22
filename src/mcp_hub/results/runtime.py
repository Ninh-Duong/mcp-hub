"""Full-result and condensed-result handling for HubRuntime: fetching, condensing, and the hub__get_full_result tool."""
from __future__ import annotations

import asyncio
import json
from time import monotonic
from collections.abc import Mapping
from typing import Any

from mcp.types import (
    CallToolResult,
    TextContent,
    Tool,
)

from ..extensions import processing_step
from ..results.shape import is_condensable, parse_page_args, with_condensed_meta


class ResultsMixin:
    """Methods of HubRuntime, moved out of server.py."""

    def _get_full_result(self, arguments: dict[str, Any], request_id: str) -> CallToolResult:
        fields = {"request_id": request_id, "feature": "hub", "server": "mcp-hub", "tool": FULL_RESULT_TOOL}
        self.audit.write("tool.call.started", **fields)
        ids = _extract_result_ids(arguments)
        fmt = arguments.get("format", "raw")
        page = parse_page_args(arguments, default_offset=0, default_limit=20000)
        error = _input_error(ids, fmt, page)
        if error is not None:
            self.audit.write("tool.call.succeeded", **fields, duration_ms=0)
            return error_result(error)
        assert ids is not None and not isinstance(page, str)
        offset, limit = page
        try:
            pages = [self._fetch_one_result(rid, arguments, offset, limit) for rid in ids]
        except ValueError as exc:
            self.audit.write("tool.call.succeeded", **fields, duration_ms=0)
            return error_result(str(exc))
        self.audit.write("tool.call.succeeded", **fields, duration_ms=0)
        return _format_full_results(ids, pages, fmt)

    def _fetch_one_result(
        self, rid: str, arguments: dict[str, Any], offset: int, limit: int,
    ) -> dict[str, Any] | None:
        assert self.full_results is not None
        limit = min(limit, 50000)
        contains = arguments.get("contains")
        regex = arguments.get("regex")
        proj = arguments.get("fields")
        if contains or regex or proj:
            return self.full_results.search(
                rid, contains=contains if isinstance(contains, str) else None,
                regex=regex if isinstance(regex, str) else None,
                fields=proj if isinstance(proj, list) else None,
                offset=offset, limit=limit,
            )
        return self.full_results.page(rid, offset=offset, limit=limit)

    async def _maybe_condense(
        self, name: str, result: CallToolResult, task: str | None, request_id: str,
    ) -> CallToolResult:
        """Condense a large read-only success result; any failure returns `result` unchanged."""
        if self.condenser is None or self.full_results is None:
            return result
        if name not in self._read_only or not is_condensable(self._output_schema(name), result):
            return result
        total_len = sum(len(getattr(item, "text", "")) for item in result.content if isinstance(item, TextContent))
        if total_len < self.settings.bitnet.condense_min_chars:
            return result
        text = await asyncio.to_thread(join_text, result.content) if total_len > 100_000 else join_text(result.content)
        route = self.routes.get(name)
        server = route.server_id if route else "unknown"
        started = monotonic()
        try:
            condensed = await processing_step(self.processing_observer, "condense", asyncio.wait_for(
                self.condenser.condense(text, task),
                self.settings.bitnet.condense_timeout_seconds,
            ))
        except Exception:
            condensed = None
        duration_ms = max(0, int((monotonic() - started) * 1000))
        if condensed is None:
            self.audit.write(
                "tool.result.condensed", request_id=request_id, server=server, tool=name,
                kept_lines=0, total_lines=0, source="fallback", duration_ms=duration_ms,
            )
            return result
        return self._finish_condensed(result, text, condensed, request_id, server, name, duration_ms)

    def _output_schema(self, name: str) -> Mapping[str, Any] | None:
        """The output schema the child declared for a routed tool, or None."""
        route = self.routes.get(name)
        if route is None:
            return None
        child_tool = self._discovered.get(route.server_id, {}).get(route.tool_name)
        return getattr(child_tool, "output_schema", None)

    def _finish_condensed(
        self, original: CallToolResult, text: str, condensed: Any, request_id: str, server: str, name: str,
        duration_ms: int,
    ) -> CallToolResult:
        """The condensed result; `original` when the store cannot hold the full text (one entry over its byte limit)."""
        assert self.full_results is not None
        if getattr(condensed, "lossless", False):
            self._audit_condensed(condensed, request_id, server, name, duration_ms)
            note = "\n[Compacted losslessly by mcp-hub.]"
            return original.model_copy(update={"content": [TextContent(type="text", text=condensed.text + note)]})
        rid = self.full_results.put(text)
        if rid is None:
            return original
        self._audit_condensed(condensed, request_id, server, name, duration_ms)
        footer = (
            f"\n\n[Condensed by mcp-hub: kept {condensed.kept_lines} of {condensed.total_lines} lines. "
            f"Full result: {FULL_RESULT_TOOL} result_id=\"{rid}\" ({len(text)} chars).]"
        )
        condensed_result = original.model_copy(update={"content": [TextContent(type="text", text=condensed.text + footer)]})
        return with_condensed_meta(condensed_result, rid, condensed.kept_lines, condensed.total_lines)

    def _audit_condensed(self, condensed: Any, request_id: str, server: str, name: str, duration_ms: int) -> None:
        self.audit.write(
            "tool.result.condensed", request_id=request_id, server=server, tool=name,
            kept_lines=condensed.kept_lines, total_lines=condensed.total_lines,
            source=condensed.source, duration_ms=duration_ms,
        )


def _extract_result_ids(arguments: dict[str, Any]) -> list[str] | None:
    rids = arguments.get("result_ids")
    if isinstance(rids, list):
        if not 1 <= len(rids) <= 8 or not all(isinstance(item, str) and item for item in rids):
            return None
        return list(rids)
    rid = arguments.get("result_id")
    return [rid] if isinstance(rid, str) and rid else None

def _input_error(ids: list[str] | None, fmt: Any, page: tuple[int, int] | str) -> str | None:
    """The first problem with a hub__get_full_result call, or None when the call can run."""
    if ids is None:
        return "Provide 'result_id' or 'result_ids' (1-8 IDs)."
    if fmt not in ("raw", "json"):
        return "'format' must be 'raw' or 'json'."
    return page if isinstance(page, str) else None

def _format_raw_page(page: dict[str, Any]) -> str:
    nxt = page["next_offset"] if page["next_offset"] is not None else "null"
    header = (
        f"[result_id={page['result_id']} offset={page['offset']} limit={page['limit']} "
        f"total_chars={page['total_chars']} next_offset={nxt}]"
    )
    return f"{header}\n{page['text']}" if page["text"] else header

def _format_full_results(ids: list[str], pages: list[dict[str, Any] | None], fmt: str) -> CallToolResult:
    if len(ids) == 1:
        page = pages[0]
        if page is None:
            return error_result("Result expired or unknown; do not replay automatically. Report the limitation or re-plan with user-authorized scope.")
        text = json.dumps(page) if fmt == "json" else _format_raw_page(page)
        return CallToolResult(content=[TextContent(type="text", text=text)])
    if fmt == "json":
        payload = [p if p is not None else {"result_id": rid, "error": "Result expired or unknown."} for rid, p in zip(ids, pages, strict=True)]
        return CallToolResult(content=[TextContent(type="text", text=json.dumps({"results": payload}))], is_error=not any(pages))
    blocks = [_format_raw_page(p) if p is not None else f"[result_id={rid} error: Result expired or unknown.]" for rid, p in zip(ids, pages, strict=True)]
    return CallToolResult(content=[TextContent(type="text", text="\n\n".join(blocks))], is_error=not any(pages))

def full_result_tool() -> Tool:
    return Tool(
        name=FULL_RESULT_TOOL,
        description=(
            "Retrieve full or filtered content from stored condensed result(s). Supports single `result_id` or "
            "batch `result_ids`, `contains`/`regex`/`fields` filtering, and raw (default) or JSON output."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "result_id": {"type": "string", "description": "Single result_id from a condensed footer."},
                "result_ids": {
                    "type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8,
                    "description": "Up to 8 result_ids to fetch together in one call.",
                },
                "contains": {"type": "string", "description": "Case-insensitive substring filter on rows or lines."},
                "regex": {"type": "string", "description": "Case-insensitive regex filter on rows or lines."},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "JSON columns/fields to keep per matched item."},
                "format": {"type": "string", "enum": ["raw", "json"], "default": "raw", "description": "Raw text (default, compact) or legacy JSON wrapper."},
                "offset": {"type": "integer", "minimum": 0, "default": 0, "description": "Character offset to read from."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50000, "default": 20000, "description": "Maximum characters to return."},
            },
            "additionalProperties": False,
        },
        annotations={"readOnlyHint": True},
    )


FULL_RESULT_TOOL = "hub__get_full_result"


def error_result(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)

def join_text(content: list[Any]) -> str:
    return "".join(item.text for item in content if isinstance(item, TextContent) and item.text)
