from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..audit import AuditLog
from .tracker import ToolCallRecord

BATCH_SIZE = 50
BATCH_SECONDS = 1.0
QUEUE_LIMIT = 10_000
_STOP = object()


class ActivityRecorder:
    """Persists finished calls in the background: one multi-row INSERT per batch, off the event loop.

    A batch holds up to `batch_size` records or whatever arrived within `interval` seconds of its first
    record. `close()` writes everything still queued.
    """

    def __init__(
        self, store: Any, audit: AuditLog, *,
        batch_size: int = BATCH_SIZE, interval: float = BATCH_SECONDS, queue_limit: int = QUEUE_LIMIT,
    ) -> None:
        self.store = store
        self.audit = audit
        self.batch_size = batch_size
        self.interval = interval
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_limit)
        self._task: asyncio.Task[None] | None = None
        self._dropped = 0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="mcp-hub-activity-recorder")

    def record(self, record: ToolCallRecord) -> None:
        """Queue one finished call; never blocks the caller."""
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 1000 == 0:
                self.audit.write("activity.persist.dropped", level=logging.WARNING, dropped=self._dropped)

    async def close(self) -> None:
        """Stop the writer after it has written every queued record."""
        if self._task is None:
            await self._write(self._drain())
            return
        await self._queue.put(_STOP)
        await self._task
        self._task = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            first = await self._queue.get()
            if first is _STOP:
                return
            batch = [first]
            stop = await self._fill(batch, loop.time() + self.interval)
            await self._write(batch)
            if stop:
                await self._write(self._drain())
                return

    async def _fill(self, batch: list[ToolCallRecord], deadline: float) -> bool:
        """Add records until the batch is full or the deadline passes; True when a stop was requested."""
        loop = asyncio.get_running_loop()
        while len(batch) < self.batch_size:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                item = await asyncio.wait_for(self._queue.get(), remaining)
            except asyncio.TimeoutError:
                return False
            if item is _STOP:
                return True
            batch.append(item)
        return False

    def _drain(self) -> list[ToolCallRecord]:
        items: list[ToolCallRecord] = []
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if item is not _STOP:
                items.append(item)
        return items

    async def _write(self, records: list[ToolCallRecord]) -> None:
        for start in range(0, len(records), self.batch_size):
            chunk = records[start:start + self.batch_size]
            try:
                await asyncio.to_thread(self.store.record_tool_calls, chunk)
            except Exception as exc:
                self.audit.write(
                    "activity.persist.failed", level=logging.ERROR, records=len(chunk), error_type=type(exc).__name__,
                )
