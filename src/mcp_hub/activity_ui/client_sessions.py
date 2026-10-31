from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any, Callable

from mcp.server.streamable_http_manager import DEFAULT_SESSION_IDLE_TIMEOUT

from ..server import request_client_name


@dataclass
class _Session:
    connected_at: datetime
    last_seen: datetime
    client: str = "unknown"
    in_flight: int = 0


@dataclass
class _Stream:
    client: str
    connected_at: datetime


@dataclass
class _ClientEntry:
    client: str
    connections: int = 0
    connected_at: datetime | None = None
    last_seen: datetime | None = None

    def connect(self, connected_at: datetime, last_seen: datetime) -> None:
        self.connections += 1
        self.connected_at = connected_at if self.connected_at is None else min(self.connected_at, connected_at)
        self.touch(last_seen)

    def touch(self, at: datetime) -> None:
        self.last_seen = at if self.last_seen is None else max(self.last_seen, at)

    def as_dict(self) -> dict[str, Any]:
        return {
            "client": self.client,
            "live": self.connections > 0,
            "connections": self.connections,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }


class ClientSessions:
    """In-memory view of which AI clients use the shared Hub. Session IDs never leave this class.

    Three signals, because 2026-07-28 clients are stateless:
    - an open `subscriptions/listen` stream (the stdio bridge holds one) is a live connection;
    - a 2025-era HTTP session is live until DELETE or the SDK idle timeout with no request in flight;
    - any other request marks its client as recently active for the same idle window.
    """

    def __init__(
        self,
        *,
        idle_timeout: float = DEFAULT_SESSION_IDLE_TIMEOUT,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.idle_timeout = timedelta(seconds=idle_timeout)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._sessions: dict[str, _Session] = {}
        self._streams: dict[int, _Stream] = {}
        self._stream_ids = count(1)
        self._recent: dict[str, datetime] = {}

    def _touch(self, session_id: str) -> _Session:
        now = self.clock()
        session = self._sessions.get(session_id)
        if session is None:
            session = self._sessions[session_id] = _Session(connected_at=now, last_seen=now)
        session.last_seen = now
        return session

    def begin(self, session_id: str) -> None:
        self._touch(session_id).in_flight += 1

    def end(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.in_flight = max(0, session.in_flight - 1)
            session.last_seen = self.clock()

    def identify(self, session_id: str, client: str) -> None:
        self._touch(session_id).client = client

    def close(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def seen(self, client: str) -> None:
        self._recent[client] = self.clock()

    def open_stream(self, client: str) -> int:
        stream_id = next(self._stream_ids)
        self._streams[stream_id] = _Stream(client=client, connected_at=self.clock())
        return stream_id

    def close_stream(self, stream_id: int) -> None:
        stream = self._streams.pop(stream_id, None)
        if stream is not None:
            self.seen(stream.client)

    def note(self, ctx: Any) -> str:
        """Record the client behind an MCP request context and return its normalized name."""
        client_name = request_client_name(ctx)
        headers = getattr(getattr(ctx, "request", None), "headers", None)
        session_id = headers.get("mcp-session-id") if headers is not None else None
        if session_id:
            self.identify(session_id, client_name)
        elif headers is not None:
            self.seen(client_name)
        return client_name

    def snapshot(self) -> list[dict[str, Any]]:
        self._prune(self.clock() - self.idle_timeout)
        entries = sorted(self._group().values(), key=lambda entry: (entry.connections == 0, entry.client.lower()))
        return [entry.as_dict() for entry in entries]

    def _prune(self, cutoff: datetime) -> None:
        self._sessions = {
            key: session for key, session in self._sessions.items()
            if session.in_flight > 0 or session.last_seen > cutoff
        }
        self._recent = {client: seen for client, seen in self._recent.items() if seen > cutoff}

    def _group(self) -> dict[str, _ClientEntry]:
        grouped: dict[str, _ClientEntry] = {}

        def entry(client: str) -> _ClientEntry:
            return grouped.setdefault(client, _ClientEntry(client))

        for stream in self._streams.values():
            entry(stream.client).connect(stream.connected_at, stream.connected_at)
        for session in self._sessions.values():
            entry(session.client).connect(session.connected_at, session.last_seen)
        for client, seen in self._recent.items():
            entry(client).touch(seen)
        return grouped
