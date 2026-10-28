from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import PRIVATE_ROOT


ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ENVIRONMENTS = frozenset({"dev", "stg", "prod"})


class ProjectConfigError(ValueError):
    """Raised when a private project registration is invalid."""


@dataclass(frozen=True)
class ProjectConfig:
    alias: str
    allowed_envs: tuple[str, ...]
    default_env: str | None = None


def validate_project_env(project: ProjectConfig, env: str) -> str:
    if not isinstance(env, str) or env not in project.allowed_envs:
        raise ValueError(f"Environment must be one of: {', '.join(project.allowed_envs)}")
    return env


def load_projects(
    *,
    path: str | Path | None = None,
    private_root: str | Path = PRIVATE_ROOT,
) -> dict[str, ProjectConfig]:
    root = Path(private_root).resolve()
    config_path = Path(path) if path is not None else root / "projects.json"
    if not config_path.is_absolute():
        config_path = root / config_path
    config_path = config_path.resolve()
    if not config_path.is_relative_to(root):
        raise ProjectConfigError("Project config must be inside config/private/")
    if not config_path.exists():
        return {}
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectConfigError(f"Could not read project config ({type(exc).__name__})") from None
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        raise ProjectConfigError("Unsupported project config version")
    entries = raw.get("projects")
    if not isinstance(entries, dict):
        raise ProjectConfigError("projects must be an object")

    result: dict[str, ProjectConfig] = {}
    for alias, value in entries.items():
        if not isinstance(alias, str) or not ALIAS_RE.fullmatch(alias):
            raise ProjectConfigError("Project aliases may contain only letters, digits, '_' and '-'")
        if not isinstance(value, dict):
            raise ProjectConfigError(f"Project '{alias}' must be an object")
        envs = value.get("allowed_envs", ["dev", "stg", "prod"])
        if not isinstance(envs, list) or not envs or any(not isinstance(env, str) or env not in ENVIRONMENTS for env in envs):
            raise ProjectConfigError(f"Project '{alias}' has invalid allowed_envs")
        if len(set(envs)) != len(envs):
            raise ProjectConfigError(f"Project '{alias}' has duplicate allowed_envs")
        default_env = value.get("default_env")
        if default_env == "":
            default_env = None
        if default_env is not None and (not isinstance(default_env, str) or default_env not in envs):
            raise ProjectConfigError(f"Project '{alias}' default_env must be one of its allowed_envs")
        result[alias] = ProjectConfig(alias, tuple(envs), default_env)
    return result
