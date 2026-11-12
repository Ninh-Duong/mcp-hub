"""Transient, content-free processing feed, independent of Activity UI."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..extensions import ProcessingEvent


class ProcessingFeed:
    """A three-row history and bounded queues, used only on the Hub event loop."""

    def __init__(self, allowed_tools: set[tuple[str, str]] | None = None) -> None:
        self.stream_id = uuid4().hex
        self.sequence = 0
        self._allowed_tools = frozenset(allowed_tools or ())
        self._events: deque[dict[str, Any]] = deque(maxlen=3)
        self._subscribers: set[asyncio.Queue] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def processing_event(self, event: ProcessingEvent) -> None:
        self.sequence += 1
        record = self._project(event)
        self._events.append(record)
        for queue in self._subscribers:
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"resync": True})
            else:
                queue.put_nowait(dict(record))

    def _project(self, event: ProcessingEvent) -> dict[str, Any]:
        record = {
            "stream_id": self.stream_id, "sequence": self.sequence,
            "at": datetime.now(UTC).isoformat(), "operation": event.operation,
            "state": event.state, "source": event.source,
        }
        if (event.server_id, event.tool_name) in self._allowed_tools:
            record.update(server_id=event.server_id, tool_name=event.tool_name)
        if event.duration_ms is not None:
            record["duration_ms"] = min(3_600_000, max(0, event.duration_ms))
        if event.error_code is not None:
            record["error_code"] = event.error_code
        return record

    def snapshot(self) -> dict[str, Any]:
        return {"stream_id": self.stream_id, "sequence": self.sequence,
                "events": [dict(event) for event in self._events]}

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
