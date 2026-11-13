"""Playbooks: task recipes in `docs/playbooks/<id>.md` that `hub__plan` hands to an agent and enforces.

A playbook says, for one kind of input (an rcid, a Jira key, a service, a PR), which tools to call in which
order, how narrow each call must be, and when to stop. The same text serves every client and the job
specialists. Its frontmatter is what the Hub enforces:

    ---
    id: rcid-bug
    when: The user gives an rcid and wants the cause of a failure.
    servers: [mcp-server-logs, mcp-read-source]
    tools: [logs__find_openobserve_logs_by_rcid, source__search]
    max_limit: 100
    needs: [logs]
    includes: [other-playbook]
    ---

`needs: [logs]` means the plan must name a project and an environment with log targets in
`config/private/projects.json`. `includes` adds other playbooks' servers, tools, and steps. Pure parsing:
the files are read once at start, never on the tool-call path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import HUB_ROOT, ConfigError

PLAYBOOK_DIR = HUB_ROOT / "docs" / "playbooks"
NEEDS = frozenset({"logs", "source"})
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_LIST_KEYS = ("servers", "tools", "needs", "includes")


@dataclass(frozen=True)
class Playbook:
    id: str
    when: str
    servers: tuple[str, ...]
    tools: tuple[str, ...]
    max_limit: int | None
    needs: tuple[str, ...]
    includes: tuple[str, ...]
    body: str


def load_playbooks(directory: Path = PLAYBOOK_DIR) -> dict[str, Playbook]:
    """Every `<id>.md` in `directory` except README.md, with `includes` merged; ConfigError names a bad file."""
    if not directory.is_dir():
        return {}
    raw = {}
    for path in sorted(directory.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        playbook = parse_playbook(path.read_text(encoding="utf-8"), path.stem)
        raw[playbook.id] = playbook
    return {playbook_id: _merged(raw[playbook_id], raw) for playbook_id in raw}


def parse_playbook(text: str, file_id: str) -> Playbook:
    fields, body = _split_frontmatter(text, file_id)
    playbook_id = fields.get("id", "")
    if playbook_id != file_id or not _ID_RE.match(playbook_id):
        raise ConfigError(f"Playbook {file_id}: 'id' must match the file name")
    if not fields.get("when"):
        raise ConfigError(f"Playbook {file_id}: 'when' is required")
    lists = {key: _parse_list(fields.get(key, "[]"), file_id, key) for key in _LIST_KEYS}
    if not lists["servers"] or not lists["tools"]:
        raise ConfigError(f"Playbook {file_id}: 'servers' and 'tools' must list at least one item")
    if set(lists["needs"]) - NEEDS:
        raise ConfigError(f"Playbook {file_id}: 'needs' may contain only: {', '.join(sorted(NEEDS))}")
    return Playbook(
        playbook_id, fields["when"], lists["servers"], lists["tools"], _parse_limit(fields.get("max_limit"), file_id),
        lists["needs"], lists["includes"], body.strip(),
    )


def _split_frontmatter(text: str, file_id: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---" or "---" not in (line.strip() for line in lines[1:]):
        raise ConfigError(f"Playbook {file_id}: missing '---' frontmatter")
    end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    fields: dict[str, str] = {}
    key = None
    for line in lines[1:end]:
        if line[:1].isspace() and key is not None:
            fields[key] += " " + line.strip()  # a continuation of the previous value
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ConfigError(f"Playbook {file_id}: frontmatter line without ':'")
        key = key.strip()
        fields[key] = value.strip()
    return fields, "\n".join(lines[end + 1:])


def _parse_list(value: str, file_id: str, key: str) -> tuple[str, ...]:
    if not (value.startswith("[") and value.endswith("]")):
        raise ConfigError(f"Playbook {file_id}: '{key}' must be a [comma, separated] list")
    return tuple(item.strip() for item in value[1:-1].split(",") if item.strip())


def _parse_limit(value: str | None, file_id: str) -> int | None:
    if value is None:
        return None
    if not value.isdigit() or int(value) < 1:
        raise ConfigError(f"Playbook {file_id}: 'max_limit' must be a positive whole number")
    return int(value)


def _merged(playbook: Playbook, all_playbooks: dict[str, Playbook]) -> Playbook:
    """Add the servers, tools, needs, and steps of the playbooks it includes (one level)."""
    included = []
    for name in playbook.includes:
        other = all_playbooks.get(name)
        if other is None or other.includes:
            raise ConfigError(f"Playbook {playbook.id}: includes unknown or nested playbook '{name}'")
        included.append(other)
    if not included:
        return playbook
    group = (playbook, *included)
    limits = [p.max_limit for p in group if p.max_limit is not None]
    body = "\n\n".join([playbook.body, *(f"# Included: {p.id}\n\n{p.body}" for p in included)])
    return Playbook(playbook.id, playbook.when, _union(group, "servers"), _union(group, "tools"),
                    min(limits) if limits else None, _union(group, "needs"), playbook.includes, body)


def _union(group: tuple[Playbook, ...], attr: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for playbook in group for item in getattr(playbook, attr)))
