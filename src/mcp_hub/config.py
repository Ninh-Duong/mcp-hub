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
_VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
_DEFAULT_INHERIT_ENV = ["PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE"]


class ConfigError(ValueError):
    """Raised when Hub configuration is invalid or unsafe."""


@dataclass(frozen=True)
class RuntimeRequirement:
    name: str
    min_version: tuple[int, ...]
    winget_id: str | None = None


@dataclass(frozen=True)
class PrepareSpec:
    command: tuple[str, ...]
    runtime: RuntimeRequirement | None = None
    timeout_seconds: float = 900.0


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
    prepare: PrepareSpec | None = None


@dataclass(frozen=True)
class ToolRoute:
    feature: str
    server_id: str
    tool_name: str
    exposed_name: str
    cache_seconds: float = 0.0


@dataclass(frozen=True)
class HubConfig:
    config_path: Path
    servers: dict[str, ServerConfig]
    routes: tuple[ToolRoute, ...]
    detected: tuple[str, ...] = ()
    unavailable: dict[str, str] = field(default_factory=dict)


@dataclass
class _LoadContext:
    automatic: bool
    detected: tuple[str, ...] = ()
    explicit_servers: set[str] = field(default_factory=set)
    explicit_features: set[str] = field(default_factory=set)
    explicit_files: set[str] = field(default_factory=set)
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
    path, automatic = _resolve_config_path(config_path)
    raw, context = _merge_with_defaults(path) if automatic else _read_explicit(path)
    if type(raw.get("version")) is not int or raw["version"] != 1:
        raise ConfigError("Unsupported Hub config version")
    servers = _load_servers(_mapping(raw.get("servers"), "servers"), context)
    routes = _load_routes(_mapping(raw.get("features"), "features"), servers, context)
    if not routes and not automatic:
        raise ConfigError("Enable at least one feature with an allowlisted tool")
    return HubConfig(config_path=path, servers=servers, routes=routes,
                     detected=context.detected, unavailable=context.unavailable)


def _resolve_config_path(config_path: str | Path | None) -> tuple[Path, bool]:
    override = config_path or os.environ.get("MCP_HUB_CONFIG")
    path = Path(override or _DEFAULT_CONFIG)
    if not path.is_absolute():
        path = HUB_ROOT / path
    path = path.resolve()
    if not path.is_relative_to(PRIVATE_ROOT):
        raise ConfigError("Hub config must be inside config/private/")
    return path, override is None


def _overlay(defaults: dict[str, Any], private: dict[str, Any]) -> dict[str, Any]:
    merged = {key: {**defaults.get(key, {}), **value} if isinstance(value, dict) else value
              for key, value in private.items()}
    return {**defaults, **merged}


def _keys_with(items: dict[str, Any], key: str) -> set[str]:
    return {name for name, value in items.items() if isinstance(value, dict) and key in value}


def _merge_with_defaults(path: Path) -> tuple[dict[str, Any], _LoadContext]:
    defaults = _read_json(HUB_ROOT / "config" / "hub.example.json", "default Hub config")
    detected = tuple(server_id for server_id in defaults["servers"] if (HUB_ROOT.parent / server_id).is_dir())
    private = _read_json(path, "Hub config") if path.is_file() else {"version": 1, "servers": {}, "features": {}}
    private_servers = _mapping(private.get("servers"), "servers")
    private_features = _mapping(private.get("features"), "features")
    raw = {"version": private.get("version"),
           "servers": _overlay(defaults["servers"], private_servers),
           "features": _overlay(defaults["features"], private_features)}
    context = _LoadContext(
        automatic=True,
        detected=detected,
        explicit_servers=_keys_with(private_servers, "enabled"),
        explicit_features=_keys_with(private_features, "enabled"),
        explicit_files=_keys_with(private_servers, "configFile"),
    )
    return raw, context


def _read_explicit(path: Path) -> tuple[dict[str, Any], _LoadContext]:
    raw = _read_json(path, "Hub config")
    servers = raw.get("servers")
    features = raw.get("features")
    explicit_servers = set(servers) if isinstance(servers, dict) else set()
    explicit_features = set(features) if isinstance(features, dict) else set()
    context = _LoadContext(automatic=False, explicit_servers=explicit_servers,
                           explicit_features=explicit_features, explicit_files=explicit_servers)
    return raw, context


