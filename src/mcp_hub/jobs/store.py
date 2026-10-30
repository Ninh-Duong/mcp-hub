from __future__ import annotations

import json
import hashlib
import hmac
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from ..config import PRIVATE_ROOT
from ..security.dpapi import DPAPIProtector


JOB_STATES = {
    "queued", "awaiting_input", "running", "awaiting_approval",
    "completed", "partial", "failed", "interrupted", "cancelled",
}
TERMINAL_STATES = {"completed", "partial", "failed", "interrupted", "cancelled"}
MAX_ACTIVITY_OFFSET = 1_000_000
TRANSITIONS = {
    "queued": {"running", "awaiting_input", "cancelled", "failed"},
    "awaiting_input": {"queued", "running", "cancelled", "failed"},
    "running": {"awaiting_input", "awaiting_approval", "completed", "partial", "failed", "interrupted", "cancelled"},
    "awaiting_approval": {"running", "partial", "failed", "cancelled"},
    "completed": set(), "partial": set(), "failed": set(), "interrupted": set(), "cancelled": set(),
}


_SCHEMA = """
    CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        state TEXT NOT NULL,
        request_blob BLOB,
        summary_blob BLOB,
        expires_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        event TEXT NOT NULL,
        agent TEXT,
        tool TEXT,
        source_id TEXT,
        duration_ms INTEGER,
        from_state TEXT,
        to_state TEXT
    );
    CREATE TABLE IF NOT EXISTS details (
        job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
        source_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        result_blob BLOB NOT NULL,
        PRIMARY KEY (job_id, source_id)
    );
    CREATE TABLE IF NOT EXISTS pending_calls (
        job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
        tool_name TEXT NOT NULL,
        agent TEXT,
        arguments_blob BLOB NOT NULL,
        code_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        approved INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS tool_calls (
        call_id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id TEXT NOT NULL,
        tool TEXT NOT NULL,
        client TEXT NOT NULL,
        agent_role TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        duration_ms INTEGER,
        state TEXT NOT NULL CHECK (state IN ('running', 'succeeded', 'failed', 'interrupted')),
        error_type TEXT
    );
    CREATE INDEX IF NOT EXISTS jobs_updated_idx ON jobs(updated_at DESC);
    CREATE INDEX IF NOT EXISTS events_job_idx ON events(job_id, event_id);
    CREATE INDEX IF NOT EXISTS details_expiry_idx ON details(expires_at);
    CREATE INDEX IF NOT EXISTS tool_calls_started_idx ON tool_calls(started_at DESC);
    CREATE INDEX IF NOT EXISTS tool_calls_child_idx ON tool_calls(server_id, started_at DESC);
"""

_FAILED = "SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END)"
_TOOL_CALL_COLUMNS = (
    "server_id", "tool", "client", "agent_role", "started_at", "finished_at", "duration_ms", "state", "error_type",
)
_STATE_INDEX = _TOOL_CALL_COLUMNS.index("state")
_FINISHED_CALL_STATES = frozenset({"succeeded", "failed", "interrupted"})


