"""Shape rules for tool results: which results the Hub may condense, and how condensed results are marked."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mcp.types import CallToolResult, TextContent

CONDENSED_META_KEY = "mcp-hub/condensed"


def is_condensable(output_schema: Mapping[str, Any] | None, result: CallToolResult) -> bool:
    """Only read-only text results without a declared output schema may be condensed (FR-001)."""
    if output_schema:
        return False
    if result.structured_content is not None:
        return False
    if any(not isinstance(block, TextContent) for block in result.content):
        return False
    return not result.is_error


def with_condensed_meta(result: CallToolResult, result_id: str, kept: int, total: int) -> CallToolResult:
    """Mark a condensed result with its full-result ID and counts; the content is not changed (FR-002)."""
    marker = {"result_id": result_id, "kept": kept, "total": total}
    meta = {**(result.meta or {}), CONDENSED_META_KEY: marker}
    return result.model_copy(update={"meta": meta})
