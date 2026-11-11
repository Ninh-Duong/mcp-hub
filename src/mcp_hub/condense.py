"""Deterministic, structural result condenser shared by core Hub runtime and BitNet."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from .extensions import Condensed, ResultCondenser
from .plan_merge import fold

_TS_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b")
_GUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX_RE = re.compile(r"\b(?:0x)?[0-9a-fA-F]{8,}\b")
_NUM_RE = re.compile(r"\b\d+\b")
_LONG_STR_RE = re.compile(r'(["\'])(?:\\.|[^\\]){41,}\1')
_ERROR_MARKER_RE = re.compile(r"\b(error|exception|fail|fatal|timeout|loi)\b")
_KEYWORD_RE = re.compile(r"[A-Za-z0-9_]{3,}")
_LARGE_OFFLOAD_CHARS = 100_000


def template_of(line: str) -> str:
    """Replace volatile tokens in a log line with placeholders for grouping."""
    line = _TS_RE.sub("<ts>", line)
    line = _GUID_RE.sub("<id>", line)
    line = _HEX_RE.sub("<hex>", line)
    line = _LONG_STR_RE.sub("<str>", line)
    return _NUM_RE.sub("<n>", line)


def group_lines(text: str) -> tuple[list[str], int]:
    """Group repeating log templates (`[xN]`), sorting error lines first."""
    lines = text.splitlines()
    total = len(lines)
    if total < 50:
        return lines, total
    counts: dict[str, int] = {}
    first_seen: dict[str, str] = {}
    for line in lines:
        tmpl = template_of(line)
        first_seen.setdefault(tmpl, line)
        counts[tmpl] = counts.get(tmpl, 0) + 1
    formatted = [f"[x{counts[tmpl]}] {line}" if counts[tmpl] > 1 else line for tmpl, line in first_seen.items()]
    errors = [line for line in formatted if _ERROR_MARKER_RE.search(fold(line))]
    others = [line for line in formatted if not _ERROR_MARKER_RE.search(fold(line))]
    return [*errors, *others], total


def head_tail_preview(text: str, max_chars: int) -> str:
    """Keep leading and trailing slices of an oversized single-line payload within `max_chars`."""
    if len(text) <= max_chars:
        return text
    if max_chars <= 120:
        return text[:max(1, max_chars)]
    marker_budget = 48
    half = max(20, (max_chars - marker_budget) // 2)
    omitted = len(text) - 2 * half
    marker = f"\n[... {omitted} chars omitted ...]\n"
    if len(marker) + 2 * half > max_chars:
        half = max(10, (max_chars - len(marker)) // 2)
        marker = f"\n[... {len(text) - 2 * half} chars omitted ...]\n"
    return f"{text[:half]}{marker}{text[-half:]}"


def format_capped(lines: list[str], max_chars: int) -> str:
    """Join lines up to `max_chars`, slicing an overlong first line instead of dropping all data."""
    kept: list[str] = []
    current_len = 0
    for index, line in enumerate(lines):
        line_len = len(line) + (1 if kept else 0)
        if current_len + line_len <= max_chars:
            kept.append(line)
            current_len += line_len
            continue
        remaining = len(lines) - index
        if not kept:
            return _slice_overlong_first(line, remaining - 1, max_chars)
        return "\n".join(kept) + f"\n[... {remaining} more lines omitted]"
    return "\n".join(kept)


def _slice_overlong_first(line: str, extra_lines: int, max_chars: int) -> str:
    tail = f" and {extra_lines} more lines omitted]" if extra_lines > 0 else "]"
    suffix = f"... [truncated {len(line)} chars{tail}"
    allow = max(32, max_chars - len(suffix) - 16)
    if allow >= len(line):
        return line[:max_chars]
    note = f"... [truncated {len(line) - allow} chars{tail}"
    return line[:allow] + note


def _task_keywords(task: str | None) -> tuple[str, ...]:
    if not task:
        return ()
    seen = dict.fromkeys(word.lower() for word in _KEYWORD_RE.findall(task))
    return tuple(list(seen)[:20])


def _compact_table_node(node: Any) -> Any:
    if isinstance(node, list):
        if len(node) >= 3 and all(isinstance(item, dict) for item in node):
            cols = list(dict.fromkeys(key for row in node for key in row))
            keep_cols = [c for c in cols if any(row.get(c) not in (None, "", [], {}) for row in node)]
            null_cols = [c for c in cols if c not in keep_cols]
            table: dict[str, Any] = {
                "_table": keep_cols,
                "_count": len(node),
                "_rows": [[_compact_table_node(row.get(col)) for col in keep_cols] for row in node],
            }
            if null_cols:
                table["_null_cols"] = null_cols
            return table
        return [_compact_table_node(item) for item in node]
    if isinstance(node, dict):
        return {key: _compact_table_node(value) for key, value in node.items()}
    return node


def _is_priority(value: Any, keywords: tuple[str, ...]) -> bool:
    blob = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    folded = fold(blob)
    return bool(_ERROR_MARKER_RE.search(folded) or (keywords and any(word in folded for word in keywords)))


def _trim_strings(node: Any, cap: int, keywords: tuple[str, ...]) -> Any:
    if isinstance(node, str):
        if len(node) <= cap:
            return node
        if _is_priority(node, keywords) and len(node) <= cap * 2:
            return node
        return f"{node[:cap]}...(+{len(node) - cap}c)"
    if isinstance(node, list):
        return [_trim_strings(item, cap, keywords) for item in node]
    if isinstance(node, dict):
        return {key: _trim_strings(value, cap, keywords) for key, value in node.items()}
    return node


def _limit_rows(node: Any, max_n: int, keywords: tuple[str, ...]) -> Any:
    if isinstance(node, dict) and "_rows" in node and "_table" in node:
        rows = node["_rows"]
        if len(rows) <= max_n:
            return node
        prio = [row for row in rows if _is_priority(row, keywords)]
        rest = [row for row in rows if not _is_priority(row, keywords)]
        kept = [*prio, *rest][:max_n]
        return {**node, "_rows": kept, "_omitted_rows": len(rows) - len(kept)}
    if isinstance(node, dict):
        return {key: _limit_rows(value, max_n, keywords) for key, value in node.items()}
    if isinstance(node, list) and len(node) > max_n:
        return [*node[:max_n], f"...(+{len(node) - max_n} items)"]
    return node


def compact_json_payload(text: str, max_chars: int, task: str | None = None) -> tuple[str, bool] | None:
    """Compact structured JSON losslessly to tables first, then shrink fields/rows safely."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, (dict, list)):
        return None
    tabled = _compact_table_node(data)
    serialized = json.dumps(tabled, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) <= max_chars:
        return serialized, True
    keywords = _task_keywords(task)
    for cap in (2000, 1200, 800, 500, 300, 180):
        candidate = json.dumps(_trim_strings(tabled, cap, keywords), ensure_ascii=False, separators=(",", ":"))
        if len(candidate) <= max_chars:
            return candidate, False
    return _shrink_rows_to_budget(tabled, serialized, max_chars, keywords), False


