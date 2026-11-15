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


def parse_page_args(arguments: Mapping[str, Any], *, default_offset: int, default_limit: int) -> tuple[int, int] | str:
    """Read offset and limit from tool arguments; return the pair, or an error naming the bad field."""
    offset = _page_number(arguments, "offset", default_offset, minimum=0)
    if isinstance(offset, str):
        return offset
    limit = _page_number(arguments, "limit", default_limit, minimum=1)
    if isinstance(limit, str):
        return limit
    return offset, limit


def _page_number(arguments: Mapping[str, Any], field: str, default: int, *, minimum: int) -> int | str:
    if field not in arguments:
        return default
    value = _whole_number(arguments[field])
    if value is None:
        return f"{field} must be a whole number"
    if value < minimum:
        return f"{field} must be {minimum} or more"
    return value


def _whole_number(value: Any) -> int | None:
    """An int that is not a bool, or a string of ASCII digits; anything else is None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None
