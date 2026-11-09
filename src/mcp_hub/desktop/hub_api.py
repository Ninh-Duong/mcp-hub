"""The Hub's settings and service control, over its HTTP API: the app never edits a Hub file itself.

Every call raises OSError (the Hub is unreachable or refused) or ValueError (no connection file, or an
unexpected answer); the window turns either into a footer message.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import status
from .connection import load_connection

CONTROL_COMMANDS = ("restart", "stop")


@dataclass(frozen=True)
class ServerSwitch:
    server_id: str
    enabled: bool
    # Why an enabled child cannot run yet (for example "credentials required"), else None.
    reason: str | None = None


@dataclass(frozen=True)
class HubSettings:
    activity_ui: bool
    servers: tuple[ServerSwitch, ...]
    # False when the Hub runs with a custom MCP_HUB_CONFIG: the app then only shows the values.
    editable: bool
    bitnet: bool = False


def settings_from(value: dict[str, Any]) -> HubSettings:
    servers = value.get("servers")
    if not isinstance(servers, list) or not all(isinstance(item, dict) for item in servers):
        raise ValueError("The Hub returned unexpected settings")
    switches = tuple(
        ServerSwitch(str(item.get("id")), item.get("enabled") is True,
                     item.get("reason") if isinstance(item.get("reason"), str) else None)
        for item in servers
    )
    return HubSettings(
        value.get("activity_ui") is True,
        switches,
        value.get("editable") is True,
        bitnet=value.get("bitnet") is True,
    )


class HubClient:
    def __init__(self, *, opener: status.Opener = status.OPENER.open, connection_file: Path | None = None) -> None:
        self.opener, self.connection_file = opener, connection_file

    def _call(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        connection = load_connection(self.connection_file)
        method = "GET" if body is None else "POST"
        return status.request_json(self.opener, f"{connection.url}{path}", connection.token, method=method, body=body)

    def read_settings(self) -> HubSettings:
        return settings_from(self._call("/dashboard/api/settings"))

    def set_activity_ui(self, enabled: bool) -> None:
        self._call("/dashboard/api/settings/tracking", {"enabled": enabled})

    def set_bitnet(self, enabled: bool) -> None:
        self._call("/dashboard/api/settings/bitnet", {"enabled": enabled})

    def install_bitnet(self) -> None:
        """Start the BitNet install in the Hub; its progress comes with the next status reads."""
        self._call("/dashboard/api/bitnet/install", {})

    def set_server_enabled(self, server_id: str, enabled: bool) -> None:
        self._call(f"/dashboard/api/settings/servers/{quote(server_id, safe='')}", {"enabled": enabled})

    def read_hub_autostart(self) -> bool:
        return _hub_flag(self._call("/dashboard/api/settings/autostart"))

    def set_hub_autostart(self, enabled: bool) -> bool:
        return _hub_flag(self._call("/dashboard/api/settings/autostart", {"enabled": enabled}))

    def control(self, command: str) -> None:
        """Restart or stop the Hub; starting it is not an HTTP call (system.start_hub)."""
        if command not in CONTROL_COMMANDS:
            raise ValueError(f"Unknown Hub command: {command}")
        self._call(f"/dashboard/api/control/{command}", {})


def _hub_flag(value: dict[str, Any]) -> bool:
    if not isinstance(value.get("hub"), bool):
        raise ValueError("The Hub returned an unexpected sign-in state")
    return value["hub"]