class Protector(Protocol):
    def protect(self, data: bytes) -> bytes: ...
    def unprotect(self, data: bytes) -> bytes: ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class JobStore:
    """SQLite metadata with DPAPI-protected request, summary and tool-result blobs."""

    def __init__(
        self,
        path: str | Path = PRIVATE_ROOT / "jobs.sqlite3",
        *,
        protector: Protector | None = None,
        clock=utc_now,
        retention_days: int = 30,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.protector = protector or DPAPIProtector()
        self.clock = clock
        self.retention = timedelta(days=retention_days)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        # WAL with NORMAL stays consistent after a crash; only the last commits can be lost on power failure.
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self._connection.executescript(_SCHEMA)
        event_columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(events)")}
        if "source_id" not in event_columns:
            self._connection.execute("ALTER TABLE events ADD COLUMN source_id TEXT")
        pending_columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(pending_calls)")}
        if "agent" not in pending_columns:
            self._connection.execute("ALTER TABLE pending_calls ADD COLUMN agent TEXT")
        # Calls are now written once they finish; settle rows an older version left running.
        self._connection.execute(
            "UPDATE tool_calls SET state='interrupted', finished_at=started_at WHERE state='running'"
        )
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def create_job(
        self,
        *,
        project: str,
        question: str,
        env: str | None = None,
        issue_key: str | None = None,
        rcid: str | None = None,
        state: str = "queued",
    ) -> str:
        if state not in {"queued", "awaiting_input"}:
            raise ValueError("A new job must start queued or awaiting_input")
        now = self._timestamp()
        job_id = uuid.uuid4().hex
        request = {"project": project, "question": question, "env": env, "issue_key": issue_key, "rcid": rcid}
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO jobs(job_id,created_at,updated_at,state,request_blob,expires_at) VALUES(?,?,?,?,?,?)",
                (job_id, now, now, state, self._seal(request), self._expiry(now)),
            )
            self._insert_event(job_id, "job.created", None, None, None, None, state)
        return job_id

    def get_request(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT request_blob FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["request_blob"] is None:
                raise KeyError("Job request details have expired")
            return json.loads(self.protector.unprotect(row["request_blob"]).decode("utf-8"))

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT job_id,created_at,updated_at,state,summary_blob FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            summary = None
            if row["summary_blob"] is not None:
                summary = self.protector.unprotect(row["summary_blob"]).decode("utf-8")
            return {
                "job_id": row["job_id"], "created_at": row["created_at"], "updated_at": row["updated_at"],
                "state": row["state"], "summary": summary,
            }

    def list_jobs(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("Job list page must use limit 1..500 and non-negative offset")
        with self._lock:
            rows = self._connection.execute(
                "SELECT job_id,created_at,updated_at,state,summary_blob FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [self._job_row(row) for row in rows]

    def list_job_ids_by_state(self, state: str) -> list[str]:
        if state not in JOB_STATES:
            raise ValueError("Unknown job state")
        with self._lock:
            rows = self._connection.execute(
                "SELECT job_id FROM jobs WHERE state=? ORDER BY created_at", (state,),
            ).fetchall()
            return [row["job_id"] for row in rows]

    def save_summary(self, job_id: str, summary: str) -> None:
        now = self._timestamp()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE jobs SET summary_blob=?,updated_at=? WHERE job_id=?",
                (self.protector.protect(summary.encode("utf-8")), now, job_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(job_id)

    def update_request(self, job_id: str, **changes: Any) -> None:
        request = self.get_request(job_id)
        request.update(changes)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE jobs SET request_blob=?,updated_at=? WHERE job_id=?",
                (self._seal(request), self._timestamp(), job_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(job_id)

    def save_encrypted_result(self, job_id: str, source_id: str, result: Any) -> None:
        now = self._timestamp()
        body = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with self._lock, self._connection:
            if self._connection.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone() is None:
                raise KeyError(job_id)
            self._connection.execute(
                "INSERT OR REPLACE INTO details(job_id,source_id,created_at,expires_at,result_blob) VALUES(?,?,?,?,?)",
                (job_id, source_id, now, self._expiry(now), self.protector.protect(body)),
            )

    def get_detail_page(self, job_id: str, source_id: str, *, offset: int, limit: int) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 10000:
            raise ValueError("Detail page must use a non-negative offset and limit 1..10000")
        with self._lock:
            row = self._connection.execute(
                "SELECT result_blob FROM details WHERE job_id=? AND source_id=? AND expires_at>?",
                (job_id, source_id, self._timestamp()),
            ).fetchone()
            if row is None:
                raise KeyError(source_id)
            text = self.protector.unprotect(row["result_blob"]).decode("utf-8")
            end = min(len(text), offset + limit)
            return {
                "source_id": source_id, "offset": offset, "limit": limit,
                "content": text[offset:end], "next_offset": end if end < len(text) else None,
            }

    def append_event(
        self,
        job_id: str,
        event: str,
        *,
        agent: str | None = None,
        tool: str | None = None,
        source_id: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        with self._lock, self._connection:
            if self._connection.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone() is None:
                raise KeyError(job_id)
            self._insert_event(job_id, event, agent, tool, duration_ms, None, None, source_id)

    def list_events(self, job_id: str, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("Event page must use limit 1..500 and non-negative offset")
        with self._lock:
            rows = self._connection.execute(
                "SELECT created_at,event,agent,tool,source_id,duration_ms,from_state,to_state FROM events "
                "WHERE job_id=? ORDER BY event_id LIMIT ? OFFSET ?",
                (job_id, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def transition_job(
        self,
        job_id: str,
        state: str,
        *,
        event: str,
        agent: str | None = None,
        tool: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if state not in JOB_STATES:
            raise ValueError("Unknown job state")
        now = self._timestamp()
        with self._lock, self._connection:
            row = self._connection.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            previous = row["state"]
            if state not in TRANSITIONS[previous]:
                raise ValueError(f"Invalid job state transition: {previous} -> {state}")
            self._connection.execute("UPDATE jobs SET state=?,updated_at=? WHERE job_id=?", (state, now, job_id))
            self._insert_event(job_id, event, agent, tool, duration_ms, previous, state)
            if state in TERMINAL_STATES:
                self._connection.execute("DELETE FROM pending_calls WHERE job_id=?", (job_id,))

    def create_pending_call(
        self,
        job_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ttl: timedelta = timedelta(minutes=10),
        agent: str | None = None,
    ) -> str:
        code = secrets.token_urlsafe(24)
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        now = self._timestamp()
        expires_at = (datetime.fromisoformat(now) + ttl).isoformat()
        if ttl <= timedelta(0):
            raise ValueError("Approval code lifetime must be positive")
        with self._lock, self._connection:
            row = self._connection.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["state"] != "running":
                raise ValueError("An approval call can be created only for a running job")
            self._connection.execute(
                "INSERT INTO pending_calls(job_id,tool_name,agent,arguments_blob,code_hash,expires_at) VALUES(?,?,?,?,?,?)",
                (job_id, tool_name, agent, self._seal(arguments), code_hash, expires_at),
            )
            self._connection.execute(
                "UPDATE jobs SET state='awaiting_approval',updated_at=? WHERE job_id=?", (now, job_id)
            )
            self._insert_event(job_id, "job.awaiting_approval", agent, tool_name, None, "running", "awaiting_approval")
        return code

    def get_pending_call(self, job_id: str, *, include_arguments: bool = False) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT tool_name,agent,expires_at,arguments_blob FROM pending_calls WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            result: dict[str, Any] = {
                "tool_name": row["tool_name"], "agent": row["agent"], "expires_at": row["expires_at"],
            }
            if include_arguments:
                result["arguments"] = json.loads(self.protector.unprotect(row["arguments_blob"]).decode("utf-8"))
            return result

    def approve_pending_call(
        self,
        job_id: str,
        code: str | None = None,
        *,
        dashboard: bool = False,
    ) -> bool:
        now = self._timestamp()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT code_hash,expires_at,approved,tool_name FROM pending_calls WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None or row["approved"]:
                return False
            if dashboard:
                valid = True
            else:
                if not isinstance(code, str) or row["expires_at"] <= now:
                    return False
                valid = hmac.compare_digest(
                    hashlib.sha256(code.encode("utf-8")).hexdigest(), row["code_hash"]
                )
            if not valid:
                return False
            job = self._connection.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None or job["state"] != "awaiting_approval":
                return False
            self._connection.execute("UPDATE pending_calls SET approved=1 WHERE job_id=?", (job_id,))
            self._connection.execute(
                "UPDATE jobs SET state='running',updated_at=? WHERE job_id=?", (now, job_id)
            )
            self._insert_event(job_id, "job.approved", None, row["tool_name"], None, "awaiting_approval", "running")
            return True

    def consume_approved_call(self, job_id: str) -> tuple[str, dict[str, Any]]:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT tool_name,arguments_blob,approved FROM pending_calls WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None or not row["approved"]:
                raise KeyError("No approved pending call")
            self._connection.execute("DELETE FROM pending_calls WHERE job_id=?", (job_id,))
            args = json.loads(self.protector.unprotect(row["arguments_blob"]).decode("utf-8"))
            return row["tool_name"], args

    def mark_running_interrupted(self) -> int:
        now = self._timestamp()
        with self._lock, self._connection:
            rows = self._connection.execute("SELECT job_id FROM jobs WHERE state='running'").fetchall()
            for row in rows:
                self._connection.execute(
                    "UPDATE jobs SET state='interrupted',updated_at=? WHERE job_id=?", (now, row["job_id"])
                )
                self._insert_event(row["job_id"], "job.interrupted", None, None, None, "running", "interrupted")
            return len(rows)

    def purge_expired(self) -> int:
        now = self._timestamp()
        tool_call_cutoff = _utc_timestamp(datetime.fromisoformat(now) - self.retention)
        with self._lock, self._connection:
            cursor = self._connection.execute("DELETE FROM jobs WHERE expires_at<=?", (now,))
            self._connection.execute("DELETE FROM tool_calls WHERE started_at<?", (tool_call_cutoff,))
            return cursor.rowcount

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

    def _insert_event(
        self, job_id: str, event: str, agent: str | None, tool: str | None,
        duration_ms: int | None, from_state: str | None, to_state: str | None,
        source_id: str | None = None,
    ) -> None:
        self._connection.execute(
            "INSERT INTO events(job_id,created_at,event,agent,tool,duration_ms,from_state,to_state,source_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (job_id, self._timestamp(), event, agent, tool, duration_ms, from_state, to_state, source_id),
        )

    def _job_row(self, row: sqlite3.Row) -> dict[str, Any]:
        summary = self.protector.unprotect(row["summary_blob"]).decode("utf-8") if row["summary_blob"] else None
        return {
            "job_id": row["job_id"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "state": row["state"], "summary": summary,
        }

    def _timestamp(self) -> str:
        return self.clock().astimezone(timezone.utc).isoformat()

    def _expiry(self, timestamp: str) -> str:
        return (datetime.fromisoformat(timestamp) + self.retention).isoformat()

    def _seal(self, value: Any) -> bytes:
        return self.protector.protect(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _activity_filters(
    start: datetime, end: datetime, *, server_id: str | None, client: str | None, tool: str | None,
) -> tuple[str, list[Any]]:
    clauses = ["started_at>=?", "started_at<?"]
    values: list[Any] = [_utc_timestamp(start), _utc_timestamp(end)]
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
