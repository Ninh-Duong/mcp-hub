"""Client execution profiles and session-level mode/model validation."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .clients import normalize_client_name
from .config import HUB_ROOT, PRIVATE_ROOT, ConfigError, is_sensitive_key
from .jsonio import read_json
from .textnorm import fold

EXECUTION_MODES = ("multi_agent", "single_agent")
DEFAULT_PROFILES_PATH = HUB_ROOT / "config" / "client_profiles.example.json"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,64}$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TOP_KEYS = frozenset({"version", "default", "profiles"})
_PROFILE_KEYS = frozenset({
    "modes", "default_mode", "mechanism",
    "orchestrate_models", "default_orchestrate_model",
    "execute_models", "default_execute_model",
})


@dataclass(frozen=True)
class ClientProfile:
    """Execution capabilities and defaults for a connecting AI client."""

    key: str
    match: tuple[str, ...]
    modes: tuple[str, ...]
    default_mode: str
    mechanism: str
    orchestrate_models: tuple[str, ...]
    default_orchestrate_model: str
    execute_models: tuple[str, ...]
    default_execute_model: str
    source: str = "default"

    @property
    def has_choices(self) -> bool:
        """Whether the user can meaningfully pick among multiple modes or models."""
        return (
            len(self.modes) > 1
            or len(self.orchestrate_models) > 1
            or ("multi_agent" in self.modes and len(self.execute_models) > 1)
        )


@dataclass(frozen=True)
class SessionExecution:
    """Resolved execution mode, delegation mechanism, and models for a plan session."""

    client: str
    mode: str
    mechanism: str
    orchestrate_model: str
    execute_model: str


@dataclass(frozen=True)
class ClientProfileCatalog:
    """Loaded default profile and named client profiles."""

    default: ClientProfile
    profiles: dict[str, ClientProfile] = field(default_factory=dict)


def _read_profiles_file(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read {label}: {type(exc).__name__}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{label} must be a JSON object")
    unknown = sorted(set(raw) - _TOP_KEYS)
    if unknown:
        raise ConfigError(f"Unknown key in {label}: {unknown[0]}")
    if type(raw.get("version")) is not int or raw["version"] != 1:
        raise ConfigError(f"Unsupported {label} version")
    return raw


def _resolve_private_path(path: str | Path | None, private_root: str | Path) -> Path:
    root = Path(private_root).resolve()
    if path is None:
        return (root / "client_profiles.json").resolve()
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ConfigError("Client profiles config must be inside config/private/")
    return resolved


def _require_non_empty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string")
    return value.strip()


def _require_safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise ConfigError(f"{label} must be a valid identifier")
    return value


def _require_choice(value: Any, valid: tuple[str, ...], label: str) -> str:
    if not isinstance(value, str) or value not in valid:
        raise ConfigError(f"{label} must be one of: {', '.join(valid)}")
    return value


def _unique_items(value: Any, label: str, validator: Callable[[Any, str], str]) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{label} must be a non-empty list")
    items = tuple(validator(item, f"{label}[]") for item in value)
    if len(set(items)) != len(items):
        raise ConfigError(f"{label} must not contain duplicates")
    return items


def _parse_match(raw: dict[str, Any], key: str, *, is_default: bool, allow_omitted: bool) -> tuple[str, ...]:
    if is_default:
        return ()
    if "match" not in raw:
        if allow_omitted:
            return (key,)
        raise ConfigError(f"profiles.{key}.match must be a non-empty list")
    value = raw["match"]
    if not isinstance(value, list) or not value:
        raise ConfigError(f"profiles.{key}.match must be a non-empty list")
    return tuple(_require_non_empty_str(item, f"profiles.{key}.match[]") for item in value)


def _parse_profile(key: str, raw: Any, *, source: str, allow_omitted_match: bool = False) -> ClientProfile:
    if not isinstance(raw, dict):
        raise ConfigError(f"{key} profile must be an object")
    is_default = key == "default"
    if not is_default and (not _KEY_RE.fullmatch(key) or is_sensitive_key(key)):
        raise ConfigError(f"Invalid client profile key: {key!r}")
    label = "default" if is_default else f"profiles.{key}"
    allowed_keys = _PROFILE_KEYS if is_default else _PROFILE_KEYS | {"match"}
    unknown = sorted(set(raw) - allowed_keys)
    if unknown:
        raise ConfigError(f"Unknown field in {label}: {unknown[0]}")
    modes = _unique_items(raw.get("modes"), f"{label}.modes", lambda v, lbl: _require_choice(v, EXECUTION_MODES, lbl))
    default_mode = _require_choice(raw.get("default_mode"), modes, f"{label}.default_mode")
    mechanism = _require_safe_id(raw.get("mechanism"), f"{label}.mechanism")
    orch_models = _unique_items(raw.get("orchestrate_models"), f"{label}.orchestrate_models", _require_safe_id)
    default_orch = _require_choice(raw.get("default_orchestrate_model"), orch_models, f"{label}.default_orchestrate_model")
    exec_models = _unique_items(raw.get("execute_models"), f"{label}.execute_models", _require_safe_id)
    default_exec = _require_choice(raw.get("default_execute_model"), exec_models, f"{label}.default_execute_model")
    match = _parse_match(raw, key, is_default=is_default, allow_omitted=allow_omitted_match)
    return ClientProfile(
        key=key,
        match=match,
        modes=modes,
        default_mode=default_mode,
        mechanism=mechanism,
        orchestrate_models=orch_models,
        default_orchestrate_model=default_orch,
        execute_models=exec_models,
        default_execute_model=default_exec,
        source=source,
    )


def _parse_profiles_map(raw_profiles: Any, *, source: str, allow_omitted_match: bool) -> dict[str, ClientProfile]:
    if not isinstance(raw_profiles, dict):
        raise ConfigError("profiles must be an object")
    parsed: dict[str, ClientProfile] = {}
    for key, entry in raw_profiles.items():
        if not isinstance(key, str) or key == "default" or is_sensitive_key(key):
            raise ConfigError(f"Invalid client profile key: {key!r}")
        parsed[key] = _parse_profile(key, entry, source=source, allow_omitted_match=allow_omitted_match)
    return parsed


def load_client_profiles(
    *,
    path: str | Path | None = None,
    private_root: str | Path | None = PRIVATE_ROOT,
    default_path: Path = DEFAULT_PROFILES_PATH,
) -> ClientProfileCatalog:
    """Load the default client profiles catalog and apply optional private overrides."""
    base_raw = _read_profiles_file(default_path, "default client profiles")
    default_profile = _parse_profile("default", base_raw.get("default"), source="default")
    profiles = _parse_profiles_map(base_raw.get("profiles"), source="default", allow_omitted_match=False)
    if private_root is not None:
        private_path = _resolve_private_path(path, private_root)
        if private_path.exists():
            priv_raw = _read_profiles_file(private_path, "private client profiles")
            if priv_raw.get("default") is not None:
                default_profile = _parse_profile("default", priv_raw["default"], source="private")
            if priv_raw.get("profiles") is not None:
                profiles.update(_parse_profiles_map(priv_raw["profiles"], source="private", allow_omitted_match=True))
    return ClientProfileCatalog(default=default_profile, profiles=profiles)


def _match_score(candidate: str, pattern: str) -> tuple[int, int]:
    token = fold(pattern).strip()
    if not token:
        return (0, 0)
    if candidate == token:
        return (2, len(token))
    if candidate.startswith(token) and len(candidate) > len(token) and candidate[len(token)] in (" ", "-", "/"):
        return (1, len(token))
    return (0, 0)


def _match_profile(catalog: ClientProfileCatalog, raw_name: str) -> ClientProfile | None:
    candidate = fold(normalize_client_name(raw_name))
    if candidate == "unknown":
        return None
    best: tuple[tuple[int, int], ClientProfile] | None = None
    for profile in catalog.profiles.values():
        for pattern in (profile.key, *profile.match):
            score = _match_score(candidate, pattern)
            if score[0] and (best is None or score > best[0]):
                best = (score, profile)
    return best[1] if best is not None else None


def resolve_client_profile(
    catalog: ClientProfileCatalog,
    client_name: str = "unknown",
    explicit_client: str | None = None,
    *,
    shared_service: bool = False,
) -> ClientProfile:
    """Resolve the active client profile from MCP client name or an explicit override."""
    profile = _match_profile(catalog, client_name)
    if profile is None and isinstance(explicit_client, str) and explicit_client.strip():
        profile = _match_profile(catalog, explicit_client)
        if profile is None and fold(explicit_client.strip()) != "default":
            valid = ", ".join(sorted(catalog.profiles))
            raise ValueError(f"Unknown client profile '{explicit_client}'. Valid clients: {valid}.")
    if profile is None:
        profile = catalog.default
    if not shared_service and "multi_agent" in profile.modes:
        return replace(profile, modes=("single_agent",), default_mode="single_agent", mechanism="inline")
    return profile


def default_session_execution(profile: ClientProfile) -> SessionExecution:
    """Return the default execution settings for a resolved client profile."""
    return SessionExecution(
        client=profile.key,
        mode=profile.default_mode,
        mechanism=profile.mechanism if profile.default_mode == "multi_agent" else "inline",
        orchestrate_model=profile.default_orchestrate_model,
        execute_model=(
            profile.default_execute_model
            if profile.default_mode == "multi_agent"
            else profile.default_orchestrate_model
        ),
    )


def validate_session_execution(
    profile: ClientProfile,
    mode: Any,
    orchestrate_model: Any,
    execute_model: Any,
) -> SessionExecution:
    """Validate requested mode and models against the resolved client profile."""
    if not isinstance(mode, str) or mode not in profile.modes:
        raise ValueError(f"Invalid mode {mode!r} for client '{profile.key}'. Valid modes: {', '.join(profile.modes)}.")
    if not isinstance(orchestrate_model, str) or orchestrate_model not in profile.orchestrate_models:
        valid = ", ".join(profile.orchestrate_models)
        raise ValueError(f"Invalid orchestrate_model {orchestrate_model!r} for client '{profile.key}'. Valid models: {valid}.")
    if mode == "multi_agent":
        if not isinstance(execute_model, str) or execute_model not in profile.execute_models:
            valid = ", ".join(profile.execute_models)
            raise ValueError(f"Invalid execute_model {execute_model!r} for client '{profile.key}'. Valid models: {valid}.")
        return SessionExecution(profile.key, mode, profile.mechanism, orchestrate_model, execute_model)
    if execute_model is None or execute_model == "":
        resolved_execute = orchestrate_model
    elif isinstance(execute_model, str) and (execute_model in profile.execute_models or execute_model == orchestrate_model):
        resolved_execute = execute_model
    else:
        valid = ", ".join(profile.execute_models)
        raise ValueError(f"Invalid execute_model {execute_model!r} for client '{profile.key}'. Valid models: {valid}.")
    return SessionExecution(profile.key, mode, "inline", orchestrate_model, resolved_execute)
