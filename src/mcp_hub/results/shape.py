"""Shape rules for tool results: which results the Hub may condense, and how condensed results are marked."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from mcp.types import CallToolResult, TextContent

from ..audit import redact

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



# Error text markers and the summary limit for a failed result (moved from instructions.py).
SUMMARY_REDACTION_MARGIN = 256

_ERROR_PREFIXES = ("❌", "Error:")

SUMMARY_LIMIT = 1000


def _first_text(result: CallToolResult) -> str:
    for block in getattr(result, "content", ()) or ():
        if isinstance(block, TextContent) and block.text.strip():
            return block.text.strip()
    return ""

def result_succeeded(result: CallToolResult) -> bool:
    """The Hub's single failure check: not `is_error`, and the first text does not start with an error prefix."""
    return not bool(getattr(result, "is_error", False)) and not _first_text(result).startswith(_ERROR_PREFIXES)

def result_error_summary(result: CallToolResult) -> str | None:
    """None for a successful result; otherwise the redacted start of its first text block (may be empty)."""
    if result_succeeded(result):
        return None
    text = _first_text(result)
    # Truncate before the redaction regexes run; the margin keeps a secret cut at the limit matchable.
    return redact(text[:SUMMARY_LIMIT + SUMMARY_REDACTION_MARGIN], limit=SUMMARY_LIMIT)


def redact_tool_result(result: CallToolResult, redact_text: Callable[[str], str]) -> CallToolResult:
    """Redact TextContent blocks in a tool result without copying when nothing changed."""
    changed = False
    blocks: list[Any] = []
    for block in result.content or ():
        if isinstance(block, TextContent):
            cleaned = redact_text(block.text)
            if cleaned != block.text:
                changed = True
                blocks.append(block.model_copy(update={"text": cleaned}))
                continue
        blocks.append(block)
    return result.model_copy(update={"content": blocks}) if changed else result
