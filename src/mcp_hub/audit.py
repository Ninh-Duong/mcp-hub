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
    r"(?i)([\"']?(?:[\w-]*token|password|passwd|pwd|secret|api[_-]?key|cookie)\\?[\"']?\s*[:=]\s*\\?[\"']?)[^\"'\\\s,;}]+"
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

    def redact_content(self, value: str, *, limit: int = 1000) -> str:
        return redact(value, self._secrets, limit=limit)

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
