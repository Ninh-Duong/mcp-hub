from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class ActivityCall:
    """One running child-tool call, tracked in memory only."""

    call_id: int
    server_id: str
    tool: str
    client: str
    agent_role: str | None
    started_at: str


@dataclass(frozen=True)
class ToolCallRecord:
    """One finished call as it is persisted to the `tool_calls` table: metadata only."""

    server_id: str
    tool: str
    client: str
    agent_role: str | None
    started_at: str
    finished_at: str
    duration_ms: int
    state: str
    error_type: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActivityTracker:
    """Live child-tool activity: per-child state, a short event history, and stream subscribers.

    Only server IDs, tool names, client labels, timing, and call state are kept; never arguments or results.
    """

    def __init__(self, *, history: int = 80, queue_size: int = 256) -> None:
        self.stream_id = uuid4().hex
        self.sequence = 0
        self._queue_size = queue_size
        self._children: dict[str, dict[str, Any]] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=history)
        self._last_call_id = 0
        self._subscribers: set[asyncio.Queue] = set()

    def begin(self, server_id: str, tool: str, client: str, agent_role: str | None) -> ActivityCall:
        self._last_call_id += 1
        call = ActivityCall(self._last_call_id, server_id, tool, client, agent_role, _now())
        state = self._children.setdefault(server_id, {"active": {}})
        state["active"][call.call_id] = tool
        state.update(last_tool=tool, last_state="running", last_at=call.started_at)
        self.append_event(server_id, tool, "started", call_id=call.call_id, client=client, agent_role=agent_role)
        return call

    def finish(self, call: ActivityCall, state_name: str, error_type: str | None, duration_ms: int) -> ToolCallRecord:
        finished_at = _now()
        state = self._children[call.server_id]
        state["active"].pop(call.call_id, None)
        state.update(
            last_tool=call.tool, last_state=state_name, last_duration_ms=duration_ms,
            last_at=finished_at, last_error_type=error_type or None,
        )
        self.append_event(
            call.server_id, call.tool, state_name, duration_ms, error_type,
            call_id=call.call_id, client=call.client, agent_role=call.agent_role,
        )
        return ToolCallRecord(
            call.server_id, call.tool, call.client, call.agent_role, call.started_at, finished_at,
            max(0, duration_ms), state_name, error_type or None,
        )

    def append_event(
        self,
        server_id: str,
        tool: str,
        state: str,
        duration_ms: int | None = None,
        error_type: str | None = None,
        *,
        call_id: int,
        client: str = "unknown",
        agent_role: str | None = None,
    ) -> None:
        self.sequence += 1
        event = {
            "stream_id": self.stream_id,
            "sequence": self.sequence,
            "call_id": call_id,
            "server_id": server_id,
            "tool": tool,
            "state": state,
            "at": _now(),
            "duration_ms": duration_ms,
            "error_type": error_type,
            "client": client,
            "agent_role": agent_role,
        }
        self._events.append(event)
        self._publish(event)

    def _publish(self, event: dict[str, Any]) -> None:
        for subscriber in tuple(self._subscribers):
            if not subscriber.full():
                subscriber.put_nowait(event)
                continue
            # A subscriber that fell behind drops its backlog and reloads the snapshot instead.
            while not subscriber.empty():
                subscriber.get_nowait()
            subscriber.put_nowait({"resync": True})

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def snapshot(self, server_ids: Iterable[str], tool_counts: dict[str, int]) -> dict[str, Any]:
        return {
            "children": {server_id: self._child_snapshot(server_id, tool_counts) for server_id in server_ids},
            "events": list(self._events),
            "sequence": self.sequence,
            "stream_id": self.stream_id,
        }

    def _child_snapshot(self, server_id: str, tool_counts: dict[str, int]) -> dict[str, Any]:
        state = self._children.get(server_id, {})
        active = state.get("active", {})
        return {
            "tool_count": tool_counts.get(server_id, 0),
            "active_calls": len(active),
            "active_tools": list(active.values()),
            "active_call_details": [{"call_id": call_id, "tool": tool} for call_id, tool in active.items()],
            "last_tool": state.get("last_tool"),
            "last_state": state.get("last_state"),
            "last_duration_ms": state.get("last_duration_ms"),
            "last_error_type": state.get("last_error_type"),
            "last_at": state.get("last_at"),
        }
