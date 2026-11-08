"""Merging and normalization helpers for plan arguments and time windows."""
from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timezone
from typing import Any

from .extensions import PlanHints


def fold(text: str) -> str:
    """Casefold and strip diacritics (for example 'H\\u00f4m nay' -> 'hom nay')."""
    text = text.replace("\u0111", "d").replace("\u0110", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).casefold()


def parse_window(since: Any, until: Any, max_hours: int) -> tuple[datetime, datetime] | None:
    """Parse and validate an ISO-8601 time window into aware UTC datetimes.

    Returns None when both are None. Raises ValueError when only one is given,
    since >= until, or the span exceeds max_hours.
    """
    if since is None and until is None:
        return None
    if since is None or until is None:
        raise ValueError("Both 'since' and 'until' are required for a time window.")

    since_dt = _to_utc_datetime(since, "since")
    until_dt = _to_utc_datetime(until, "until")

    if since_dt >= until_dt:
        raise ValueError("'since' must be strictly before 'until'.")

    span_seconds = (until_dt - since_dt).total_seconds()
    if span_seconds > max_hours * 3600:
        raise ValueError(f"Time window span ({span_seconds / 3600:.1f}h) exceeds limit of {max_hours} hours.")

    return since_dt, until_dt


def _to_utc_datetime(value: Any, name: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid date-time format for '{name}': {value}") from exc
    else:
        raise ValueError(f"Invalid date-time for '{name}': expected string or datetime.")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(timezone.utc)


def merge_plan_arguments(arguments: Mapping[str, Any], hints: PlanHints | None) -> tuple[dict[str, Any], bool]:
    """Copy of arguments with missing playbook/servers/env/since/until filled from hints; (merged, used_any).

    Never overwrites a key the caller gave. Never adds servers when the caller gave a playbook.
    """
    merged = dict(arguments)
    if hints is None:
        return merged, False

    used = False
    if "playbook" not in merged and hints.playbook is not None:
        merged["playbook"] = hints.playbook
        used = True

    if "playbook" not in merged and "servers" not in merged and hints.servers:
        merged["servers"] = list(hints.servers)
        used = True

    if "env" not in merged and hints.env is not None:
        merged["env"] = hints.env
        used = True

    if "since" not in merged and "until" not in merged and hints.since is not None and hints.until is not None:
        merged["since"] = hints.since.isoformat()
        merged["until"] = hints.until.isoformat()
        used = True

    return merged, used


def roles_for_servers(servers: Iterable[str], routes: Mapping[str, Any]) -> list[str]:
    """Return the specialist agent roles whose tools route to any of the named child servers."""
    from .agents.specialists import ROLE_PREFIXES

    server_set = set(servers)
    matched: list[str] = []
    for role, prefix in ROLE_PREFIXES.items():
        for tool_name, route in routes.items():
            if tool_name.startswith(prefix) and getattr(route, "server_id", None) in server_set:
                if role not in matched:
                    matched.append(role)
                break
    return matched
