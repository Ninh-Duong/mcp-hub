"""In-memory cache for original, un-condensed tool results.

Kept in memory only (LRU + TTL). Never written to disk.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any


class FullResultStore:
    """Originals of condensed results, in memory only (LRU + TTL). Never written to disk."""

    def __init__(
        self,
        max_entries: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._entries: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def put(self, text: str) -> str:
        """Store an original result and return its ID, evicting expired and LRU items."""
        now = self.clock()
        self._evict_expired(now)
        while len(self._entries) >= self.max_entries:
            self._entries.popitem(last=False)
        result_id = f"r-{secrets.token_hex(6)}"
        self._entries[result_id] = (now + self.ttl_seconds, text)
        return result_id

    def page(self, result_id: str, offset: int = 0, limit: int = 20000) -> dict[str, Any] | None:
        """Return a page of text for the given result_id, or None if unknown/expired."""
        text = self._get(result_id)
        if text is None:
            return None
        return _slice_page(result_id, text, offset, limit)

    def search(
        self,
        result_id: str,
        *,
        contains: str | None = None,
        regex: str | None = None,
        fields: Sequence[str] | None = None,
        offset: int = 0,
        limit: int = 20000,
    ) -> dict[str, Any] | None:
        """Filter a stored result by substring/regex or JSON field projection, then page."""
        pattern = _compile_regex(regex)
        needle = contains.casefold() if contains else None
        text = self._get(result_id)
        if text is None:
            return None
        field_tuple = tuple(item for item in (fields or ()) if isinstance(item, str) and item)
        filtered = _filter_json_records(text, needle, pattern, field_tuple)
        if filtered is None:
            filtered = _filter_plain_text(text, needle, pattern)
        return _slice_page(result_id, filtered, offset, limit)

    def _get(self, result_id: str) -> str | None:
        now = self.clock()
        entry = self._entries.get(result_id)
        if entry is None:
            return None
        expires_at, text = entry
        if expires_at <= now:
            del self._entries[result_id]
            return None
        self._entries.move_to_end(result_id)
        return text

    def _evict_expired(self, now: float) -> None:
        expired = [rid for rid, (exp, _) in self._entries.items() if exp <= now]
        for rid in expired:
            del self._entries[rid]


def _slice_page(result_id: str, text: str, offset: int, limit: int) -> dict[str, Any]:
    total_chars = len(text)
    offset = max(0, offset)
    limit = max(1, limit)
    chunk = text[offset:offset + limit] if offset < total_chars else ""
    next_offset = offset + len(chunk) if offset + len(chunk) < total_chars else None
    return {
        "result_id": result_id,
        "offset": offset,
        "limit": limit,
        "total_chars": total_chars,
        "text": chunk,
        "next_offset": next_offset,
    }


def _compile_regex(regex: str | None) -> re.Pattern[str] | None:
    if not regex:
        return None
    try:
        return re.compile(regex, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"Invalid regex: {exc}") from exc


def _matches(blob: str, needle: str | None, pattern: re.Pattern[str] | None) -> bool:
    if needle and needle not in blob.casefold():
        return False
    if pattern and not pattern.search(blob):
        return False
    return True


def _project_dict(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    if not fields:
        return row
    lower_map = {key.casefold(): key for key in row}
    projected: dict[str, Any] = {}
    for wanted in fields:
        real_key = wanted if wanted in row else lower_map.get(wanted.casefold())
        if real_key is not None:
            projected[real_key] = row[real_key]
    return projected


def _filter_list(
    rows: list[Any], needle: str | None, pattern: re.Pattern[str] | None, fields: tuple[str, ...],
) -> tuple[list[Any], int]:
    kept: list[Any] = []
    for row in rows:
        blob = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        if _matches(blob, needle, pattern):
            kept.append(_project_dict(row, fields) if isinstance(row, dict) else row)
    return kept, len(rows)


def _filter_json_records(
    text: str, needle: str | None, pattern: re.Pattern[str] | None, fields: tuple[str, ...],
) -> str | None:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(data, list):
        kept, total = _filter_list(data, needle, pattern, fields)
        return json.dumps({"matches": len(kept), "total": total, "items": kept}, ensure_ascii=False, separators=(",", ":"))
    if not isinstance(data, dict):
        return None
    out: dict[str, Any] = {}
    matched_any = False
    for key, value in data.items():
        if isinstance(value, list) and (not value or isinstance(value[0], dict)):
            kept, total = _filter_list(value, needle, pattern, fields)
            out[key] = kept
            out[f"{key}_matched"] = f"{len(kept)}/{total}"
            matched_any = True
        elif isinstance(value, dict) and key == "related":
            rel, rel_counts = _filter_related(value, needle, pattern, fields)
            out[key] = rel
            out["related_matched"] = rel_counts
            matched_any = True
        else:
            out[key] = value
    if not matched_any:
        return None
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


def _filter_related(
    related: dict[str, Any], needle: str | None, pattern: re.Pattern[str] | None, fields: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, str]]:
    out: dict[str, Any] = {}
    counts: dict[str, str] = {}
    for table, rows in related.items():
        if isinstance(rows, list):
            kept, total = _filter_list(rows, needle, pattern, fields)
            if kept or (not needle and not pattern):
                out[table] = kept
            counts[table] = f"{len(kept)}/{total}"
    return out, counts


def _filter_plain_text(text: str, needle: str | None, pattern: re.Pattern[str] | None) -> str:
    if not needle and not pattern:
        return text
    lines = text.splitlines()
    if len(lines) > 1:
        matched = [f"L{idx}: {line}" for idx, line in enumerate(lines, 1) if _matches(line, needle, pattern)]
        return "\n".join(matched) if matched else "[0 matching lines]"
    return _match_snippets(text, needle, pattern)


def _match_snippets(text: str, needle: str | None, pattern: re.Pattern[str] | None) -> str:
    pat = pattern or re.compile(re.escape(needle or ""), re.IGNORECASE)
    spans: list[tuple[int, int]] = []
    for match in pat.finditer(text):
        start = max(0, match.start() - 160)
        end = min(len(text), match.end() + 200)
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
        if len(spans) >= 40:
            break
    if not spans:
        return "[0 matches]"
    return "\n---\n".join(text[s:e] for s, e in spans)
