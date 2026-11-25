from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
from dataclasses import dataclass
from datetime import datetime, UTC
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from typing import Any
from collections.abc import Iterable

from .config import HUB_ROOT
from .settings import LogSettings


_BEARER_RE = re.compile(r"(?i)((?:authorization\s*[:=]\s*)?(?:basic|bearer)\s+)[^\s,;]+")
# `(?<![\w-])` starts the `*token` name at a word start: same matches, linear time on long words.
_KEY_VALUE_RE = re.compile(
    r"(?i)([\"']?(?:(?<![\w-])[\w-]*token|password|passwd|pwd|secret|api[_-]?key|cookie)\\?[\"']?\s*[:=]\s*\\?[\"']?)[^\"'\\\s,;}]+"
)
# Well-known credential shapes: OpenAI/Anthropic, GitHub, AWS, Atlassian, Slack, Google, JWT.
_TOKEN_SHAPE_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}"
    r"|ATATT[A-Za-z0-9_=-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[A-Za-z0-9_-]{30,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.DOTALL)
_URL_CREDENTIALS_RE = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")
SENSITIVE_FIELD_RE = re.compile(r"(?i)(?:token|secret|password|authorization|api[_-]?key|credential|email|user(?:name|_id)?)")


def describe_exception(exc: BaseException) -> tuple[str, str]:
    """The type name and message for an audit record, from the first leaf of a (nested) exception group.

    `str()` of a task-group failure is only "unhandled errors in a TaskGroup"; the leaf names the cause.
    The message is still redacted by `AuditLog.write` when it is logged as `error`.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return type(exc).__name__, str(exc)


def redact_secrets(value: str, secrets: Iterable[str] = ()) -> str:
    """Redact known secrets and high-confidence credential token shapes without truncating."""
    result = value
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        result = result.replace(secret, "[REDACTED]")
    result = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", result)
    result = _URL_CREDENTIALS_RE.sub(r"\1[REDACTED]@", result)
    result = _BEARER_RE.sub(r"\1[REDACTED]", result)
    return _TOKEN_SHAPE_RE.sub("[REDACTED_TOKEN]", result)


def redact(value: str, secrets: Iterable[str] = (), *, limit: int = 1000) -> str:
    result = redact_secrets(value, secrets)
    result = _KEY_VALUE_RE.sub(r"\1[REDACTED]", result)
    return result[:limit]


LOG_DIR_ENV = "MCP_HUB_LOG_DIR"


class _SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that skips rollover when Windows briefly holds a file lock."""

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            # ponytail: skip one rotation cycle on Windows AV lock; next write retries rollover.
            if self.stream is None and not self.delay:
                self.stream = self._open()


@dataclass
class _HandlerSlot:
    file_handler: RotatingFileHandler
    queue: queue.Queue[logging.LogRecord] | None
    queue_handler: QueueHandler | None
    listener: QueueListener | None
    users: int


# One rotating handler per resolved log file in this process, shared by every AuditLog writing to it.
_handlers: dict[Path, _HandlerSlot] = {}
_handlers_lock = threading.Lock()


def default_log_dir() -> Path:
    """`logs/` in this checkout, or the directory named by `MCP_HUB_LOG_DIR` (used by tests and tools)."""
    override = os.environ.get(LOG_DIR_ENV)
    return Path(override) if override else HUB_ROOT / "logs"


def default_audit_path() -> Path:
    return default_log_dir() / "mcp-hub.jsonl"


def _acquire_handler(path: Path, logs: LogSettings, *, async_writes: bool = False) -> _HandlerSlot:
    """The first AuditLog for a file sets its rotation; later ones share that handler."""
    with _handlers_lock:
        slot = _handlers.get(path)
        if slot is None:
            file_handler = _SafeRotatingFileHandler(
                path, maxBytes=logs.max_bytes, backupCount=logs.backups, encoding="utf-8", delay=True,
            )
            file_handler.setFormatter(logging.Formatter("%(message)s"))
            slot = _HandlerSlot(file_handler, None, None, None, 0)
            _handlers[path] = slot
        if async_writes and slot.listener is None:
            record_queue: queue.Queue[logging.LogRecord] = queue.Queue()
            slot.queue = record_queue
            slot.queue_handler = QueueHandler(record_queue)
            slot.listener = QueueListener(record_queue, slot.file_handler, respect_handler_level=True)
            slot.listener.start()
        slot.users += 1
        return slot


def _release_handler(path: Path) -> None:
    with _handlers_lock:
        slot = _handlers.get(path)
        if slot is None:
            return
        if slot.users > 1:
            slot.users -= 1
            return
        del _handlers[path]
    if slot.listener is not None:
        slot.listener.stop()
    slot.file_handler.close()


_DEFAULT_LOG_SETTINGS = LogSettings()


class AuditLog:
    """Redacted JSONL audit events, written to `path` (default: `default_audit_path()`)."""

    def __init__(
        self,
        path: Path | None = None,
        logs: LogSettings = _DEFAULT_LOG_SETTINGS,
        *,
        async_writes: bool = False,
    ) -> None:
        self.path = path or default_audit_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._key = self.path.resolve()
        self._async_writes = async_writes
        self._slot: _HandlerSlot | None = _acquire_handler(self._key, logs, async_writes=async_writes)
        self._secrets: set[str] = set()

    def flush(self) -> None:
        """Wait for any queued background audit records to hit disk."""
        slot = self._slot
        if slot is not None and slot.queue is not None:
            slot.queue.join()

    def close(self) -> None:
        """Release the log file; later writes are dropped. Safe to call twice."""
        if self._slot is not None:
            self._slot = None
            _release_handler(self._key)

    def add_secrets(self, values: Iterable[str]) -> None:
        self._secrets.update(value for value in values if value)

    def redact_content(self, value: str, *, limit: int = 1000) -> str:
        return redact(value, self._secrets, limit=limit)

    def redact_result_text(self, value: str) -> str:
        """Redact configured child secrets and high-confidence credential shapes without truncating."""
        return redact_secrets(value, self._secrets)

    def write(self, event: str, level: int = logging.INFO, **fields: Any) -> None:
        slot = self._slot
        if slot is None or level < logging.INFO:
            return
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            **fields,
        }
        for key in ("error", "error_summary"):
            if isinstance(record.get(key), str):
                record[key] = redact(record[key], self._secrets)
        line = json.dumps(record, ensure_ascii=False, default=str)
        log_record = logging.makeLogRecord({
            "name": "mcp_hub.audit", "msg": line, "levelno": level, "levelname": logging.getLevelName(level),
        })
        handler = slot.queue_handler if (self._async_writes and slot.queue_handler is not None) else slot.file_handler
        handler.handle(log_record)
