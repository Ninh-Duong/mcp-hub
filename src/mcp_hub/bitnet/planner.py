from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..extensions import PlanAssistant, PlanContext, PlanHints
from .client import BitnetClient
from .rules import parse_rules


_KEY_SPECS = {
    "playbook": "playbook (one of {ids} or null)",
    "servers": "servers (array, subset of {servers})",
    "env": "env (one of {envs} or null)",
    "window": "window_hours (integer 1-168 or null)",
}


def _build_system_prompt(context: PlanContext, unresolved: frozenset[str]) -> str:
    parts: list[str] = []
    if "playbook" in unresolved:
        ids_str = ", ".join(sorted(context.playbooks)) if context.playbooks else "none"
        parts.append(_KEY_SPECS["playbook"].format(ids=ids_str))
    if "servers" in unresolved:
        srv_str = ", ".join(sorted(context.servers)) if context.servers else "none"
        parts.append(_KEY_SPECS["servers"].format(servers=srv_str))
    if "env" in unresolved:
        envs_str = ", ".join(sorted(context.envs if context.envs else ("dev", "stg", "prod")))
        parts.append(_KEY_SPECS["env"].format(envs=envs_str))
    if "window" in unresolved:
        parts.append(_KEY_SPECS["window"])

    keys_spec = ", ".join(parts)
    return (
        "You map an operator request to a JSON object. "
        "Reply with one JSON object only, no prose. "
        f"Keys: {keys_spec}. Use null when unsure."
    )


def _validate_model_reply(
    reply: dict[str, Any] | None, context: PlanContext, unresolved: frozenset[str],
) -> tuple[str | None, tuple[str, ...], str | None, datetime | None, datetime | None]:
    if not reply or not isinstance(reply, dict):
        return None, (), None, None, None

    playbook = None
    if "playbook" in unresolved:
        val = reply.get("playbook")
        if isinstance(val, str) and val in context.playbooks:
            playbook = val

    servers: list[str] = []
    if "servers" in unresolved:
        val = reply.get("servers")
        if isinstance(val, list):
            servers = [s for s in val if isinstance(s, str) and s in context.servers]

    env = None
    if "env" in unresolved:
        val = reply.get("env")
        valid_envs = set(context.envs if context.envs else ("dev", "stg", "prod"))
        if isinstance(val, str) and val in valid_envs:
            env = val

    since = None
    until = None
    if "window" in unresolved:
        val = reply.get("window_hours")
        if isinstance(val, int) and 1 <= val <= 168:
            now_utc = context.now.astimezone(timezone.utc)
            since = now_utc - timedelta(hours=val)
            until = now_utc

    return playbook, tuple(servers), env, since, until


class BitnetPlanAssistant(PlanAssistant):
    def __init__(self, client: BitnetClient, *, timeout: float = 3.0) -> None:
        self.client = client
        self.timeout = timeout

    async def hints(self, context: PlanContext) -> PlanHints | None:
        try:
            return await self._get_hints(context)
        except Exception:
            try:
                rule_res = parse_rules(context)
                return rule_res.hints if _has_hints(rule_res.hints) else None
            except Exception:
                return None

    assist_plan = hints

    async def _get_hints(self, context: PlanContext) -> PlanHints | None:
        rule_res = parse_rules(context)
        if not rule_res.unresolved:
            return rule_res.hints

        system = _build_system_prompt(context, rule_res.unresolved)
        model_reply = await self.client.complete_json(system, context.task, max_tokens=128, timeout=self.timeout)
        m_pb, m_srv, m_env, m_since, m_until = _validate_model_reply(model_reply, context, rule_res.unresolved)

        model_filled = bool(m_pb or m_srv or m_env or (m_since and m_until))
        final_pb = rule_res.hints.playbook or m_pb
        final_srv = rule_res.hints.servers or m_srv
        final_env = rule_res.hints.env or m_env
        final_since = rule_res.hints.since or m_since
        final_until = rule_res.hints.until or m_until

        if not any((final_pb, final_srv, final_env, final_since)):
            return None

        source = "bitnet" if model_filled else "rules"
        return PlanHints(
            playbook=final_pb,
            servers=final_srv,
            env=final_env,
            since=final_since,
            until=final_until,
            source=source,
        )


def _has_hints(hints: PlanHints) -> bool:
    return bool(hints.playbook or hints.servers or hints.env or hints.since)