def _load_servers(raw_servers: dict[str, Any], context: _LoadContext) -> dict[str, ServerConfig]:
    servers: dict[str, ServerConfig] = {}
    for server_id, value in raw_servers.items():
        if not isinstance(server_id, str) or not server_id.strip():
            raise ConfigError("Server IDs must be non-empty strings")
        server = _load_server(server_id, _mapping(value, f"server {server_id}"), context)
        if server is not None:
            servers[server_id] = server
    return servers


def _server_enabled(server_id: str, item: dict[str, Any], context: _LoadContext) -> bool:
    if not context.automatic or server_id in context.explicit_servers:
        return item.get("enabled") is True
    return server_id in context.detected


def _server_file(server_id: str, item: dict[str, Any], context: _LoadContext) -> Path:
    if context.automatic and server_id not in context.explicit_files:
        private_path = PRIVATE_ROOT / "mcps" / f"{server_id}.json"
        return private_path if private_path.is_file() else HUB_ROOT / "config" / "mcps" / f"{server_id}.example.json"
    return _private_file(item.get("configFile"), f"server {server_id} configFile")


def _load_server(server_id: str, item: dict[str, Any], context: _LoadContext) -> ServerConfig | None:
    if not _server_enabled(server_id, item, context):
        if server_id in context.detected:
            context.unavailable[server_id] = "disabled"
        return None
    local = _read_json(_server_file(server_id, item, context), f"server {server_id} config")
    if SECRET_PLACEHOLDER in json.dumps(local):
        if context.automatic:
            context.unavailable[server_id] = "credentials required"
            return None
        raise ConfigError(f"server {server_id} has unfilled {SECRET_PLACEHOLDER} values in its configFile")
    if local.get("transport") != "stdio":
        raise ConfigError(f"server {server_id} uses an unsupported transport")
    command = _string(local.get("command"), f"server {server_id} command")
    args = _parse_args(local.get("args", []), server_id)
    cwd = _parse_cwd(local.get("cwd"), server_id)
    if not Path(command).is_absolute() and ("/" in command or "\\" in command):
        command = str((cwd / command).resolve())
    env = _parse_env(local.get("env", {}), server_id)
    command = _apply_hub_venv(server_id, command, cwd, env, context)
    return ServerConfig(
        server_id=server_id,
        command=command,
        args=args,
        cwd=cwd,
        env=env,
        inherit_env=_parse_inherit_env(local.get("inheritEnv", _DEFAULT_INHERIT_ENV), server_id),
        timeout_seconds=_parse_timeout(local.get("timeoutSeconds", 60), f"server {server_id} timeoutSeconds"),
        secret_values=tuple(_collect_sensitive_values(local)),
        prepare=_parse_prepare(item.get("prepare"), server_id),
    )


def _parse_args(value: Any, server_id: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(arg, str) for arg in value):
        raise ConfigError(f"server {server_id} args must be a list of strings")
    return tuple(value)


def _parse_cwd(value: Any, server_id: str) -> Path:
    cwd = (HUB_ROOT / Path(_string(value, f"server {server_id} cwd")).expanduser()).resolve()
    if not cwd.is_dir():
        raise ConfigError(f"server {server_id} cwd does not exist")
    return cwd


def _parse_env(value: Any, server_id: str) -> dict[str, str]:
    env = _mapping(value, f"server {server_id} env")
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in env.items()):
        raise ConfigError(f"server {server_id} env must contain string values")
    return dict(env)


