from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, AsyncIterator

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ..jobs.store import MAX_ACTIVITY_OFFSET
from .tracker import ActivityTracker

STREAM_KEEPALIVE_SECONDS = 15
_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ActivityRoutes:
    """`/dashboard/api/activity` (daily report) and `/dashboard/api/activity/stream` (live SSE)."""

    def __init__(self, runtime: Any, tracker: ActivityTracker, store: Any | None) -> None:
        self.runtime = runtime
        self.tracker = tracker
        self.store = store

    def routes(self) -> tuple[Route, ...]:
        return (
            Route("/dashboard/api/activity", self.activity, methods=["GET"]),
            Route("/dashboard/api/activity/stream", self.activity_stream, methods=["GET"]),
        )

    async def activity_stream(self, _request: Request) -> Response:
        subscriber = self.tracker.subscribe()
        return StreamingResponse(self._messages(subscriber), media_type="text/event-stream", headers=_SSE_HEADERS)

    def _snapshot_message(self) -> str:
        activity = self.runtime.activity_snapshot()
        current = {"children": self.runtime.child_states(), "activity": activity}
        event_id = f"{activity.get('stream_id', 'unknown')}:{activity.get('sequence', 0)}"
        return _sse_message("snapshot", current, event_id)

    async def _messages(self, subscriber: asyncio.Queue) -> AsyncIterator[str]:
        try:
            yield self._snapshot_message()
            while True:
                try:
                    event = await asyncio.wait_for(subscriber.get(), timeout=STREAM_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event.get("resync"):
                    yield self._snapshot_message()
                    continue
                event_id = f"{event.get('stream_id', 'unknown')}:{event.get('sequence', 0)}"
                yield _sse_message("activity", event, event_id)
        finally:
            self.tracker.unsubscribe(subscriber)

    async def activity(self, request: Request) -> Response:
        if self.store is None:
            return JSONResponse({"error": "Activity history is unavailable."}, status_code=503)
        try:
            selected, offset, limit, sort_by = _parse_activity_page(request)
        except (TypeError, ValueError):
            return JSONResponse({"error": "Invalid activity query."}, status_code=400)
        start, end = _local_day_bounds(selected)
        params = request.query_params
        try:
            report = await asyncio.to_thread(
                self.store.activity_report, start, end,
                server_id=params.get("server_id") or None, client=params.get("client") or None,
                tool=params.get("tool") or None, sort_by=sort_by, offset=offset, limit=limit,
            )
        except ValueError:
            return JSONResponse({"error": "Invalid activity query."}, status_code=400)
        return JSONResponse({
            **report,
            "date": selected.isoformat(),
            "timezone": str(datetime.now().astimezone().tzinfo),
        })


def _parse_activity_page(request: Request) -> tuple[date, int, int, str]:
    params = request.query_params
    selected = date.fromisoformat(params.get("date") or date.today().isoformat())
    offset = max(0, int(params.get("offset", "0")))
    if offset > MAX_ACTIVITY_OFFSET:
        raise ValueError("Activity offset is too large")
    limit = min(250, max(1, int(params.get("limit", "100"))))
    sort_by = params.get("sort", "calls")
    if sort_by not in {"calls", "duration"}:
        raise ValueError("Invalid activity sort")
    return selected, offset, limit, sort_by


def _local_day_bounds(selected: date) -> tuple[datetime, datetime]:
    start = datetime.combine(selected, time.min).astimezone().astimezone(timezone.utc)
    end = datetime.combine(selected + timedelta(days=1), time.min).astimezone().astimezone(timezone.utc)
    return start, end


def _sse_message(event_name: str, data: dict[str, Any], event_id: str) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event_name}\ndata: {payload}\n\n"
