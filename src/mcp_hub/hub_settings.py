"""Hub settings the desktop app may change: child MCPs on or off, and the saved activity-UI mode.

Only `servers.<id>.enabled` booleans and the top-level `extensions` list are ever written to
`config/private/hub.json`, so the file stays free of secrets; every other key is kept as it is.
Changes apply at the next Hub start.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config
from .extensions import ACTIVITY_UI, BITNET, EXTENSIONS_KEY, KNOWN_EXTENSIONS, saved_extensions

_EMPTY_CONFIG = {"version": 1, "servers": {}, "features": {}}


@dataclass(frozen=True)
class HubSettings:
    activity_ui: bool
    servers: tuple[config.ServerSwitch, ...]
    # False with MCP_HUB_CONFIG set: that file belongs to the user, so the app only shows it.
    editable: bool
    bitnet: bool = False


def read_settings(config_path: str | Path | None = None) -> HubSettings:
    path = config.resolved_config_path(config_path)
    saved = saved_extensions(path)
    return HubSettings(
        activity_ui=ACTIVITY_UI in saved,
        servers=config.server_switches(config_path),
        editable=config.uses_default_config(config_path),
        bitnet=BITNET in saved,
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


def prepare_child_config(server_id: str, config_path: str | Path | None = None) -> Path:
    """The child's private config file, created from its template when it does not exist yet.

    Only the template is copied; nothing is read back, so the file's values never reach the caller.
    """
    if not config.uses_default_config(config_path):
        raise config.ConfigError("MCP_HUB_CONFIG is set; change that file instead")
    if server_id not in {switch.server_id for switch in config.server_switches(config_path)}:
        raise KeyError(server_id)
    target = config.child_config_file(server_id, config_path)
    if not target.is_file():
        template = config.HUB_ROOT / "config" / "mcps" / f"{server_id}.example.json"
        if not template.is_file():
            raise config.ConfigError("No template for this child MCP")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, target)
    return target


def set_extension(name: str, enabled: bool, config_path: str | Path | None = None) -> None:
    """Add or remove an extension from the saved extensions list."""
    if name not in KNOWN_EXTENSIONS:
        raise config.ConfigError("Unknown extension")
    raw = _editable_config(config_path)
    existing = raw.get(EXTENSIONS_KEY, [])
    if not isinstance(existing, list):
        existing = []
    current = [item for item in existing if isinstance(item, str)]
    if enabled:
        if name not in current:
            current.append(name)
    else:
        current = [item for item in current if item != name]
    if current:
        raw[EXTENSIONS_KEY] = current
    else:
        raw.pop(EXTENSIONS_KEY, None)
    _write(config.resolved_config_path(config_path), raw)


def set_activity_ui(enabled: bool, config_path: str | Path | None = None) -> None:
    """Remember the activity UI for every later start, or go back to fast mode."""
    set_extension(ACTIVITY_UI, enabled, config_path=config_path)


def _editable_config(config_path: str | Path | None) -> dict[str, Any]:
    if not config.uses_default_config(config_path):
        raise config.ConfigError("MCP_HUB_CONFIG is set; change that file instead")
    path = config.resolved_config_path(config_path)
    if not path.is_file():
        return json.loads(json.dumps(_EMPTY_CONFIG))
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))  # the same reading as config._read_json
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