def _parse_inherit_env(value: Any, server_id: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise ConfigError(f"server {server_id} inheritEnv must be a list of names")
    return tuple(value)


def _parse_timeout(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label} must be a positive number")
    try:
        seconds = float(value)
    except OverflowError as exc:
        raise ConfigError(f"{label} must be a positive finite number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ConfigError(f"{label} must be a positive finite number")
    return seconds


def _hub_python() -> str:
    return str(HUB_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


def _apply_hub_venv(server_id: str, command: str, cwd: Path, env: dict[str, str], context: _LoadContext) -> str:
    """Run Python children that share this repo's dependencies on the Hub's own venv."""
    if server_id == "mcp-jira-sync" and context.automatic and server_id not in context.explicit_files:
        return _hub_python()
    if server_id == "mcp-read-source":
        inherited_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(cwd / "src") + (os.pathsep + inherited_pythonpath if inherited_pythonpath else "")
        return _hub_python()
    return command


def _parse_prepare(value: Any, server_id: str) -> PrepareSpec | None:
    if value is None:
        return None
    label = f"server {server_id} prepare"
    item = _mapping(value, label)
    command = item.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part.strip() for part in command):
        raise ConfigError(f"{label} command must be a non-empty list of non-empty strings")
    return PrepareSpec(
        command=tuple(command),
        runtime=_parse_runtime(item.get("runtime"), label),
        timeout_seconds=_parse_timeout(item.get("timeoutSeconds", 900), f"{label} timeoutSeconds"),
    )


def _parse_runtime(value: Any, label: str) -> RuntimeRequirement | None:
    if value is None:
        return None
    item = _mapping(value, f"{label} runtime")
    name = _string(item.get("name"), f"{label} runtime name")
    min_version = item.get("minVersion")
    if not isinstance(min_version, str) or not _VERSION_RE.fullmatch(min_version.strip()):
        raise ConfigError(f"{label} runtime minVersion must look like X.Y or X.Y.Z")
    winget = item.get("winget")
    return RuntimeRequirement(
        name=name,
        min_version=tuple(int(part) for part in min_version.strip().split(".")),
        winget_id=None if winget is None else _string(winget, f"{label} runtime winget"),
    )


def _feature_enabled(feature: str, item: dict[str, Any], context: _LoadContext) -> bool:
    if not context.automatic or feature in context.explicit_features:
        return item.get("enabled") is True
    return any(tool.get("server") in context.detected for tool in item.get("tools", []) if isinstance(tool, dict))


def _load_route(feature: str, value: Any, exposed_names: set[str]) -> ToolRoute:
    tool = _mapping(value, f"feature {feature} tool")
    server_id = _string(tool.get("server"), f"feature {feature} server")
    tool_name = _string(tool.get("name"), f"feature {feature} tool name")
    exposed_name = str(tool.get("exposeAs") or f"{server_id}__{tool_name}")
    if exposed_name.startswith("hub__"):
        raise ConfigError("The hub__ prefix is reserved for the Hub")
    if not _ALIAS_RE.fullmatch(exposed_name):
        raise ConfigError(f"Invalid exposed tool name in feature {feature}")
    if exposed_name in exposed_names:
        raise ConfigError(f"Duplicate exposed tool name: {exposed_name}")
    cache_seconds = _parse_cache_seconds(tool.get("cacheSeconds", 0), f"feature {feature} tool {tool_name} cacheSeconds")
    return ToolRoute(feature, server_id, tool_name, exposed_name, cache_seconds)


def _parse_cache_seconds(value: Any, label: str) -> float:
    """Parse a result-cache TTL: a finite number of seconds, 0 meaning no caching."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label} must be a non-negative number")
    try:
        seconds = float(value)
    except OverflowError as exc:
        raise ConfigError(f"{label} must be a non-negative finite number") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ConfigError(f"{label} must be a non-negative finite number")
    return seconds


def _load_routes(raw_features: dict[str, Any], servers: dict[str, ServerConfig], context: _LoadContext) -> tuple[ToolRoute, ...]:
    routes: list[ToolRoute] = []
    exposed_names: set[str] = set()
    for feature, value in raw_features.items():
        item = _mapping(value, f"feature {feature}")
        if not _feature_enabled(feature, item, context):
            continue
        tools = item.get("tools")
        if not isinstance(tools, list):
            raise ConfigError(f"feature {feature} tools must be a list")
        for tool in tools:
            route = _load_route(feature, tool, exposed_names)
            if route.server_id in context.unavailable and context.automatic:
                continue
            if route.server_id not in servers:
                raise ConfigError(f"feature {feature} refers to a disabled or unknown server")
            exposed_names.add(route.exposed_name)
            routes.append(route)
    return tuple(routes)
