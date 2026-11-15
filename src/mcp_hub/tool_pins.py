"""Pins for child tool definitions: a changed tool is noticed, and only hashes are stored."""
from __future__ import annotations

import hashlib
import json

from mcp.types import Tool

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
