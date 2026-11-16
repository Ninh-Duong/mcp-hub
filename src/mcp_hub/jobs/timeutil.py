"""UTC time helpers for the job store and its activity reports. Leaf module: no mcp_hub imports."""
from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()
