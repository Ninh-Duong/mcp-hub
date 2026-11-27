from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from ..jsonio import read_json
from ..config import PRIVATE_ROOT, SECRET_PLACEHOLDER


AGENT_ROLES = ("coordinator", "logs", "source", "jira", "pr", "sql")
PROVIDERS = frozenset({"openai", "gemini", "anthropic", "opencode"})
DEFAULT_OPENCODE_BASE_URL = "https://opencode.ai/zen/v1"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class AgentConfigError(ValueError):
    """Raised for an invalid private agent configuration."""


@dataclass(frozen=True)
class AgentConfig:
    name: str
    provider: str | None
    model: str | None
    api_key: str | None = field(default=None, repr=False)
    base_url: str | None = None

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
        raw = read_json(config_path)
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
        base_url = _validated_base_url(item.get("base_url"), provider)
        result[role] = AgentConfig(role, provider, model, api_key, base_url)
    return result


def _validated_base_url(value: object, provider: str | None) -> str | None:
    raw = _optional_string(value, "base_url")
    if raw is None:
        return DEFAULT_OPENCODE_BASE_URL if provider == "opencode" else None
    if provider != "opencode":
        raise AgentConfigError("Agent base_url is only supported for the 'opencode' provider")
    parsed = urlparse(raw)
    if parsed.username or parsed.password:
        raise AgentConfigError("Agent base_url must not contain credentials")
    if parsed.scheme == "https" and parsed.netloc:
        return raw.rstrip("/")
    if parsed.scheme == "http" and (parsed.hostname or "").lower() in _LOOPBACK_HOSTS:
        return raw.rstrip("/")
    raise AgentConfigError("Agent base_url must be an https URL or loopback http URL")


def _optional_string(value: object, label: str) -> str | None:
    if value is None or value == "" or value == SECRET_PLACEHOLDER:
        return None
    if not isinstance(value, str):
        raise AgentConfigError(f"Agent {label} must be a string")
    return value.strip() or None
