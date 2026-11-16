"""Required settings a child MCP declares in its own repository (`hub-requirements.json`).

The file names dotted key paths only, never values: `{"version": 1, "groups": [{"label": ..., "anyOf":
[["openobserve.dev.url", "openobserve.dev.token"], ["seq.server_url", "seq.api_key"]]}]}`. A group is
satisfied when one of its alternatives has a filled value for every key. A missing value is reported by
key name, so no secret ever leaves the Hub through this module.

A group may carry an `id`. A child that offers the `config_status` tool reports, per group id, where its
values come from (`env`, `hub`, `local`, `default`, or `unset`); a group the child reports as set counts as
satisfied even when the Hub file leaves it empty, because the child reads it from its own local files.

This module is a leaf: it imports nothing from the Hub, so `config` can use it.
"""
from __future__ import annotations
from .jsonio import read_json

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUIREMENTS_FILE = "hub-requirements.json"
REQUIREMENTS_VERSION = 1
# Same value as the placeholder `config` uses for "a user must fill this in"; defined once, here.
SECRET_PLACEHOLDER = "<SET_BY_USER>"

DECLARED = "declared"
NONE = "none"
INVALID = "invalid"

_KEY_PATH_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
_GROUP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# The sources a child's `config_status` may report for a group, and those that mean "the values are set".
SOURCES = frozenset({"env", "hub", "local", "default", "unset"})
SET_SOURCES = frozenset({"env", "hub", "local", "default"})


class RequirementsError(ValueError):
    """The requirements file exists but does not follow the format above."""


@dataclass(frozen=True)
class Group:
    label: str
    alternatives: tuple[tuple[str, ...], ...]
    group_id: str | None = None


@dataclass(frozen=True)
class MissingGroup:
    """One unsatisfied group: its id (if declared) and the empty key paths of its closest alternative."""

    group_id: str | None
    keys: tuple[str, ...]


@dataclass(frozen=True)
class Requirements:
    """`status` is `declared` (file read), `none` (no file), or `invalid` (file unreadable or malformed)."""

    status: str
    groups: tuple[Group, ...] = field(default=())


def read_requirements(repo_dir: Path) -> Requirements:
    path = repo_dir / REQUIREMENTS_FILE
    if not path.is_file():
        return Requirements(NONE)
    try:
        raw = read_json(path)
        return Requirements(DECLARED, parse_requirements(raw))
    except (OSError, ValueError, RequirementsError):
        return Requirements(INVALID)


def parse_requirements(raw: Any) -> tuple[Group, ...]:
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != REQUIREMENTS_VERSION:
        raise RequirementsError("unsupported requirements version")
    groups = raw.get("groups")
    if not isinstance(groups, list):
        raise RequirementsError("groups must be a list")
    return tuple(_parse_group(item) for item in groups)


def _parse_group(item: Any) -> Group:
    if not isinstance(item, dict) or not isinstance(item.get("label"), str) or not item["label"].strip():
        raise RequirementsError("each group needs a label")
    any_of = item.get("anyOf")
    if not isinstance(any_of, list) or not any_of:
        raise RequirementsError("anyOf must be a non-empty list")
    group_id = item.get("id")
    if group_id is not None and (not isinstance(group_id, str) or not _GROUP_ID_RE.fullmatch(group_id)):
        raise RequirementsError("a group id is a lowercase name such as log_account")
    return Group(item["label"].strip(), tuple(_parse_alternative(alternative) for alternative in any_of), group_id)


def _parse_alternative(alternative: Any) -> tuple[str, ...]:
    if not isinstance(alternative, list) or not alternative:
        raise RequirementsError("each alternative must be a non-empty list of key paths")
    for key in alternative:
        if not isinstance(key, str) or not _KEY_PATH_RE.fullmatch(key):
            raise RequirementsError("key paths are dotted names such as openobserve.dev.url")
    return tuple(alternative)


def missing_groups(groups: tuple[Group, ...], values: dict[str, Any]) -> tuple[MissingGroup, ...]:
    """The groups that no alternative satisfies in `values`, each with the empty keys of its closest alternative."""
    missing: list[MissingGroup] = []
    for group in groups:
        gaps = [tuple(key for key in alternative if not is_filled(lookup(values, key)))
                for alternative in group.alternatives]
        if any(not gap for gap in gaps):
            continue
        missing.append(MissingGroup(group.group_id, min(gaps, key=len)))
    return tuple(missing)


def remaining_keys(gaps: tuple[MissingGroup, ...], sources: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Key paths of the gaps the child did not report as set, without duplicates, in order."""
    sources = sources or {}
    missing: list[str] = []
    for gap in gaps:
        if gap.group_id is not None and sources.get(gap.group_id) in SET_SOURCES:
            continue
        missing.extend(key for key in gap.keys if key not in missing)
    return tuple(missing)


def missing_keys(groups: tuple[Group, ...], values: dict[str, Any]) -> tuple[str, ...]:
    """Key paths still empty for the groups that no alternative satisfies, from the closest alternative."""
    return remaining_keys(missing_groups(groups, values))


def parse_source_report(payload: Any) -> dict[str, str] | None:
    """A child's `config_status` result `{"groups": {"<id>": "<source>"}}`, or None when it has another shape."""
    groups = payload.get("groups") if isinstance(payload, dict) else None
    if not isinstance(groups, dict):
        return None
    report = {}
    for group_id, source in groups.items():
        if not isinstance(group_id, str) or not _GROUP_ID_RE.fullmatch(group_id) or source not in SOURCES:
            return None
        report[group_id] = source
    return report


def lookup(values: dict[str, Any], dotted: str) -> Any:
    node: Any = values
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def is_filled(value: Any) -> bool:
    """Empty strings, null, and the placeholder count as missing; any other value counts as filled."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() not in ("", SECRET_PLACEHOLDER)
    return True
