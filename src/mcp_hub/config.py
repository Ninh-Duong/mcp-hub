from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


HUB_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_ROOT = (HUB_ROOT / "config" / "private").resolve()
_DEFAULT_CONFIG = PRIVATE_ROOT / "hub.json"
SECRET_PLACEHOLDER = "<SET_BY_USER>"
_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SENSITIVE_KEY_RE = re.compile(r"token|secret|password|authorization|email|credential|api[_-]?key|(?:^|[_-])key$", re.IGNORECASE)


class ConfigError(ValueError):
    """Raised when Hub configuration is invalid or unsafe."""


@dataclass(frozen=True)
class ServerConfig:
    server_id: str
    command: str
    args: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    inherit_env: tuple[str, ...]
    timeout_seconds: float = 60.0
    secret_values: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class ToolRoute:
    feature: str
    server_id: str
    tool_name: str
    exposed_name: str


@dataclass(frozen=True)
class HubConfig:
    config_path: Path
    servers: dict[str, ServerConfig]
    routes: tuple[ToolRoute, ...]
    detected: tuple[str, ...] = ()
    unavailable: dict[str, str] = field(default_factory=dict)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string")
    return value.strip()


def _collect_sensitive_values(value: Any, sensitive_parent: bool = False) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            sensitive = sensitive_parent or bool(_SENSITIVE_KEY_RE.search(str(key)))
            found.extend(_collect_sensitive_values(child, sensitive))
    elif isinstance(value, list):
        for child in value:
            found.extend(_collect_sensitive_values(child, sensitive_parent))
    elif sensitive_parent and isinstance(value, str) and value:
        found.append(value)
    return found


def _private_file(path_value: Any, label: str) -> Path:
    relative = Path(_string(path_value, label))
    path = (HUB_ROOT / relative).resolve() if not relative.is_absolute() else relative.resolve()
    if not path.is_relative_to(PRIVATE_ROOT):
        raise ConfigError(f"{label} must point inside config/private/")
    if not path.is_file():
        raise ConfigError(f"{label} does not exist")
    return path


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read {label}: {type(exc).__name__}") from exc
    return _mapping(value, label)


