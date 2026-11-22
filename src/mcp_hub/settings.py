"""Operator tunables: one optional `settings` object in the Hub config, with every default defined here.

    "settings": {"http": {"port": 8765}, "jobs": {"maxActive": 3}, "logs": {"maxBytes": 10485760}}

A missing group or key keeps its default. An unknown group or key, or a value of the wrong type or out of
range, stops the Hub with a ConfigError that names it, so a typo never silently keeps a default. Keys are
camelCase, like the rest of the Hub config. The values are passed to the objects that use them when the
service (or the stdio Hub) starts; modules never read this file themselves.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .config import ConfigError, settings_block


@dataclass(frozen=True)
class HttpSettings:
    # The Hub always binds to 127.0.0.1; only the port can move. HTTP clients' URLs must follow it.
    port: int = 8765
    shutdown_drain_seconds: float = 20.0


@dataclass(frozen=True)
class BridgeSettings:
    # The stdio bridge (mcp-hub-bridge) to the shared service: presence retry and the per-request timeout.
    presence_retry_seconds: float = 5.0
    connect_timeout_seconds: float = 60.0


@dataclass(frozen=True)
class ProtocolSettings:
    # Argument checks against a child's input schema: off, or warn (audit only; the call still runs).
    validate_arguments: str = "warn"
    # Quiet time before one notifications/tools/list_changed is sent for a burst of tool changes.
    list_changed_debounce_seconds: float = 1.0
    # ttlMs of tools/list results; 0 tells clients not to cache them.
    tools_list_ttl_ms: int = 0


@dataclass(frozen=True)
class SecuritySettings:
    # Tool definition pins (config/private/tool_pins.json): off, or warn (audit when a child's definition changes).
    tool_pin_mode: str = "warn"
    # Longest tool description accepted without a finding.
    max_description_chars: int = 4096


@dataclass(frozen=True)
class ChildrenSettings:
    # A dropped child is reconnected after `reconnect_initial_seconds`, doubling up to `reconnect_max_seconds`.
    reconnect_initial_seconds: float = 1.0
    reconnect_max_seconds: float = 60.0
    # Longest the Hub waits for children at start; a child that has not answered by then joins later.
    startup_wait_seconds: float = 10.0
    # Longest the Hub waits for a child's `config_status` answer after it connects.
    config_status_timeout_seconds: float = 5.0


@dataclass(frozen=True)
class LogSettings:
    # logs/mcp-hub.jsonl and each child's stderr log rotate at this size, keeping `backups` older audit files.
    max_bytes: int = 5 * 1024 * 1024
    backups: int = 3


@dataclass(frozen=True)
class CacheSettings:
    max_entries: int = 256
    max_bytes: int = 4 * 1024 * 1024


@dataclass(frozen=True)
class JobSettings:
    max_active: int = 2
    max_queued: int = 20
    max_job_seconds: float = 15 * 60
    max_child_calls: int = 30
    retention_days: int = 30
    approval_code_minutes: float = 10.0
    max_window_hours: int = 168
    # The coordinator's time after the window ends, and the specialists' round and context budgets.
    coordinator_seconds: float = 90.0
    specialist_max_rounds: int = 8
    tool_result_chars: int = 6000
    context_chars: int = 48000
    # Ask the model provider to cache the stable part of each prompt (tool list first).
    prompt_cache: bool = True
    # The model provider: the request timeout and the output budget of each call.
    provider_timeout_seconds: float = 60.0
    provider_output_tokens: int = 2048
    # The coordinator's summary: input budget, output budget, and the length of the summary it returns.
    coordinator_input_chars: int = 12000
    coordinator_output_tokens: int = 1200
    coordinator_summary_chars: int = 4000
    # The default page size of hub__get_job_detail and the longest question hub__submit_issue accepts.
    detail_page_limit: int = 2000
    question_max_chars: int = 5000


@dataclass(frozen=True)
class ActivitySettings:
    # The activity UI's recorder: events written per batch, the longest wait for a batch, and the queue bound.
    batch_size: int = 50
    batch_seconds: float = 1.0
    queue_limit: int = 10000
    # The activity stream sends a keep-alive comment after this many seconds without an event.
    stream_keepalive_seconds: float = 15.0


@dataclass(frozen=True)
class BatchSettings:
    max_calls: int = 8
    max_per_child: int = 8
    timeout_seconds: float = 120.0
    pack_chars: int = 9500


@dataclass(frozen=True)
class PolicySettings:
    workflow_max_receipts: int = 128
    workflow_initial_window_seconds: float = 3600.0
    workflow_max_limit: int = 50
    # enforce: refuse calls that break the client's hub__plan; warn: run them with a warning; off: no plan needed.
    mode: str = "enforce"
    # Plans kept in memory; the least recently used is dropped, and a call naming it must plan again.
    max_plans: int = 256
    # Playbook confirmation (docs/usage-policy.md): always asks before every plan; ambiguous asks unless exactly one
    # playbook matches the task; off never asks. A confirmation token lives confirm_token_seconds and is used once.
    confirm_playbook: str = "always"
    confirm_token_seconds: float = 600.0
    # An identical call that already succeeded is refused, unless this many seconds have passed (0: always refused).
    repeat_after_seconds: float = 0.0
    # Connection checks: allowed only after a failed call, or when the plan says the user asked for checks.
    # Listing OpenObserve organizations is discovery, not a check; playbooks pass the project's targets instead.
    health_check_tools: tuple[str, ...] = (
        "jira__check_connection",
        "pr__check_bitbucket_connection",
        "source__check_connection",
        "sql__check_connection",
    )


@dataclass(frozen=True)
class BitnetSettings:
    # Local BitNet extension (docs/bitnet.md). Used only when the `bitnet` extension is on.
    port: int = 8766
    threads: int = 0
    model: str = "BitNet-b1.58-2B-4T"
    max_restarts: int = 3
    plan_timeout_seconds: float = 3.0
    condense_timeout_seconds: float = 8.0
    condense_min_chars: int = 9000
    full_result_ttl_seconds: float = 1800.0
    full_result_max_entries: int = 50
    # Total bytes of the original results kept in memory; the oldest are dropped first.
    full_result_max_bytes: int = 32 * 1024 * 1024
    # The sidecar: the context window in tokens, the health check timeout, and the recheck timeout while it runs.
    sidecar_context_tokens: int = 4096
    sidecar_health_seconds: float = 0.5
    sidecar_recheck_seconds: float = 1.0
    # The draft summary: input budget, output budget, and the time it may take.
    summary_input_chars: int = 10000
    summary_output_tokens: int = 400
    summary_timeout_seconds: float = 30.0
    # How long a stop waits for an install to end at its next chunk, and the size of the chunks the condenser reads.
    setup_wait_seconds: float = 5.0
    condense_chunk_chars: int = 10000
    # Install from the desktop app: the GitHub repository whose Release carries the prebuilt runtime and the
    # model manifest, the time without data before a download fails, and the free space needed before it starts.
    release_repository: str = "Ninh-Duong/mcp-hub"
    download_timeout_seconds: float = 60.0
    min_free_bytes: int = 3 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    http: HttpSettings = field(default_factory=HttpSettings)
    bridge: BridgeSettings = field(default_factory=BridgeSettings)
    protocol: ProtocolSettings = field(default_factory=ProtocolSettings)
    security: SecuritySettings = field(default_factory=SecuritySettings)
    children: ChildrenSettings = field(default_factory=ChildrenSettings)
    logs: LogSettings = field(default_factory=LogSettings)
    cache: CacheSettings = field(default_factory=CacheSettings)
    jobs: JobSettings = field(default_factory=JobSettings)
    batch: BatchSettings = field(default_factory=BatchSettings)
    policy: PolicySettings = field(default_factory=PolicySettings)
    bitnet: BitnetSettings = field(default_factory=BitnetSettings)
    activity: ActivitySettings = field(default_factory=ActivitySettings)


# Lowest accepted value per key; everything else must only be positive. The port stays out of the
# privileged range, and a batch needs at least two calls to be one.
_MINIMUMS = {
    ("http", "port"): 1024,
    ("logs", "backups"): 0,
    ("batch", "max_calls"): 2,
    ("bitnet", "port"): 1024,
    ("bitnet", "threads"): 0,
    ("protocol", "list_changed_debounce_seconds"): 0,
    ("protocol", "tools_list_ttl_ms"): 0,
    ("policy", "repeat_after_seconds"): 0,
}
_MAXIMUMS = {("http", "port"): 65535, ("bitnet", "port"): 65535, ("children", "config_status_timeout_seconds"): 60}
# Text settings and the values each accepts.
_CHOICES = {
    ("policy", "mode"): ("enforce", "warn", "off"),
    ("policy", "confirm_playbook"): ("always", "ambiguous", "off"),
    ("protocol", "validate_arguments"): ("off", "warn"),
    ("security", "tool_pin_mode"): ("off", "warn"),
}


def load_settings(config_path: str | Path | None = None) -> Settings:
    return parse_settings(settings_block(config_path))


def parse_settings(raw: dict[str, Any]) -> Settings:
    groups = {_camel(item.name): item for item in fields(Settings)}
    if "dashboard" in raw and not isinstance(raw["dashboard"], dict):
        raise ConfigError("settings.dashboard must be an object")
    unknown = sorted(set(raw) - set(groups) - {"dashboard"})
    if unknown:
        raise ConfigError(f"Unknown settings group: {unknown[0]}")
    values = {}
    for key, item in groups.items():
        value = raw.get(key, {})
        if not isinstance(value, dict):
            raise ConfigError(f"settings.{key} must be an object")
        values[item.name] = _parse_group(item.name, item.default_factory, value)
    return Settings(**values)


def _parse_group(group: str, cls: type, raw: dict[str, Any]) -> Any:
    keys = {_camel(item.name): item for item in fields(cls)}
    unknown = sorted(set(raw) - set(keys))
    if unknown:
        raise ConfigError(f"Unknown setting: settings.{_camel(group)}.{unknown[0]}")
    defaults = cls()
    values = {}
    for key, item in keys.items():
        value = raw.get(key, getattr(defaults, item.name))
        values[item.name] = _checked(f"settings.{_camel(group)}.{key}", (group, item.name), value,
                                     type(getattr(defaults, item.name)))
    return cls(**values)


def _checked(label: str, key: tuple[str, str], value: Any, kind: type) -> Any:
    if kind is str:
        if key in _CHOICES:
            return _checked_choice(label, key, value)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{label} must be a non-empty string")
        return value.strip()
    if kind is bool:
        return _checked_flag(label, value)
    if kind is tuple:
        return _checked_names(label, value)
    return _checked_number(label, key, value, kind)


def _checked_flag(label: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{label} must be true or false")
    return value


def _checked_choice(label: str, key: tuple[str, str], value: Any) -> str:
    choices = _CHOICES[key]
    if value not in choices:
        raise ConfigError(f"{label} must be one of: {', '.join(choices)}")
    return value


def _checked_names(label: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) and item for item in value):
        raise ConfigError(f"{label} must be a list of tool names")
    return tuple(value)


def _checked_number(label: str, key: tuple[str, str], value: Any, kind: type) -> int | float:
    wrong_kind = kind is int and not isinstance(value, int)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or wrong_kind or not math.isfinite(value):
        raise ConfigError(f"{label} must be {'a whole number' if kind is int else 'a number'}")
    low, high = _MINIMUMS.get(key), _MAXIMUMS.get(key)
    if (value <= 0 if low is None else value < low) or (high is not None and value > high):
        bounds = f"between {low} and {high}" if high is not None else (f"at least {low}" if low is not None else "positive")
        raise ConfigError(f"{label} must be {bounds}")
    return kind(value)


def _camel(name: str) -> str:
    return re.sub(r"_([a-z])", lambda match: match.group(1).upper(), name)