def _shrink_rows_to_budget(tabled: Any, serialized: str, max_chars: int, keywords: tuple[str, ...]) -> str:
    for cap in (400, 250, 150):
        trimmed = _trim_strings(tabled, cap, keywords)
        for max_n in (60, 40, 25, 15, 10, 6, 3, 1):
            candidate = json.dumps(_limit_rows(trimmed, max_n, keywords), ensure_ascii=False, separators=(",", ":"))
            if len(candidate) <= max_chars:
                return candidate
    return head_tail_preview(serialized, max_chars)


def condense_text_rules(text: str, max_chars: int, task: str | None = None) -> Condensed:
    """Condense JSON structurally or plain logs by template grouping, without calling an LLM."""
    compacted = compact_json_payload(text, max_chars, task)
    if compacted is not None:
        compact_text, lossless = compacted
        return Condensed(compact_text, 1, 1, "rules", lossless=lossless)
    lines, total = group_lines(text)
    if len(lines) == 1 and len(lines[0]) > max_chars:
        preview = head_tail_preview(lines[0], max_chars)
        return Condensed(preview, len(preview.splitlines()), total, "rules", lossless=False)
    capped = format_capped(lines, max_chars)
    return Condensed(capped, len(capped.splitlines()), total, "rules", lossless=False)


class RuleCondenser(ResultCondenser):
    """Pure-Python deterministic condenser; runs in core even without BitNet."""

    def __init__(self, *, max_chars: int = 9000) -> None:
        self.max_chars = max_chars

    async def condense(
        self, text: str, task: str | None = None, *, max_chars: int | None = None,
    ) -> Condensed | None:
        budget = self.max_chars if max_chars is None else max_chars
        if len(text) > _LARGE_OFFLOAD_CHARS:
            return await asyncio.to_thread(condense_text_rules, text, budget, task)
        return condense_text_rules(text, budget, task)
