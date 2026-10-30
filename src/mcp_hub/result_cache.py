"""Short-lived, in-process cache for read-only child tool results.

Keys are the tool name plus canonical JSON of its arguments. Error results and
exceptions are never stored; a caller's `store_if` check can refuse more (the Hub
passes its single failure check, so error-looking text is not cached either). Concurrent identical calls share one in-flight
child call (single-flight), and every caller receives its own deep copy.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from mcp.types import CallToolResult


DEFAULT_MAX_ENTRIES = 256
DEFAULT_MAX_BYTES = 4 * 1024 * 1024
_KEY_SEPARATOR = "\n"


@dataclass(frozen=True)
class _Entry:
    result: CallToolResult
    expires_at: float
    size: int


def cache_key(tool: str, arguments: dict[str, Any] | None) -> str | None:
    """Return a stable key for the call, or None when the arguments cannot be serialized."""
    try:
        canonical = json.dumps(
            arguments or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError):
        return None
    return f"{tool}{_KEY_SEPARATOR}{canonical}"


class ResultCache:
    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[CallToolResult]] = {}
        self._bytes = 0
        self._generation = 0
        self._hits = 0
        self._misses = 0

    async def get_or_call(
        self,
        tool: str,
        arguments: dict[str, Any] | None,
        ttl_seconds: float,
        call: Callable[[], Awaitable[CallToolResult]],
        store_if: Callable[[CallToolResult], bool] | None = None,
    ) -> tuple[CallToolResult, bool]:
        """Return (result, served_from_cache); call the child only on a miss.

        A result is stored only when it is not `is_error` and `store_if` (when given) returns True.
        """
        key = cache_key(tool, arguments)
        if key is None or not ttl_seconds > 0:
            return await call(), False
        cached = self._lookup(key)
        if cached is not None:
            self._hits += 1
            return cached.model_copy(deep=True), True
        return await self._call_once(key, ttl_seconds, call, store_if)

    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "entries": len(self._entries)}

    def invalidate(self, tools: Iterable[str] | None = None) -> int:
        """Drop cached results of the given tools (all when None); return how many were dropped.

        Calls still in flight are not stored when they finish, so a write cannot be
        followed by a stale read that started before it.
        """
        self._generation += 1
        prefixes = None if tools is None else tuple(f"{tool}{_KEY_SEPARATOR}" for tool in tools)
        for key in [key for key in self._inflight if prefixes is None or key.startswith(prefixes)]:
            del self._inflight[key]
        stale = [key for key in self._entries if prefixes is None or key.startswith(prefixes)]
        for key in stale:
            self._remove(key)
        return len(stale)

    async def _call_once(
        self, key: str, ttl_seconds: float, call: Callable[[], Awaitable[CallToolResult]],
        store_if: Callable[[CallToolResult], bool] | None,
    ) -> tuple[CallToolResult, bool]:
        task = self._inflight.get(key)
        leader = task is None
        if task is None:
            self._misses += 1
            task = asyncio.ensure_future(call())
            self._inflight[key] = task
            generation = self._generation
            task.add_done_callback(lambda done: self._settle(key, ttl_seconds, generation, done, store_if))
        else:
            self._hits += 1
        # shield: a cancelled caller must not cancel the call other waiters share.
        result = await asyncio.shield(task)
        return result.model_copy(deep=True), not leader

    def _settle(
        self, key: str, ttl_seconds: float, generation: int, task: asyncio.Future[CallToolResult],
        store_if: Callable[[CallToolResult], bool] | None,
    ) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if task.cancelled() or task.exception() is not None or generation != self._generation:
            return
        result = task.result()
        if not isinstance(result, CallToolResult) or result.is_error:
            return
        if store_if is None or store_if(result):
            self._store(key, result, ttl_seconds)

    def _lookup(self, key: str) -> CallToolResult | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._remove(key)
            return None
        self._entries.move_to_end(key)
        return entry.result

    def _store(self, key: str, result: CallToolResult, ttl_seconds: float) -> None:
        size = len(result.model_dump_json(by_alias=True))
        if size > self.max_bytes or self.max_entries <= 0:
            return
        expires_at = self._clock() + ttl_seconds if math.isfinite(ttl_seconds) else math.inf
        self._remove(key)
        self._entries[key] = _Entry(result, expires_at, size)
        self._bytes += size
        while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
            self._remove(next(iter(self._entries)))

    def _remove(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= entry.size
