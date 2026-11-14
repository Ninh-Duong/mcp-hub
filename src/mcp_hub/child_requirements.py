"""Required settings a child MCP declares in its own repository (`hub-requirements.json`).

The file names dotted key paths only, never values: `{"version": 1, "groups": [{"label": ..., "anyOf":
[["openobserve.dev.url", "openobserve.dev.token"], ["seq.server_url", "seq.api_key"]]}]}`. A group is
satisfied when one of its alternatives has a filled value for every key. A missing value is reported by
key name, so no secret ever leaves the Hub through this module.

This module is a leaf: it imports nothing from the Hub, so `config` can use it.
"""
from __future__ import annotations

import json
import re
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


class RequirementsError(ValueError):
    """The requirements file exists but does not follow the format above."""


@dataclass(frozen=True)
class Group:
    label: str
    alternatives: tuple[tuple[str, ...], ...]


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
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
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
    return Group(item["label"].strip(), tuple(_parse_alternative(alternative) for alternative in any_of))


def _parse_alternative(alternative: Any) -> tuple[str, ...]:
    if not isinstance(alternative, list) or not alternative:
        raise RequirementsError("each alternative must be a non-empty list of key paths")
    for key in alternative:
        if not isinstance(key, str) or not _KEY_PATH_RE.fullmatch(key):
            raise RequirementsError("key paths are dotted names such as openobserve.dev.url")
    return tuple(alternative)


def missing_keys(groups: tuple[Group, ...], values: dict[str, Any]) -> tuple[str, ...]:
    """Key paths still empty for the groups that no alternative satisfies, from the closest alternative."""
    missing: list[str] = []
    for group in groups:
        gaps = [[key for key in alternative if not is_filled(lookup(values, key))]
                for alternative in group.alternatives]
        if any(not gap for gap in gaps):
            continue
        for key in min(gaps, key=len):
            if key not in missing:
                missing.append(key)
    return tuple(missing)


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
