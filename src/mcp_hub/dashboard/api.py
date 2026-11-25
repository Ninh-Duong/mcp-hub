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
        job_runner: Any | None = None,
    ) -> None:
        self.runtime = runtime
        self.launch_control = control_launcher or launch_hubctl
        self.shutdown_callback = shutdown_callback
        self.extensions = tuple(extensions)
        self.settings = settings or SettingsHandlers()
        source_reports = getattr(runtime, "config_source_reports", None)
        if callable(source_reports):
            self.settings.use_source_reports(source_reports)
        reload_server = getattr(runtime, "reload_server", None)
        if callable(reload_server):
            self.settings.use_reload_server(reload_server)
        self.updates = updates or UpdateHandlers(control_launcher or launch_hubctl)
        self.job_runner = job_runner

    def routes(self) -> list[Route]:
        extension_routes = [route for extension in self.extensions for route in extension.routes]
        return [
            Route("/dashboard/api/status", self.status, methods=["GET"]),
            *extension_routes,
            Route("/dashboard/api/control/{command:str}", self.service_control, methods=["POST"]),
            Route("/dashboard/api/service/shutdown", self.request_shutdown, methods=["POST"]),
            Route("/dashboard/api/jobs/pending", self.pending_jobs, methods=["GET"]),
            Route("/dashboard/api/jobs/{job_id:str}/decision", self.job_decision, methods=["POST"]),
            Route("/dashboard/api/security/pins/accept", self.accept_pins, methods=["POST"]),
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
        payload = self.status_payload()
        if self.job_runner is not None and callable(getattr(self.job_runner, "list_pending_approvals", None)):
            pending = await self.job_runner.list_pending_approvals()
            if pending:
                payload["pending_approvals"] = pending
        return JSONResponse(payload)

    async def pending_jobs(self, _request: Request) -> Response:
        if self.job_runner is None or not callable(getattr(self.job_runner, "list_pending_approvals", None)):
            return JSONResponse({"pending": []})
        return JSONResponse({"pending": await self.job_runner.list_pending_approvals()})

    async def job_decision(self, request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": 'Expected {"action": "approve"|"reject"}.'}, status_code=400)
        action = body.get("action") if isinstance(body, dict) else None
        if action not in {"approve", "reject"}:
            return JSONResponse({"error": 'Expected {"action": "approve"|"reject"}.'}, status_code=400)
        if self.job_runner is None or not callable(getattr(self.job_runner, "resolve_pending_approval", None)):
            return JSONResponse({"error": "Job approvals are unavailable."}, status_code=503)
        job_id = request.path_params["job_id"]
        result = await self.job_runner.resolve_pending_approval(job_id, approve=action == "approve")
        return JSONResponse(result)

    async def accept_pins(self, request: Request) -> Response:
        """Accept changed tool pins for one or all connected children (admin token only)."""
        server_id = None
        if await request.body():
            try:
                body = await request.json()
            except ValueError:
                return JSONResponse({"error": "Expected a JSON object."}, status_code=400)
            if not isinstance(body, dict):
                return JSONResponse({"error": "Expected a JSON object."}, status_code=400)
            server_id = body.get("server_id")
            if server_id is not None and (not isinstance(server_id, str) or not server_id.strip()):
                return JSONResponse({"error": "server_id must be a non-empty string."}, status_code=400)
        accept = getattr(self.runtime, "accept_pins", None)
        accepted = accept(server_id) if callable(accept) else []
        return JSONResponse({"accepted": accepted})

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
    job_runner: Any | None = None,
) -> list[Route]:
    handlers = DashboardHandlers(
        runtime,
        control_launcher=control_launcher, shutdown_callback=shutdown_callback, extensions=extensions,
        settings=settings,
        updates=updates,
        job_runner=job_runner,
    )
    return handlers.routes()
