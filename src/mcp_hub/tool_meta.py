"""Reading a tool's MCP annotations. Leaf module: no mcp_hub imports."""
from __future__ import annotations

from typing import Any


def is_read_only_tool(tool: Any) -> bool:
    """True only when the child declared `readOnlyHint=true` for this tool (a Tool or its dict form)."""
    annotations = tool.get("annotations") if isinstance(tool, dict) else getattr(tool, "annotations", None)
    if isinstance(annotations, dict):
        return annotations.get("readOnlyHint") is True or annotations.get("read_only_hint") is True
    return getattr(annotations, "read_only_hint", None) is True
