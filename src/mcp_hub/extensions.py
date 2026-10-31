"""Optional Hub extensions: the observer contract and the start-time launch flags.

The core tool-call path (`server`, `http`, `dashboard/api.py` core routes) never imports an extension.
`service.py` reads the launch flags once at start and installs only the requested extensions.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .config import PRIVATE_ROOT, ConfigError, resolved_config_path

ACTIVITY_UI = "activity-ui"
# Top-level key of the Hub config that keeps extensions on across starts.
EXTENSIONS_KEY = "extensions"
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


def saved_extensions(config_path: Path | None = None) -> frozenset[str]:
    """Extensions kept on for every start: the `extensions` list in the Hub config (set from the desktop app).

    Never raises: a missing, unreadable, or malformed file means none, and the Hub starts in fast mode.
    """
    try:
        raw = json.loads((config_path or resolved_config_path()).read_text(encoding="utf-8-sig"))
        names = raw.get(EXTENSIONS_KEY, [])
    except (OSError, ValueError, AttributeError, ConfigError):
        return frozenset()
    if not isinstance(names, list):
        return frozenset()
    return frozenset(name for name in names if isinstance(name, str))


def read_launch_extensions(path: Path = LAUNCH_FILE, audit: Any = None) -> frozenset[str]:
    """Return the extensions requested for this start, then delete the request.

    `hubctl start|restart|open -Ui` writes the file; deleting it makes the next start
    (including the logon-triggered one) use the saved mode (`saved_extensions`), fast by default.
    A file that cannot be read, decoded, parsed, or deleted never stops the start: it is logged as
    `launch.file.invalid` (when `audit` is given) and adds nothing.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return frozenset()
    except (OSError, UnicodeDecodeError) as exc:
        return _invalid_launch_file(audit, type(exc).__name__)
    finally:
        _remove_launch_file(path, audit)
    try:
        names = json.loads(text).get("extensions")
    except (ValueError, AttributeError) as exc:
        return _invalid_launch_file(audit, type(exc).__name__)
    if not isinstance(names, list):
        return _invalid_launch_file(audit, "ValueError")
    return frozenset(name for name in names if isinstance(name, str))


def _remove_launch_file(path: Path, audit: Any) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _invalid_launch_file(audit, type(exc).__name__)


def _invalid_launch_file(audit: Any, error_type: str) -> frozenset[str]:
    if audit is not None:
        audit.write("launch.file.invalid", level=logging.WARNING, error_type=error_type)
    return frozenset()
