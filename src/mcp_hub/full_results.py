"""In-memory cache for original, un-condensed tool results.

Kept in memory only (LRU + TTL). Never written to disk.
"""
from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
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
        now = self.clock()
        entry = self._entries.get(result_id)
        if entry is None:
            return None
        expires_at, text = entry
        if expires_at <= now:
            del self._entries[result_id]
            return None
        self._entries.move_to_end(result_id)
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

    def _evict_expired(self, now: float) -> None:
        expired = [rid for rid, (exp, _) in self._entries.items() if exp <= now]
        for rid in expired:
            del self._entries[rid]
