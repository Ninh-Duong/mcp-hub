"""Client names as MCP requests report them, normalized for logs and routing. Leaf module: no mcp_hub imports."""
from __future__ import annotations

import re
from typing import Any

_CLIENT_LABEL_RE = re.compile(r"[^A-Za-z0-9_. -]")


def normalize_client_name(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    return " ".join(_CLIENT_LABEL_RE.sub("", value).split())[:48] or "unknown"


def request_client_name(ctx: Any) -> str:
    """The normalized client name an MCP request context reports, or `unknown`."""
    client_params = getattr(getattr(ctx, "session", None), "client_params", None)
    client_info = getattr(client_params, "client_info", None)
    return normalize_client_name(getattr(client_info, "name", None)) if client_info is not None else "unknown"
