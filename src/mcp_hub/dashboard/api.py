from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from collections.abc import Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..control import launch_hubctl
from ..extensions import DashboardExtension, status_flags
from .settings_api import SettingsHandlers
from .update_api import UpdateHandlers


class DashboardHandlers:
    """Request handlers for the local Hub API used by the desktop app."""

    def __init__(
        self,
        runtime: Any,
        *,
        control_launcher: Callable[[list[str]], None] | None = None,
        shutdown_callback: Callable[[], None] | None = None,
        extensions: Sequence[DashboardExtension] = (),
        settings: SettingsHandlers | None = None,
        updates: UpdateHandlers | None = None,
    ) -> None:
        self.runtime = runtime
        self.launch_control = control_launcher or launch_hubctl
        self.shutdown_callback = shutdown_callback
        self.extensions = tuple(extensions)
        self.settings = settings or SettingsHandlers()
        self.updates = updates or UpdateHandlers(control_launcher or launch_hubctl)

    def routes(self) -> list[Route]:
        extension_routes = [route for extension in self.extensions for route in extension.routes]
        return [
            Route("/dashboard/api/status", self.status, methods=["GET"]),
            *extension_routes,
            Route("/dashboard/api/control/{command:str}", self.service_control, methods=["POST"]),
            Route("/dashboard/api/service/shutdown", self.request_shutdown, methods=["POST"]),
            *self.settings.routes(),
            *self.updates.routes(),
        ]

    def status_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": True,
            "children": self.runtime.child_states(),
            "config_incomplete": self.runtime.config_incomplete(),
            "extensions": status_flags(tuple(extension.name for extension in self.extensions)),
        }
        for extension in self.extensions:
            payload.update(extension.status_fields())
        return payload

    async def status(self, _request: Request) -> Response:
        return JSONResponse(self.status_payload())

    async def service_control(self, request: Request) -> Response:
        command = request.path_params["command"]
        if command not in {"stop", "restart"}:
            return JSONResponse({"error": "Unsupported control."}, status_code=404)
        # A restart keeps the extensions this Hub runs with (for example -Ui).
        flags = [flag for extension in self.extensions for flag in extension.hubctl_flags] if command == "restart" else []
        try:
            self.launch_control([command, *flags])
        except OSError:
            return JSONResponse({"error": "Service control is available only on Windows."}, status_code=503)
        return JSONResponse({"accepted": True, "command": command})

    async def request_shutdown(self, _request: Request) -> Response:
        if self.shutdown_callback is None:
            return JSONResponse({"error": "Service shutdown is unavailable."}, status_code=503)
        self.shutdown_callback()
        return JSONResponse({"accepted": True})


def create_dashboard_routes(
    runtime: Any,
    *,
    control_launcher: Callable[[list[str]], None] | None = None,
    shutdown_callback: Callable[[], None] | None = None,
    extensions: Sequence[DashboardExtension] = (),
    settings: SettingsHandlers | None = None,
    updates: UpdateHandlers | None = None,
) -> list[Route]:
    handlers = DashboardHandlers(
        runtime,
        control_launcher=control_launcher, shutdown_callback=shutdown_callback, extensions=extensions,
        settings=settings,
        updates=updates,
    )
    return handlers.routes()
