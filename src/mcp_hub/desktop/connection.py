"""How the app finds the Hub: the connection file the Hub service writes at every start.

The app never reads the Hub's checkout or `config/private/`. The file holds the Hub's loopback URL and
bearer token; the token is read in process, only ever sent to the Hub, and never displayed or logged.
Its path matches `service_config.connection_file()` on the Hub side.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

CONNECTION_ENV = "MCP_HUB_CONNECTION_FILE"
PRIVATE_DIR_ENV = "MCP_HUB_PRIVATE_DIR"
_LOOPBACK = "http://127.0.0.1:"


class ConnectionMissing(ValueError):
    """There is no usable connection file: the Hub was never started for this user."""


@dataclass(frozen=True)
class Connection:
    url: str
    token: str = field(repr=False)


def connection_path() -> Path:
    override = os.environ.get(CONNECTION_ENV)
    if override:
        return Path(override)
    private = os.environ.get(PRIVATE_DIR_ENV)
    if private:
        return Path(private).resolve() / "connection.json"
    data = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "state")
    return Path(data) / "mcp-hub" / "connection.json"


def load_connection(path: Path | None = None) -> Connection:
    try:
        value = json.loads((path or connection_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConnectionMissing(f"No Hub connection file ({type(exc).__name__})") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ConnectionMissing("Unsupported Hub connection file")
    url, token = value.get("url"), value.get("token")
    # Only a Hub on this machine's loopback address ever receives the token.
    if not isinstance(url, str) or not url.startswith(_LOOPBACK) or not url[len(_LOOPBACK):].isdigit():
        raise ConnectionMissing("The Hub connection must be on 127.0.0.1")
    if not isinstance(token, str) or len(token) < 32:
        raise ConnectionMissing("The Hub connection has no valid token")
    return Connection(url=url, token=token)
