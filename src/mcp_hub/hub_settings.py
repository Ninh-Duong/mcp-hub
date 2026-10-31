"""Hub settings the desktop app may change: child MCPs on or off, and the saved activity-UI mode.

Only `servers.<id>.enabled` booleans and the top-level `extensions` list are ever written to
`config/private/hub.json`, so the file stays free of secrets; every other key is kept as it is.
Changes apply at the next Hub start.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config
from .extensions import ACTIVITY_UI, EXTENSIONS_KEY, saved_extensions

_EMPTY_CONFIG = {"version": 1, "servers": {}, "features": {}}


@dataclass(frozen=True)
class HubSettings:
    activity_ui: bool
    servers: tuple[config.ServerSwitch, ...]
    # False with MCP_HUB_CONFIG set: that file belongs to the user, so the app only shows it.
    editable: bool


def read_settings(config_path: str | Path | None = None) -> HubSettings:
    path = config.resolved_config_path(config_path)
    return HubSettings(
        activity_ui=ACTIVITY_UI in saved_extensions(path),
        servers=config.server_switches(config_path),
        editable=config.uses_default_config(config_path),
    )


def set_server_enabled(server_id: str, enabled: bool, config_path: str | Path | None = None) -> None:
    if server_id not in {switch.server_id for switch in config.server_switches(config_path)}:
        raise config.ConfigError("Unknown child MCP")
    raw = _editable_config(config_path)
    servers = raw.setdefault("servers", {})
    if not isinstance(servers, dict) or not isinstance(servers.setdefault(server_id, {}), dict):
        raise config.ConfigError("servers must be an object")
    servers[server_id]["enabled"] = bool(enabled)
    _write(config.resolved_config_path(config_path), raw)


def set_activity_ui(enabled: bool, config_path: str | Path | None = None) -> None:
    """Remember the activity UI for every later start, or go back to fast mode."""
    raw = _editable_config(config_path)
    if enabled:
        raw[EXTENSIONS_KEY] = [ACTIVITY_UI]
    else:
        raw.pop(EXTENSIONS_KEY, None)
    _write(config.resolved_config_path(config_path), raw)


def _editable_config(config_path: str | Path | None) -> dict[str, Any]:
    if not config.uses_default_config(config_path):
        raise config.ConfigError("MCP_HUB_CONFIG is set; change that file instead")
    path = config.resolved_config_path(config_path)
    if not path.is_file():
        return json.loads(json.dumps(_EMPTY_CONFIG))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise config.ConfigError(f"Could not read Hub config: {type(exc).__name__}") from exc
    if not isinstance(raw, dict):
        raise config.ConfigError("Hub config must be an object")
    return raw


def _write(path: Path, raw: dict[str, Any]) -> None:
    """Replace the file in one step, so the Hub never reads half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".hub-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(raw, file, indent=2)
            file.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
