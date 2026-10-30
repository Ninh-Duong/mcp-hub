from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterable

from .config import HUB_ROOT


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


def redact(value: str, secrets: Iterable[str] = (), *, limit: int = 1000) -> str:
    result = value
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        result = result.replace(secret, "[REDACTED]")
    result = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", result)
    result = _URL_CREDENTIALS_RE.sub(r"\1[REDACTED]@", result)
    result = _BEARER_RE.sub(r"\1[REDACTED]", result)
    result = _KEY_VALUE_RE.sub(r"\1[REDACTED]", result)
    result = _TOKEN_SHAPE_RE.sub("[REDACTED_TOKEN]", result)
    return result[:limit]


LOG_DIR_ENV = "MCP_HUB_LOG_DIR"
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3
# One rotating handler per resolved log file in this process, shared by every AuditLog writing to it.
_handlers: dict[Path, tuple[RotatingFileHandler, int]] = {}
_handlers_lock = threading.Lock()


def default_log_dir() -> Path:
    """`logs/` in this checkout, or the directory named by `MCP_HUB_LOG_DIR` (used by tests and tools)."""
    override = os.environ.get(LOG_DIR_ENV)
    return Path(override) if override else HUB_ROOT / "logs"


def default_audit_path() -> Path:
    return default_log_dir() / "mcp-hub.jsonl"


def _acquire_handler(path: Path) -> RotatingFileHandler:
    with _handlers_lock:
        handler, users = _handlers.get(path, (None, 0))
        if handler is None:
            handler = RotatingFileHandler(
                path, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8", delay=True,
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
        _handlers[path] = (handler, users + 1)
        return handler


def _release_handler(path: Path) -> None:
    with _handlers_lock:
        handler, users = _handlers.get(path, (None, 0))
        if handler is None:
            return
        if users > 1:
            _handlers[path] = (handler, users - 1)
            return
        del _handlers[path]
    handler.close()


class AuditLog:
    """Redacted JSONL audit events, written to `path` (default: `default_audit_path()`)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_audit_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._key = self.path.resolve()
        self._handler: RotatingFileHandler | None = _acquire_handler(self._key)
        self._secrets: set[str] = set()

    def close(self) -> None:
        """Release the log file; later writes are dropped. Safe to call twice."""
        if self._handler is not None:
            self._handler = None
            _release_handler(self._key)

    def add_secrets(self, values: Iterable[str]) -> None:
        self._secrets.update(value for value in values if value)

    def redact_content(self, value: str, *, limit: int = 1000) -> str:
        return redact(value, self._secrets, limit=limit)

    def write(self, event: str, level: int = logging.INFO, **fields: Any) -> None:
        handler = self._handler
        if handler is None or level < logging.INFO:
            return
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        for key in ("error", "error_summary"):
            if isinstance(record.get(key), str):
                record[key] = redact(record[key], self._secrets)
        line = json.dumps(record, ensure_ascii=False, default=str)
        handler.handle(logging.makeLogRecord({
            "name": "mcp_hub.audit", "msg": line, "levelno": level, "levelname": logging.getLevelName(level),
        }))
