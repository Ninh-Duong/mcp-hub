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
from typing import Any, Protocol

from ..config import PRIVATE_ROOT
from ..security.dpapi import DPAPIProtector


JOB_STATES = {
    "queued", "awaiting_input", "running", "awaiting_approval",
    "completed", "partial", "failed", "interrupted", "cancelled",
}
TERMINAL_STATES = {"completed", "partial", "failed", "interrupted", "cancelled"}
TRANSITIONS = {
    "queued": {"running", "awaiting_input", "cancelled", "failed"},
    "awaiting_input": {"queued", "running", "cancelled", "failed"},
    "running": {"awaiting_input", "awaiting_approval", "completed", "partial", "failed", "interrupted", "cancelled"},
    "awaiting_approval": {"running", "partial", "failed", "cancelled"},
    "completed": set(), "partial": set(), "failed": set(), "interrupted": set(), "cancelled": set(),
}


class Protector(Protocol):
    def protect(self, data: bytes) -> bytes: ...
    def unprotect(self, data: bytes) -> bytes: ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript("""
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
            CREATE INDEX IF NOT EXISTS jobs_updated_idx ON jobs(updated_at DESC);
            CREATE INDEX IF NOT EXISTS events_job_idx ON events(job_id, event_id);
            CREATE INDEX IF NOT EXISTS details_expiry_idx ON details(expires_at);
        """)
        event_columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(events)")}
        if "source_id" not in event_columns:
            self._connection.execute("ALTER TABLE events ADD COLUMN source_id TEXT")
        pending_columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(pending_calls)")}
        if "agent" not in pending_columns:
            self._connection.execute("ALTER TABLE pending_calls ADD COLUMN agent TEXT")
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
        with self._lock, self._connection:
            cursor = self._connection.execute("DELETE FROM jobs WHERE expires_at<=?", (now,))
            return cursor.rowcount

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
