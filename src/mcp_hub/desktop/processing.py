"""Typed, transient BitNet processing stream. Network workers never touch Qt."""
from __future__ import annotations

import re
import threading
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError

from . import status, theme, usage
from .connection import load_connection

OPERATIONS = frozenset({"plan", "child_call", "condense", "job_plan", "local_summary"})
STATES = frozenset({"started", "succeeded", "failed", "fallback"})
SOURCES = frozenset({"bitnet", "rules", "child", "cache", "fallback", "local"})
ERRORS = frozenset({"timeout", "unavailable", "cancelled", "tool_error", "unexpected"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")


@dataclass(frozen=True)
class ProcessingEvent:
    stream_id: str
    sequence: int
    at: str
    operation: str
    state: str
    source: str | None = None
    server_id: str | None = None
    tool_name: str | None = None
    duration_ms: int | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ProcessingMessage:
    kind: str
    stream_id: str
    sequence: int
    events: tuple[ProcessingEvent, ...]


def _sequence(value: Any) -> bool:
    return type(value) is int and value >= 0


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _event(value: Any) -> ProcessingEvent | None:
    if not isinstance(value, dict):
        return None
    if not _identifier(value.get("stream_id")) or not _sequence(value.get("sequence")):
        return None
    for key, allowed in (("operation", OPERATIONS), ("state", STATES), ("source", SOURCES), ("error_code", ERRORS)):
        code = value.get(key)
        if code is None and key in {"source", "error_code"}:
            continue
        if not isinstance(code, str) or code not in allowed:
            return None
    for key in ("server_id", "tool_name"):
        if value.get(key) is not None and not _identifier(value[key]):
            return None
    duration = value.get("duration_ms")
    if duration is not None and not _sequence(duration):
        return None
    try:
        at = value["at"]
        if not isinstance(at, str) or len(at) > 40 or datetime.fromisoformat(at).tzinfo is None:
            return None
    except (KeyError, ValueError):
        return None
    fields = ProcessingEvent.__dataclass_fields__
    return ProcessingEvent(**{key: value[key] for key in fields if key in value})


def parse_message(kind: str, value: Any) -> ProcessingMessage | None:
    """Project only known safe fields; malformed and future records are ignored."""
    if not isinstance(value, dict):
        return None
    if kind == "event":
        event = _event(value)
        return ProcessingMessage(kind, event.stream_id, event.sequence, (event,)) if event else None
    if kind not in {"snapshot", "resync"}:
        return None
    stream_id, sequence, raw = value.get("stream_id"), value.get("sequence"), value.get("events")
    if not _identifier(stream_id) or not _sequence(sequence) or not isinstance(raw, list):
        return None
    events = tuple(_event(item) for item in raw)
    if any(item is None or item.stream_id != stream_id or item.sequence > sequence for item in events):
        return None
    return ProcessingMessage("snapshot", stream_id, sequence, tuple(sorted(events, key=lambda item: item.sequence)[-3:]))


class ProcessingBoard:
    """Three newest events, applied only by the Qt consumer, deduplicated per Hub process."""

    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.stream_id: str | None = None
        self.sequence = -1
        self.events: tuple[ProcessingEvent, ...] = ()

    def apply(self, message: ProcessingMessage | None) -> bool:
        if message is None:
            return False
        if message.stream_id != self.stream_id:
            if message.kind != "snapshot":
                return False
            self.clear()
            self.stream_id = message.stream_id
        if message.sequence <= self.sequence:
            return False
        self.sequence = message.sequence
        values = message.events if message.kind == "snapshot" else (*self.events, *message.events)
        self.events = tuple(sorted(values, key=lambda item: item.sequence, reverse=True)[:3])
        return True


def open_processing_stream(*, opener: status.Opener = status.OPENER.open) -> Any:
    """Only the validated loopback connection receives the admin bearer."""
    connection = load_connection()
    request = urllib.request.Request(
        f"{connection.url}/dashboard/api/bitnet/processing/stream",
        headers={"Authorization": f"Bearer {connection.token}", "Accept": "text/event-stream"},
    )
    return opener(request, timeout=usage.STREAM_READ_TIMEOUT_SECONDS)


def stream_processing(
    put: Callable[[tuple[str, Any]], None], stop: threading.Event,
    *, connect: Callable[[], Any] = open_processing_stream,
) -> None:
    """Forward typed messages through the app queue; reconnect quietly for an older Hub."""
    delay, unavailable = 1.0, False
    while not stop.is_set():
        try:
            with connect() as response:
                for name, value in usage.parse_sse(_decoded_lines(response, stop)):
                    message = parse_message(name, value)
                    if message is not None and not stop.is_set():
                        put(("processing", message))
                        delay, unavailable = 1.0, False
        except HTTPError as exc:
            if exc.code in {401, 404} and not unavailable and not stop.is_set():
                put(("processing_unavailable", None))
                unavailable = True
        except (status.DesktopError, OSError, ValueError, HTTPException):
            pass
        if stop.wait(delay):
            return
        delay = min(delay * 2, theme.STREAM_RETRY_MAX_SECONDS)


def _decoded_lines(response: Iterable[bytes], stop: threading.Event) -> Iterator[str]:
    for line in response:
        if stop.is_set():
            return
        yield line.decode("utf-8", "replace")
