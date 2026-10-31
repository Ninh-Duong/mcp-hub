"""Which AI client is using each child MCP, from the Hub's live activity stream. No Qt here.

With the activity UI on, the Hub streams one Server-Sent Event per child-tool call event
(`/dashboard/api/activity/stream`). Events hold only server IDs, tool names, client labels, and
times, never arguments or results. A worker thread forwards them; `UsageBoard` turns them into one
short line per child on the Qt thread.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..service_config import ServiceConfigError
from . import status, theme

# Longer than twice the stream's 15 s keep-alive, so a dead connection is noticed.
STREAM_READ_TIMEOUT_SECONDS = 40
FINAL_STATES = frozenset({"succeeded", "failed", "interrupted"})


@dataclass(frozen=True)
class ChildUsage:
    """`active`: calls running now; `recent`: the last call finished `minutes_ago` whole minutes ago."""

    kind: str
    clients: tuple[str, ...]
    tool: str | None = None
    minutes_ago: int = 0


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield `(event name, JSON object)` per event; comments (keep-alives) and non-object data are skipped."""
    name, data = "message", []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            payload = _json_object("\n".join(data)) if data else None
            if payload is not None:
                yield name, payload
            name, data = "message", []
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                name = value
            elif field == "data":
                data.append(value)


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _parse_time(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class UsageBoard:
    """Running calls and the last finished call per child, fed only on the Qt thread."""

    def __init__(self) -> None:
        self._active: dict[str, dict[Any, tuple[str, str]]] = {}
        self._last: dict[str, tuple[str, str, datetime]] = {}

    def clear(self) -> None:
        self._active.clear()
        self._last.clear()

    def load_snapshot(self, activity: dict[str, Any]) -> None:
        """Start over from a stream snapshot (the first message, and every resync)."""
        self.clear()
        children = activity.get("children") if isinstance(activity.get("children"), dict) else {}
        for server_id, child in children.items():
            if not isinstance(child, dict):
                continue
            for call in child.get("active_call_details") or ():
                if isinstance(call, dict) and "call_id" in call:
                    self._active.setdefault(server_id, {})[call["call_id"]] = (_client(call), str(call.get("tool") or ""))
            finished_at = _parse_time(child.get("last_at"))
            if child.get("last_client") and finished_at is not None:
                self._last[server_id] = (_client({"client": child["last_client"]}), str(child.get("last_tool") or ""), finished_at)

    def apply_event(self, event: dict[str, Any], now: datetime) -> None:
        server_id, state = event.get("server_id"), event.get("state")
        if not isinstance(server_id, str):
            return
        call = (_client(event), str(event.get("tool") or ""))
        if state == "started":
            self._active.setdefault(server_id, {})[event.get("call_id")] = call
        elif state in FINAL_STATES:
            self._active.get(server_id, {}).pop(event.get("call_id"), None)
            self._last[server_id] = (*call, _parse_time(event.get("at")) or now)

    def usage(self, server_id: str, now: datetime) -> ChildUsage | None:
        calls = list(self._active.get(server_id, {}).values())
        if calls:
            tools = {tool for _client_name, tool in calls}
            clients = status.count_labels(Counter(client for client, _tool in calls))
            return ChildUsage("active", clients, tools.pop() if len(tools) == 1 else None)
        last = self._last.get(server_id)
        age = _age(last[2], now) if last else None
        if last is None or age is None:
            return None
        return ChildUsage("recent", (last[0],), last[1] or None, int(age // 60))

    def client_usage(self, now: datetime) -> dict[str, tuple[str, tuple[str, ...]]]:
        """Per client: (`active`, children it calls now), else (`recent`, children it used in the last minutes)."""
        active: dict[str, dict[str, None]] = {}
        for server_id, calls in self._active.items():
            for client, _tool in calls.values():
                active.setdefault(client, {})[server_id] = None
        result = {client: ("active", tuple(children)) for client, children in active.items()}
        recent: dict[str, list[str]] = {}
        for server_id, (client, _tool, finished_at) in self._last.items():
            if client not in result and _age(finished_at, now) is not None:
                recent.setdefault(client, []).append(server_id)
        result.update({client: ("recent", tuple(children)) for client, children in recent.items()})
        return result


def _age(finished_at: datetime, now: datetime) -> float | None:
    """Seconds since `finished_at`, or None once the use is older than RECENT_SECONDS."""
    age = max(0.0, (now - finished_at).total_seconds())
    return age if age < theme.RECENT_SECONDS else None


def _client(item: dict[str, Any]) -> str:
    return str(item.get("client") or "unknown")


def open_activity_stream(*, opener: status.Opener = status.OPENER.open, private_root: Path | None = None) -> Any:
    """Open the Hub's activity stream with the bearer token (sent only to 127.0.0.1, never shown)."""
    try:
        config = status.load_config(private_root)
        request = urllib.request.Request(
            f"{status.base_url(config)}/dashboard/api/activity/stream",
            headers={"Authorization": f"Bearer {config.token}", "Accept": "text/event-stream"},
        )
        return opener(request, timeout=STREAM_READ_TIMEOUT_SECONDS)
    except (ServiceConfigError, OSError, ValueError) as exc:
        raise status.DesktopError(f"Activity stream unavailable ({type(exc).__name__})") from exc


def stream_usage(
    put: Callable[[tuple[str, Any]], None],
    stop: threading.Event,
    *,
    connect: Callable[[], Any] = open_activity_stream,
) -> None:
    """Worker: forward `usage_snapshot` / `usage_event` messages until `stop` is set, reconnecting with backoff."""
    delay = 1.0
    while not stop.is_set():
        try:
            with connect() as response:
                for name, data in parse_sse(_lines(response, stop)):
                    delay = 1.0
                    _forward(put, name, data)
        except (status.DesktopError, OSError, ValueError):
            pass
        if stop.wait(delay):
            return
        delay = min(delay * 2, theme.STREAM_RETRY_MAX_SECONDS)


def _lines(response: Iterable[bytes], stop: threading.Event) -> Iterator[str]:
    """Decoded lines until `stop` is set; keep-alives arrive every 15 s, so the check runs at least that often."""
    for line in response:
        if stop.is_set():
            return
        yield line.decode("utf-8", "replace")


def _forward(put: Callable[[tuple[str, Any]], None], name: str, data: dict[str, Any]) -> None:
    if name == "snapshot":
        activity = data.get("activity")
        put(("usage_snapshot", activity if isinstance(activity, dict) else {}))
    elif name == "activity":
        put(("usage_event", data))
