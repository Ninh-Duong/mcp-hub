"""Optional Hub extensions: the observer contract and the start-time launch flags.

The core tool-call path (`server`, `http`, `dashboard/api.py` core routes) never imports an extension.
`service.py` reads the launch flags once at start and installs only the requested extensions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .config import PRIVATE_ROOT

ACTIVITY_UI = "activity-ui"
KNOWN_EXTENSIONS = frozenset({ACTIVITY_UI})
# Dashboard status flag for each extension, always reported so the UI knows what is installed.
STATUS_FLAGS = {ACTIVITY_UI: "activityUi"}
LAUNCH_FILE = PRIVATE_ROOT / "launch.json"


class CallObserver(Protocol):
    """Receives call and client metadata; never tool arguments or results.

    The Hub calls observers only when at least one is installed, so the default path pays nothing.
    Observer methods run on the event loop and must not block.
    """

    def call_started(self, server_id: str, tool_name: str, client: str, agent_role: str | None) -> Any:
        """Return a token that is passed back to `call_finished`."""

    def call_finished(self, token: Any, state: str, error_type: str | None, duration_ms: int) -> None:
        """`state` is succeeded, failed, or interrupted."""

    def client_seen(self, ctx: Any) -> None:
        """An MCP request arrived with this request context."""


class ActivitySource(Protocol):
    """Live activity the dashboard can show; provided by the activity UI extension."""

    def activity_snapshot(self, server_ids: tuple[str, ...], tool_counts: dict[str, int]) -> dict[str, Any]: ...

    def client_snapshot(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class DashboardExtension:
    """What an extension adds to the dashboard: routes, status fields, and the hubctl flags that keep it on."""

    name: str
    routes: tuple[Any, ...] = ()
    status_fields: Callable[[], dict[str, Any]] = field(default=lambda: {})
    hubctl_flags: tuple[str, ...] = ()


def empty_activity() -> dict[str, Any]:
    return {"children": {}, "events": [], "sequence": 0, "stream_id": None}


def status_flags(installed: tuple[str, ...] | frozenset[str]) -> dict[str, bool]:
    return {flag: name in installed for name, flag in STATUS_FLAGS.items()}


def read_launch_extensions(path: Path = LAUNCH_FILE) -> frozenset[str]:
    """Return the extensions requested for this start, then delete the request.

    `hubctl start|restart|open -Ui` writes the file; deleting it makes the next start
    (including the logon-triggered one) use the default fast mode.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return frozenset()
    finally:
        path.unlink(missing_ok=True)
    try:
        names = json.loads(text).get("extensions")
    except (ValueError, AttributeError):
        return frozenset()
    if not isinstance(names, list):
        return frozenset()
    return frozenset(name for name in names if isinstance(name, str))
