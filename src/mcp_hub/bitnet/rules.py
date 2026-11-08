from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from ..extensions import PlanContext, PlanHints


@dataclass(frozen=True)
class RuleResult:
    hints: PlanHints
    unresolved: frozenset[str]


def fold(text: str) -> str:
    """Casefold and strip diacritics; keeps offsets irrelevant (match-only)."""
    text = text.replace("\u0111", "d").replace("\u0110", "d")
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).casefold()


_ENV_PATTERNS = (
    ("prod", re.compile(r"\b(production|prod|live|moi truong that)\b")),
    ("stg", re.compile(r"\b(staging|stage|stg|uat)\b")),
    ("dev", re.compile(r"\b(development|dev)\b")),
)

_WINDOW_TODAY_RE = re.compile(r"\b(today|hom nay|24h|24 gio|last 24 hours|trong ngay)\b")
_WINDOW_YESTERDAY_RE = re.compile(r"\b(yesterday|hom qua)\b")
_WINDOW_HOURS_RE = re.compile(r"\b(\d+)\s*(?:h|gio|hours?)\b")
_WINDOW_DAYS_RE = re.compile(r"\b(\d+)\s*(?:ngay|days?)\b")
_WINDOW_WEEK_RE = re.compile(r"\b(this week|tuan nay)\b")

_RCID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|\b(?:rcid|request\s*id|correlation\s*id)\s*[:=]?\s*\S+"
)
_JIRA_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_PR_RE = re.compile(r"/(?:pull-requests|pull)/\d+")

_SERVER_KEYWORDS = (
    ("logs", re.compile(r"\b(log|logs|loi|errors?|exceptions?|500)\b")),
    ("sql", re.compile(r"\b(sql|database|db|table|bang|du lieu|query)\b")),
    ("jira", re.compile(r"\b(jira|ticket)\b")),
    ("pr", re.compile(r"\b(pr|pull\s*request)\b")),
    ("source", re.compile(r"\b(source|code|ma\s*nguon)\b")),
)


def _parse_env(folded: str, allowed_envs: tuple[str, ...]) -> str | None:
    valid_set = set(allowed_envs) if allowed_envs else {"dev", "stg", "prod"}
    matched = [env for env, pat in _ENV_PATTERNS if pat.search(folded)]
    if len(matched) == 1 and matched[0] in valid_set:
        return matched[0]
    return None


def _parse_window(folded: str, now: datetime) -> tuple[datetime, datetime] | None:
    now_utc = now.astimezone(timezone.utc)
    if _WINDOW_TODAY_RE.search(folded):
        return now_utc - timedelta(hours=24), now_utc
    if _WINDOW_YESTERDAY_RE.search(folded):
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        return today_start - timedelta(days=1), today_start
    m_h = _WINDOW_HOURS_RE.search(folded)
    if m_h:
        hours = int(m_h.group(1))
        if 1 <= hours <= 168:
            return now_utc - timedelta(hours=hours), now_utc
        return None
    m_d = _WINDOW_DAYS_RE.search(folded)
    if m_d:
        days = int(m_d.group(1))
        hours = days * 24
        if 1 <= hours <= 168:
            return now_utc - timedelta(hours=hours), now_utc
        return None
    if _WINDOW_WEEK_RE.search(folded):
        return now_utc - timedelta(days=7), now_utc
    return None


def _parse_playbook(original: str, folded: str, playbooks: Mapping[str, str]) -> str | None:
    if "rcid-bug" in playbooks and _RCID_RE.search(folded):
        return "rcid-bug"
    if "jira-ticket-bug" in playbooks and _JIRA_RE.search(original):
        return "jira-ticket-bug"
    if "pr-review" in playbooks and _PR_RE.search(original):
        return "pr-review"
    return None


def _parse_servers(folded: str, connected_servers: tuple[str, ...]) -> tuple[str, ...]:
    servers: list[str] = []
    for fragment, pat in _SERVER_KEYWORDS:
        if pat.search(folded):
            srv = next((s for s in connected_servers if fragment in s), None)
            if srv and srv not in servers:
                servers.append(srv)
    return tuple(servers)


def parse_rules(context: PlanContext) -> RuleResult:
    folded = fold(context.task)
    env = _parse_env(folded, context.envs)
    window = _parse_window(folded, context.now)
    playbook = _parse_playbook(context.task, folded, context.playbooks)
    servers = _parse_servers(folded, context.servers)

    unresolved: set[str] = set()
    if playbook is None:
        unresolved.add("playbook")
    if not servers:
        unresolved.add("servers")
    if env is None:
        unresolved.add("env")
    if window is None:
        unresolved.add("window")

    since = window[0] if window else None
    until = window[1] if window else None
    hints = PlanHints(
        playbook=playbook,
        servers=servers,
        env=env,
        since=since,
        until=until,
        source="rules",
    )
    return RuleResult(hints=hints, unresolved=frozenset(unresolved))
