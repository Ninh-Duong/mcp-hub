from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterable

from .config import HUB_ROOT


_BEARER_RE = re.compile(r"(?i)((?:authorization\s*[:=]\s*)?(?:basic|bearer)\s+)[^\s,;]+")
_KEY_VALUE_RE = re.compile(
    r"(?i)([\"']?(?:api[_-]?token|access[_-]?token|refresh[_-]?token|password|secret|api[_-]?key)[\"']?\s*[:=]\s*[\"']?)[^\"'\s,;}]+"
)


def redact(value: str, secrets: Iterable[str] = ()) -> str:
    result = value
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        result = result.replace(secret, "[REDACTED]")
    result = _BEARER_RE.sub(r"\1[REDACTED]", result)
    result = _KEY_VALUE_RE.sub(r"\1[REDACTED]", result)
    return result[:1000]


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or HUB_ROOT / "logs" / "mcp-hub.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger("mcp_hub.audit")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        if not self._logger.handlers:
            handler = RotatingFileHandler(self.path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
        self._secrets: set[str] = set()

    def add_secrets(self, values: Iterable[str]) -> None:
        self._secrets.update(value for value in values if value)

    def write(self, event: str, level: int = logging.INFO, **fields: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        for key in ("error", "error_summary"):
            if isinstance(record.get(key), str):
                record[key] = redact(record[key], self._secrets)
        self._logger.log(level, json.dumps(record, ensure_ascii=False, default=str))
