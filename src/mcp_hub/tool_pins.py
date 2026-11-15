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
