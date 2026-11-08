"""Hub settings over HTTP, so local clients (the desktop status app) never edit a Hub file themselves.

Only booleans and child ids go in or out. `hub_settings` stays the only writer of `hub.json`, and
`hubctl` the only owner of the sign-in task; both run off the event loop.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .. import control, hub_settings
from ..config import ConfigError
from ..extensions import BITNET


def settings_payload(settings: hub_settings.HubSettings) -> dict[str, Any]:
    return {
        "editable": settings.editable,
        "activity_ui": settings.activity_ui,
        "bitnet": settings.bitnet,
        "servers": [{"id": item.server_id, "enabled": item.enabled, "reason": item.reason} for item in settings.servers],
    }


async def _enabled(request: Request) -> bool | None:
    """The `{"enabled": true|false}` body, or None when it is anything else."""
    try:
        body = await request.json()
    except ValueError:
        return None
    value = body.get("enabled") if isinstance(body, dict) else None
    return value if isinstance(value, bool) else None


def _error(message: str, status_code: int) -> Response:
    return JSONResponse({"error": message}, status_code=status_code)


class SettingsHandlers:
    def __init__(
        self,
        *,
        store: Any = hub_settings,
        read_autostart: Callable[[], dict[str, bool]] = control.autostart_state,
        write_autostart: Callable[[str, bool], dict[str, bool]] = control.set_autostart,
    ) -> None:
        self.store, self.read_autostart, self.write_autostart = store, read_autostart, write_autostart

    def routes(self) -> list[Route]:
        return [
            Route("/dashboard/api/settings", self.settings, methods=["GET"]),
            Route("/dashboard/api/settings/tracking", self.tracking, methods=["POST"]),
            Route("/dashboard/api/settings/bitnet", self.bitnet, methods=["POST"]),
            Route("/dashboard/api/settings/servers/{server_id:str}", self.server, methods=["POST"]),
            Route("/dashboard/api/settings/autostart", self.autostart, methods=["GET", "POST"]),
        ]

    async def _read(self) -> Response:
        try:
            return JSONResponse(settings_payload(await asyncio.to_thread(self.store.read_settings)))
        except (ConfigError, OSError):
            return _error("Hub settings could not be read.", 500)

    async def settings(self, _request: Request) -> Response:
        return await self._read()

    async def tracking(self, request: Request) -> Response:
        enabled = await _enabled(request)
        if enabled is None:
            return _error('Expected {"enabled": true|false}.', 400)
        return await self._write(self.store.set_activity_ui, enabled)

    async def bitnet(self, request: Request) -> Response:
        enabled = await _enabled(request)
        if enabled is None:
            return _error('Expected {"enabled": true|false}.', 400)
        return await self._write(self.store.set_extension, BITNET, enabled)

    async def server(self, request: Request) -> Response:
        enabled = await _enabled(request)
        if enabled is None:
            return _error('Expected {"enabled": true|false}.', 400)
        server_id = request.path_params["server_id"]
        try:
            current = await asyncio.to_thread(self.store.read_settings)
        except (ConfigError, OSError):
            return _error("Hub settings could not be read.", 500)
        if server_id not in {item.server_id for item in current.servers}:
            return _error("Unknown child MCP.", 404)
        return await self._write(self.store.set_server_enabled, server_id, enabled)

    async def _write(self, write: Callable[..., None], *args: Any) -> Response:
        """Apply one change; a custom config (MCP_HUB_CONFIG) or an unreadable file is a conflict."""
        try:
            await asyncio.to_thread(write, *args)
        except (ConfigError, OSError):
            return _error("Hub settings could not be changed here.", 409)
        return await self._read()

    async def autostart(self, request: Request) -> Response:
        """The Hub's sign-in task only; the desktop app manages its own."""
        try:
            if request.method == "GET":
                state = await asyncio.to_thread(self.read_autostart)
            else:
                enabled = await _enabled(request)
                if enabled is None:
                    return _error('Expected {"enabled": true|false}.', 400)
                state = await asyncio.to_thread(self.write_autostart, "hub", enabled)
        except OSError:
            return _error("The sign-in task could not be read or changed.", 503)
        return JSONResponse({"hub": state["hub"]})
