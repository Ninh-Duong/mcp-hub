"""Pins for child tool definitions: a changed tool is noticed, and only hashes are stored."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from mcp.types import Tool

FILE_VERSION = 1
# The wire fields that define what a tool does. Title, icons and _meta are presentation only.
PIN_FIELDS = ("name", "description", "inputSchema", "outputSchema", "annotations")


def fingerprint(tool: Tool) -> str:
    """SHA-256 hex digest of the canonical JSON of the fields that define a tool."""
    wire = tool.model_dump(by_alias=True, mode="json")
    canonical = {key: wire.get(key) for key in PIN_FIELDS}
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Finding codes in the order they are reported. Each code lists inclusive code point ranges.
_CHARACTER_FINDINGS: tuple[tuple[str, tuple[tuple[int, int], ...]], ...] = (
    ("tag-characters", ((0xE0000, 0xE007F),)),
    ("bidi-control", ((0x202A, 0x202E), (0x2066, 0x2069))),
    ("zero-width", ((0x200B, 0x200D), (0x2060, 0x2060), (0xFEFF, 0xFEFF))),
)


def description_findings(text: str, max_chars: int) -> list[str]:
    """Finding codes for a tool description; an empty list means the text is clean."""
    found = [code for code, ranges in _CHARACTER_FINDINGS if _has_codepoint(text, ranges)]
    if len(text) > max_chars:
        found.append("too-long")
    return found


def _has_codepoint(text: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(low <= ord(char) <= high for char in text for low, high in ranges)


@dataclass(frozen=True)
class PinVerdict:
    """The tool names of one server, sorted by how they compare with the stored pins."""

    new: tuple[str, ...]
    same: tuple[str, ...]
    changed: tuple[str, ...]


class PinStore:
    """Hashes of each server's tool definitions, kept in one small JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._servers: dict[str, dict[str, str]] = {}
        self.corrupt = False

    def load(self) -> None:
        """Read the pins; a missing file is empty, an unreadable or malformed one is empty and corrupt."""
        self._servers = {}
        self.corrupt = False
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            self.corrupt = True
            return
        servers = _valid_servers(value)
        if servers is None:
            self.corrupt = True
        else:
            self._servers = servers

    def check(self, server_id: str, tools: Iterable[Tool]) -> PinVerdict:
        """Compare a server's tools with the pins; a new tool is pinned now, a changed one waits for accept."""
        known = self._servers.setdefault(server_id, {})
        new: list[str] = []
        same: list[str] = []
        changed: list[str] = []
        listed: set[str] = set()
        for tool in tools:
            listed.add(tool.name)
            digest = fingerprint(tool)
            if tool.name not in known:
                known[tool.name] = digest
                new.append(tool.name)
            elif known[tool.name] == digest:
                same.append(tool.name)
            else:
                changed.append(tool.name)
        for name in set(known) - listed:
            del known[name]
        return PinVerdict(tuple(new), tuple(same), tuple(changed))

    def accept(self, server_id: str, tool: Tool) -> None:
        """Replace a tool's stored pin with its current definition."""
        self._servers.setdefault(server_id, {})[tool.name] = fingerprint(tool)

    def snapshot(self) -> dict[str, dict[str, str]]:
        """A copy of the pins, taken on the calling thread: later checks cannot change it."""
        return {server_id: dict(tools) for server_id, tools in self._servers.items()}

    def save(self, snapshot: dict[str, dict[str, str]] | None = None) -> None:
        """Write the pins atomically: a temporary file in the same folder, then os.replace.

        Pass a snapshot taken on the calling thread when this runs on a worker thread. Without one, the current pins
        are copied here.
        """
        servers = self.snapshot() if snapshot is None else snapshot
        self._write({"version": FILE_VERSION, "servers": servers})

    def _write(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self._path.parent, prefix="tool-pins-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True, indent=2)
            os.replace(temporary, self._path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def _valid_servers(value: Any) -> dict[str, dict[str, str]] | None:
    """The servers table when the file has the expected shape, otherwise None."""
    if not isinstance(value, dict) or value.get("version") != FILE_VERSION:
        return None
    servers = value.get("servers")
    if not isinstance(servers, dict):
        return None
    result: dict[str, dict[str, str]] = {}
    for server_id, tools in servers.items():
        if not isinstance(tools, dict) or not all(isinstance(digest, str) for digest in tools.values()):
            return None
        result[server_id] = dict(tools)
    return result
