from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


HUB_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_ROOT = (HUB_ROOT / "config" / "private").resolve()
_DEFAULT_CONFIG = PRIVATE_ROOT / "hub.json"
_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SENSITIVE_KEY_RE = re.compile(r"token|secret|password|authorization|email|credential", re.IGNORECASE)


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


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string")
    return value.strip()


def _collect_sensitive_values(value: Any, parent_key: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if _SENSITIVE_KEY_RE.search(str(key)) and isinstance(child, str) and child:
                found.append(child)
            else:
                found.extend(_collect_sensitive_values(child, str(key)))
    elif isinstance(value, list):
        for child in value:
            found.extend(_collect_sensitive_values(child, parent_key))
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
    configured_path = config_path or os.environ.get("MCP_HUB_CONFIG") or _DEFAULT_CONFIG
    path = Path(configured_path)
    if not path.is_absolute():
        path = HUB_ROOT / path
    path = path.resolve()
    if not path.is_relative_to(PRIVATE_ROOT):
        raise ConfigError("Hub config must be inside config/private/")
    raw = _read_json(path, "Hub config")
    if raw.get("version") != 1:
        raise ConfigError("Unsupported Hub config version")

    raw_servers = _mapping(raw.get("servers"), "servers")
    servers: dict[str, ServerConfig] = {}
    for server_id, value in raw_servers.items():
        if not isinstance(server_id, str) or not server_id.strip():
            raise ConfigError("Server IDs must be non-empty strings")
        item = _mapping(value, f"server {server_id}")
        if item.get("enabled") is not True:
            continue
        server_config_path = _private_file(item.get("configFile"), f"server {server_id} configFile")
        local = _read_json(server_config_path, f"server {server_id} config")
        if local.get("transport") != "stdio":
            raise ConfigError(f"server {server_id} uses an unsupported transport")
        command = _string(local.get("command"), f"server {server_id} command")
        args_value = local.get("args", [])
        if not isinstance(args_value, list) or not all(isinstance(arg, str) for arg in args_value):
            raise ConfigError(f"server {server_id} args must be a list of strings")
        cwd = Path(_string(local.get("cwd"), f"server {server_id} cwd")).expanduser().resolve()
        if not cwd.is_dir():
            raise ConfigError(f"server {server_id} cwd does not exist")
        env = _mapping(local.get("env", {}), f"server {server_id} env")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in env.items()):
            raise ConfigError(f"server {server_id} env must contain string values")
        inherit_env = local.get("inheritEnv", ["PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE"])
        if not isinstance(inherit_env, list) or not all(isinstance(name, str) for name in inherit_env):
            raise ConfigError(f"server {server_id} inheritEnv must be a list of names")
        servers[server_id] = ServerConfig(
            server_id=server_id,
            command=command,
            args=tuple(args_value),
            cwd=cwd,
            env=dict(env),
            inherit_env=tuple(inherit_env),
            secret_values=tuple(_collect_sensitive_values(local)),
        )

    raw_features = _mapping(raw.get("features"), "features")
    routes: list[ToolRoute] = []
    exposed_names: set[str] = set()
    for feature, value in raw_features.items():
        item = _mapping(value, f"feature {feature}")
        if item.get("enabled") is not True:
            continue
        tools = item.get("tools")
        if not isinstance(tools, list):
            raise ConfigError(f"feature {feature} tools must be a list")
        for tool in tools:
            tool = _mapping(tool, f"feature {feature} tool")
            server_id = _string(tool.get("server"), f"feature {feature} server")
            tool_name = _string(tool.get("name"), f"feature {feature} tool name")
            exposed_name = str(tool.get("exposeAs") or f"{server_id}__{tool_name}")
            if not _ALIAS_RE.fullmatch(exposed_name):
                raise ConfigError(f"Invalid exposed tool name in feature {feature}")
            if exposed_name in exposed_names:
                raise ConfigError(f"Duplicate exposed tool name: {exposed_name}")
            if server_id not in servers:
                raise ConfigError(f"feature {feature} refers to a disabled or unknown server")
            exposed_names.add(exposed_name)
            routes.append(ToolRoute(feature, server_id, tool_name, exposed_name))

    if not routes:
        raise ConfigError("Enable at least one feature with an allowlisted tool")
    return HubConfig(config_path=path, servers=servers, routes=tuple(routes))
