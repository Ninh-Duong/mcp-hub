"""Small, sanitized update status shared by the Hub and desktop app."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_DATA = "mcp-hub-status"
MAX_EVENTS = 40
MAX_COMPONENTS = 32
_ALLOWED_STATES = {"idle", "running", "partial", "complete"}


def state_path() -> Path:
    """The desktop-owned status file, outside the Hub checkout and private configuration."""
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "state")
    return root / APP_DATA / "update.json"


def empty_state() -> dict[str, Any]:
    return {"run_id": None, "state": "idle", "started_at": None, "updated_at": None,
            "components": [], "events": []}


def read(path: Path | None = None) -> dict[str, Any]:
    try:
        value = json.loads((path or state_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_state()
    if not isinstance(value, dict) or value.get("state") not in _ALLOWED_STATES:
        return empty_state()
    components = value.get("components")
    events = value.get("events")
    if not isinstance(components, list) or not isinstance(events, list):
        return empty_state()
    return {
        "run_id": _text(value.get("run_id"), 40),
        "state": value["state"],
        "started_at": _text(value.get("started_at"), 40),
        "updated_at": _text(value.get("updated_at"), 40),
        "components": [_component(item) for item in components[:MAX_COMPONENTS] if _safe_component(item)],
        "events": [_event(item) for item in events[-MAX_EVENTS:] if _safe_event(item)],
    }


def write(value: dict[str, Any], path: Path | None = None) -> None:
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    safe = {
        "run_id": _text(value.get("run_id"), 40),
        "state": value.get("state") if value.get("state") in _ALLOWED_STATES else "idle",
        "started_at": _text(value.get("started_at"), 40),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "components": [item for item in value.get("components", [])[:MAX_COMPONENTS] if _safe_component(item)],
        "events": [item for item in value.get("events", [])[-MAX_EVENTS:] if _safe_event(item)],
    }
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix="update-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(safe, stream, separators=(",", ":"))
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def add_event(value: dict[str, Any], component: str, phase: str, state: str, error_code: str | None = None) -> None:
    value.setdefault("events", []).append({"component": component[:128], "phase": phase[:32], "state": state[:16],
                                            "error_code": error_code[:40] if error_code else None,
                                            "at": datetime.now(timezone.utc).isoformat()})
    value["events"] = value["events"][-MAX_EVENTS:]


def _text(value: Any, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _safe_component(value: Any) -> bool:
    return isinstance(value, dict) and all(
        value.get(key) is None or isinstance(value.get(key), str)
        for key in ("id", "kind", "state", "phase", "error_code")
    )


def _safe_event(value: Any) -> bool:
    return isinstance(value, dict) and all(
        value.get(key) is None or isinstance(value.get(key), str)
        for key in ("component", "phase", "state", "error_code", "at")
    )


def _component(value: dict[str, Any]) -> dict[str, Any]:
    return {key: _text(value.get(key), limit) for key, limit in
            (("id", 128), ("kind", 16), ("state", 16), ("phase", 32), ("error_code", 40))}


def _event(value: dict[str, Any]) -> dict[str, Any]:
    return {key: _text(value.get(key), limit) for key, limit in
            (("component", 128), ("phase", 32), ("state", 16), ("error_code", 40), ("at", 40))}