def load_config(config_path: str | Path | None = None) -> HubConfig:
    override = config_path or os.environ.get("MCP_HUB_CONFIG")
    automatic = override is None
    configured_path = override or _DEFAULT_CONFIG
    path = Path(configured_path)
    if not path.is_absolute():
        path = HUB_ROOT / path
    path = path.resolve()
    if not path.is_relative_to(PRIVATE_ROOT):
        raise ConfigError("Hub config must be inside config/private/")
    detected: tuple[str, ...] = ()
    if automatic:
        defaults = _read_json(HUB_ROOT / "config" / "hub.example.json", "default Hub config")
        detected = tuple(server_id for server_id in defaults["servers"] if (HUB_ROOT.parent / server_id).is_dir())
        private = _read_json(path, "Hub config") if path.is_file() else {"version": 1, "servers": {}, "features": {}}
        private_servers = _mapping(private.get("servers"), "servers")
        private_features = _mapping(private.get("features"), "features")
        raw = {"version": private.get("version"),
               "servers": {**defaults["servers"], **{
                   key: {**defaults["servers"].get(key, {}), **value} if isinstance(value, dict) else value
                   for key, value in private_servers.items()}},
               "features": {**defaults["features"], **{
                   key: {**defaults["features"].get(key, {}), **value} if isinstance(value, dict) else value
                   for key, value in private_features.items()}}}
        explicit_servers = {key for key, value in private_servers.items() if isinstance(value, dict) and "enabled" in value}
        explicit_features = {key for key, value in private_features.items() if isinstance(value, dict) and "enabled" in value}
        explicit_files = {key for key, value in private_servers.items() if isinstance(value, dict) and "configFile" in value}
    else:
        raw = _read_json(path, "Hub config")
        explicit_servers = set(raw.get("servers", {})) if isinstance(raw.get("servers"), dict) else set()
        explicit_features = set(raw.get("features", {})) if isinstance(raw.get("features"), dict) else set()
        explicit_files = explicit_servers
    if type(raw.get("version")) is not int or raw["version"] != 1:
        raise ConfigError("Unsupported Hub config version")

    raw_servers = _mapping(raw.get("servers"), "servers")
    servers: dict[str, ServerConfig] = {}
    unavailable: dict[str, str] = {}
    for server_id, value in raw_servers.items():
        if not isinstance(server_id, str) or not server_id.strip():
            raise ConfigError("Server IDs must be non-empty strings")
        item = _mapping(value, f"server {server_id}")
        enabled = item.get("enabled") if not automatic or server_id in explicit_servers else server_id in detected
        if enabled is not True:
            if server_id in detected:
                unavailable[server_id] = "disabled"
            continue
        if automatic and server_id not in explicit_files:
            private_path = PRIVATE_ROOT / "mcps" / f"{server_id}.json"
            server_config_path = private_path if private_path.is_file() else HUB_ROOT / "config" / "mcps" / f"{server_id}.example.json"
        else:
            server_config_path = _private_file(item.get("configFile"), f"server {server_id} configFile")
        local = _read_json(server_config_path, f"server {server_id} config")
        if SECRET_PLACEHOLDER in json.dumps(local):
            if automatic:
                unavailable[server_id] = "credentials required"
                continue
            raise ConfigError(f"server {server_id} has unfilled {SECRET_PLACEHOLDER} values in its configFile")
        if local.get("transport") != "stdio":
            raise ConfigError(f"server {server_id} uses an unsupported transport")
        command = _string(local.get("command"), f"server {server_id} command")
        args_value = local.get("args", [])
        if not isinstance(args_value, list) or not all(isinstance(arg, str) for arg in args_value):
            raise ConfigError(f"server {server_id} args must be a list of strings")
        cwd = HUB_ROOT / Path(_string(local.get("cwd"), f"server {server_id} cwd")).expanduser()
        cwd = cwd.resolve()
        if not cwd.is_dir():
            raise ConfigError(f"server {server_id} cwd does not exist")
        if not Path(command).is_absolute() and ("/" in command or "\\" in command):
            command = str((cwd / command).resolve())
        if automatic and server_id == "mcp-jira-sync" and server_id not in explicit_files:
            command = str(HUB_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        env = _mapping(local.get("env", {}), f"server {server_id} env")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in env.items()):
            raise ConfigError(f"server {server_id} env must contain string values")
        inherit_env = local.get("inheritEnv", ["PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE"])
        if not isinstance(inherit_env, list) or not all(isinstance(name, str) for name in inherit_env):
            raise ConfigError(f"server {server_id} inheritEnv must be a list of names")
        timeout = local.get("timeoutSeconds", 60)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ConfigError(f"server {server_id} timeoutSeconds must be a positive number")
        try:
            timeout_seconds = float(timeout)
        except OverflowError as exc:
            raise ConfigError(f"server {server_id} timeoutSeconds must be a positive finite number") from exc
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ConfigError(f"server {server_id} timeoutSeconds must be a positive finite number")
        servers[server_id] = ServerConfig(
            server_id=server_id,
            command=command,
            args=tuple(args_value),
            cwd=cwd,
            env=dict(env),
            inherit_env=tuple(inherit_env),
            timeout_seconds=timeout_seconds,
            secret_values=tuple(_collect_sensitive_values(local)),
        )

    raw_features = _mapping(raw.get("features"), "features")
    routes: list[ToolRoute] = []
    exposed_names: set[str] = set()
    for feature, value in raw_features.items():
        item = _mapping(value, f"feature {feature}")
        enabled = item.get("enabled") if not automatic or feature in explicit_features else any(
            tool.get("server") in detected for tool in item.get("tools", []) if isinstance(tool, dict)
        )
        if enabled is not True:
            continue
        tools = item.get("tools")
        if not isinstance(tools, list):
            raise ConfigError(f"feature {feature} tools must be a list")
        for tool in tools:
            tool = _mapping(tool, f"feature {feature} tool")
            server_id = _string(tool.get("server"), f"feature {feature} server")
            tool_name = _string(tool.get("name"), f"feature {feature} tool name")
            exposed_name = str(tool.get("exposeAs") or f"{server_id}__{tool_name}")
            if exposed_name == "hub__status":
                raise ConfigError("hub__status is reserved for the Hub")
            if not _ALIAS_RE.fullmatch(exposed_name):
                raise ConfigError(f"Invalid exposed tool name in feature {feature}")
            if exposed_name in exposed_names:
                raise ConfigError(f"Duplicate exposed tool name: {exposed_name}")
            if server_id in unavailable and automatic:
                continue
            if server_id not in servers:
                raise ConfigError(f"feature {feature} refers to a disabled or unknown server")
            exposed_names.add(exposed_name)
            routes.append(ToolRoute(feature, server_id, tool_name, exposed_name))

    if not routes and not automatic:
        raise ConfigError("Enable at least one feature with an allowlisted tool")
    return HubConfig(config_path=path, servers=servers, routes=tuple(routes), detected=detected, unavailable=unavailable)
