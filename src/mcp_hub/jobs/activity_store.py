"""Tool-call records and activity reports of the job store: the usage the activity UI shows, read from the store's tables."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any
from collections.abc import Iterable

from .timeutil import utc_timestamp


_TOOL_CALL_COLUMNS = (
    "server_id", "tool", "client", "agent_role", "started_at", "finished_at", "duration_ms", "state", "error_type",
)
_STATE_INDEX = _TOOL_CALL_COLUMNS.index("state")
_FINISHED_CALL_STATES = frozenset({"succeeded", "failed", "interrupted"})
_FAILED = "SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END)"
MAX_ACTIVITY_OFFSET = 1_000_000


class ActivityStoreMixin:
    """Tool-call records and activity reports of JobStore (moved out of store.py to keep each module under 600 lines)."""

    def record_tool_calls(self, records: Iterable[Any]) -> int:
        """Insert finished calls in one statement and one commit.

        Each record has the `_TOOL_CALL_COLUMNS` attributes (see `activity_ui.tracker.ToolCallRecord`).
        """
        rows = [tuple(getattr(record, column) for column in _TOOL_CALL_COLUMNS) for record in records]
        if not rows:
            return 0
        if any(row[_STATE_INDEX] not in _FINISHED_CALL_STATES for row in rows):
            raise ValueError("Invalid tool call state")
        placeholders = ",".join([f"({','.join('?' * len(_TOOL_CALL_COLUMNS))})"] * len(rows))
        values = [value for row in rows for value in row]
        with self._lock, self._connection:
            self._connection.execute(
                f"INSERT INTO tool_calls({','.join(_TOOL_CALL_COLUMNS)}) VALUES {placeholders}", values,
            )
        return len(rows)

    def activity_report(
        self,
        start: datetime,
        end: datetime,
        *,
        server_id: str | None = None,
        client: str | None = None,
        tool: str | None = None,
        sort_by: str = "calls",
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        if sort_by not in {"calls", "duration"}:
            raise ValueError("sort_by must be calls or duration")
        offset = max(0, offset)
        if offset > MAX_ACTIVITY_OFFSET:
            raise ValueError("Activity offset is too large")
        page_limit = max(1, min(250, limit))
        where, values = _activity_filters(start, end, server_id=server_id, client=client, tool=tool)
        order = "calls DESC, duration_ms DESC" if sort_by == "calls" else "duration_ms DESC, calls DESC"
        with self._lock:
            rows = self._activity_rows(where, values, order, page_limit, offset)
        return _summarize_activity(rows, offset, page_limit)

    def _activity_rows(
        self, where: str, values: list[Any], order: str, limit: int, offset: int,
    ) -> dict[str, Any]:
        def fetch(sql: str, params: list[Any] = values) -> list[sqlite3.Row]:
            return self._connection.execute(sql, params).fetchall()

        return {
            "summary": fetch(
                f"SELECT COUNT(*) AS total_calls, COALESCE(SUM(duration_ms), 0) AS total_duration_ms, "
                f"{_FAILED} AS failures, SUM(CASE WHEN state='interrupted' THEN 1 ELSE 0 END) AS interrupted "
                f"FROM tool_calls WHERE {where}"
            )[0],
            "by_child": fetch(
                f"SELECT server_id, COUNT(*) AS calls, COALESCE(SUM(duration_ms), 0) AS duration_ms, "
                f"{_FAILED} AS failures FROM tool_calls WHERE {where} GROUP BY server_id ORDER BY {order}, server_id"
            ),
            "by_tool": fetch(
                f"SELECT tool, COUNT(*) AS calls, COALESCE(SUM(duration_ms), 0) AS duration_ms, "
                f"{_FAILED} AS failures FROM tool_calls WHERE {where} GROUP BY tool ORDER BY calls DESC, tool"
            ),
            "by_client": fetch(
                f"SELECT client, COUNT(*) AS calls FROM tool_calls WHERE {where} "
                f"GROUP BY client ORDER BY calls DESC, client"
            ),
            "events": fetch(
                f"SELECT call_id, server_id, tool, client, agent_role, started_at, finished_at, "
                f"duration_ms, state, error_type FROM tool_calls WHERE {where} "
                f"ORDER BY started_at DESC, call_id DESC LIMIT ? OFFSET ?",
                [*values, limit, offset],
            ),
        }


def _activity_filters(
    start: datetime, end: datetime, *, server_id: str | None, client: str | None, tool: str | None,
) -> tuple[str, list[Any]]:
    clauses = ["started_at>=?", "started_at<?"]
    values: list[Any] = [utc_timestamp(start), utc_timestamp(end)]
    for column, value in (("server_id", server_id), ("client", client), ("tool", tool)):
        if value:
            clauses.append(f"{column}=?")
            values.append(value)
    return " AND ".join(clauses), values

def _summarize_activity(rows: dict[str, Any], offset: int, limit: int) -> dict[str, Any]:
    summary = rows["summary"]
    events = [dict(row) for row in rows["events"]]
    return {
        "summary": {
            "total_calls": summary["total_calls"],
            "total_duration_ms": summary["total_duration_ms"],
            "failures": summary["failures"] or 0,
            "interrupted": summary["interrupted"] or 0,
        },
        "by_child": [dict(row) for row in rows["by_child"]],
        "by_tool": [dict(row) for row in rows["by_tool"]],
        "by_client": [dict(row) for row in rows["by_client"]],
        "events": events,
        "has_more": offset + len(events) < summary["total_calls"],
        "offset": offset,
        "limit": limit,
    }
