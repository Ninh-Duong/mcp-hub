"""Server-sent event streams of the desktop app: one reader and one reconnect loop, shared by usage and processing.

A stream is read until its stop event is set. A dropped connection or an error reconnects with a backoff that grows
to `theme.STREAM_RETRY_MAX_SECONDS`, and a delivered message resets it. The loop is the same for every stream; each
stream says what an event means (`handle`) and what an HTTP error means (`on_http_error`).
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable, Iterator
from typing import Any
from urllib.error import HTTPError

from . import status, theme

STREAM_ERRORS: tuple[type[BaseException], ...] = (status.DesktopError, OSError, ValueError)


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


def decoded_lines(response: Iterable[bytes], stop: threading.Event) -> Iterator[str]:
    """Decoded lines until `stop` is set; keep-alives arrive every 15 s, so the check runs at least that often."""
    for line in response:
        if stop.is_set():
            return
        yield line.decode("utf-8", "replace")


class StreamController:
    """Reads the events from `connect()` until `stop` is set, reconnecting with backoff.

    `handle(name, data)` returns True when it delivered a message, which resets the backoff. `on_http_error(exc)`
    sees the HTTP errors; without it they are ignored like the other errors in `errors`.
    """

    def __init__(
        self,
        connect: Callable[[], Any],
        stop: threading.Event,
        handle: Callable[[str, dict[str, Any]], bool],
        *,
        errors: tuple[type[BaseException], ...] = STREAM_ERRORS,
        on_http_error: Callable[[HTTPError], None] | None = None,
    ) -> None:
        self.connect = connect
        self.stop = stop
        self.handle = handle
        self.errors = errors
        self.on_http_error = on_http_error

    def run(self) -> None:
        delay = 1.0
        while not self.stop.is_set():
            try:
                with self.connect() as response:
                    for name, data in parse_sse(decoded_lines(response, self.stop)):
                        if self.handle(name, data):
                            delay = 1.0
            except HTTPError as exc:  # an OSError: the stream decides what the HTTP status means
                if self.on_http_error is not None:
                    self.on_http_error(exc)
            except self.errors:
                pass
            if self.stop.wait(delay):
                return
            delay = min(delay * 2, theme.STREAM_RETRY_MAX_SECONDS)
