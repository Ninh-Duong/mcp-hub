from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import PRIVATE_ROOT, SECRET_PLACEHOLDER


AGENT_ROLES = ("coordinator", "logs", "source", "jira", "pr")
PROVIDERS = frozenset({"openai", "gemini", "anthropic"})


class AgentConfigError(ValueError):
    """Raised for an invalid private agent configuration."""


@dataclass(frozen=True)
class AgentConfig:
    name: str
    provider: str | None
    model: str | None
    api_key: str | None = field(default=None, repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.model and self.api_key)


def load_agent_configs(
    *,
    path: str | Path | None = None,
    private_root: str | Path = PRIVATE_ROOT,
) -> dict[str, AgentConfig]:
    root = Path(private_root).resolve()
    config_path = Path(path) if path is not None else root / "agents.json"
    if not config_path.is_absolute():
        config_path = root / config_path
    config_path = config_path.resolve()
    if not config_path.is_relative_to(root):
        raise AgentConfigError("Agent config must be inside config/private/")
    if not config_path.exists():
        return {role: AgentConfig(role, None, None) for role in AGENT_ROLES}
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentConfigError(f"Could not read agent config ({type(exc).__name__})") from exc
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        raise AgentConfigError("Unsupported agent config version")
    agents = raw.get("agents")
    if not isinstance(agents, dict):
        raise AgentConfigError("agents must be an object")
    unknown = set(agents) - set(AGENT_ROLES)
    if unknown:
        raise AgentConfigError("Agent config contains an unsupported role")

    result: dict[str, AgentConfig] = {}
    for role in AGENT_ROLES:
        item = agents.get(role, {})
        if not isinstance(item, dict):
            raise AgentConfigError(f"Configuration for agent '{role}' must be an object")
        provider = _optional_string(item.get("provider"), "provider")
        if provider is not None and provider not in PROVIDERS:
            raise AgentConfigError(f"Agent '{role}' has an unsupported provider")
        model = _optional_string(item.get("model"), "model")
        api_key = _optional_string(item.get("api_key"), "api_key")
        result[role] = AgentConfig(role, provider, model, api_key)
    return result


def _optional_string(value: object, label: str) -> str | None:
    if value is None or value == "" or value == SECRET_PLACEHOLDER:
        return None
    if not isinstance(value, str):
        raise AgentConfigError(f"Agent {label} must be a string")
    return value.strip() or None
