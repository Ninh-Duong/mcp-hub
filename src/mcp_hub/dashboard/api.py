from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ..config import HUB_ROOT
from ..extensions import DashboardExtension, status_flags
from .sessions import DashboardSessions


DASHBOARD_ROOT = Path(__file__).parent
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60
PAIRING_TTL_SECONDS = 120

_JAVASCRIPT = "application/javascript"
# barn.js, activity.js, and stream.js belong to the activity UI; app.js imports them only when it is on.
_JS_MODULES = (
    "app.js", "api.js", "dom.js", "i18n.js", "state.js", "status.js", "jobs.js", "ui_extension.js",
    "barn.js", "activity.js", "stream.js",
)

# Published path below /dashboard/ -> (file relative to DASHBOARD_ROOT, media type).
# Only these files are served; every other path under /dashboard/ returns 404.
STATIC_FILES: dict[str, tuple[str, str]] = {
    **{name: (name, _JAVASCRIPT) for name in _JS_MODULES},
    "style.css": ("style.css", "text/css"),
    "locales/en.json": ("locales/en.json", "application/json"),
    "locales/vi.json": ("locales/vi.json", "application/json"),
}


class DashboardHandlers:
    """Request handlers for the dashboard page, its static assets, and its JSON API."""

    def __init__(
        self,
        runtime: Any,
        job_runner: Any,
        sessions: DashboardSessions,
        *,
        control_launcher: Callable[[list[str]], None] | None = None,
        shutdown_callback: Callable[[], None] | None = None,
        extensions: Sequence[DashboardExtension] = (),
    ) -> None:
        self.runtime = runtime
        self.job_runner = job_runner
        self.sessions = sessions
        self.launch_control = control_launcher or _launch_control
        self.shutdown_callback = shutdown_callback
        self.extensions = tuple(extensions)

    def routes(self) -> list[Route]:
        extension_routes = [route for extension in self.extensions for route in extension.routes]
        return [
            Route("/dashboard", self.dashboard_entry, methods=["GET"]),
            Route("/dashboard/", self.dashboard_entry, methods=["GET"]),
            Route("/dashboard/pair", self.issue_pairing, methods=["POST"]),
            Route("/dashboard/api/status", self.status, methods=["GET"]),
            *extension_routes,
            Route("/dashboard/api/jobs", self.jobs, methods=["GET"]),
            Route("/dashboard/api/jobs/{job_id:str}", self.job, methods=["GET"]),
            Route("/dashboard/api/jobs/{job_id:str}/details/{source_id:str}", self.detail, methods=["GET"]),
            Route("/dashboard/api/jobs/{job_id:str}/approval-code", self.approval_code, methods=["GET"]),
            Route("/dashboard/api/jobs/{job_id:str}/pending-call", self.pending_call, methods=["GET"]),
            Route("/dashboard/api/jobs/{job_id:str}/approve", self.approve, methods=["POST"]),
            Route("/dashboard/api/jobs/{job_id:str}/cancel", self.cancel, methods=["POST"]),
            Route("/dashboard/api/control/{command:str}", self.service_control, methods=["POST"]),
            Route("/dashboard/api/service/shutdown", self.request_shutdown, methods=["POST"]),
            # Keep last: it matches any remaining GET path below /dashboard/.
            Route("/dashboard/{asset:path}", self.static_asset, methods=["GET"]),
        ]

    async def dashboard_entry(self, request: Request) -> Response:
        pair = request.query_params.get("pair")
        if not pair:
            return FileResponse(DASHBOARD_ROOT / "index.html", media_type="text/html; charset=utf-8")
        session = self.sessions.consume_pairing_token(pair)
        if not session:
            return JSONResponse({"error": "Dashboard pairing token is invalid or expired."}, status_code=401)
        response = RedirectResponse("/dashboard/", status_code=303)
        response.set_cookie(
            "hub_session", session, httponly=True, samesite="strict", path="/dashboard", max_age=SESSION_MAX_AGE_SECONDS,
        )
        return response

    async def static_asset(self, request: Request) -> Response:
        entry = STATIC_FILES.get(request.path_params["asset"])
        if entry is None:
            return JSONResponse({"error": "Not found."}, status_code=404)
        file_name, media_type = entry
        return FileResponse(DASHBOARD_ROOT / file_name, media_type=media_type)

    async def issue_pairing(self, _request: Request) -> Response:
        token = self.sessions.issue_pairing_token()
        return JSONResponse({"pairing_token": token, "expires_in_seconds": PAIRING_TTL_SECONDS})

    def status_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": True,
            "children": self.runtime.child_states(),
            "agents": self.job_runner.agent_status(),
            "projects": self.job_runner.list_projects(),
            "extensions": status_flags(tuple(extension.name for extension in self.extensions)),
        }
        for extension in self.extensions:
            payload.update(extension.status_fields())
        return payload

    async def status(self, _request: Request) -> Response:
        return JSONResponse(self.status_payload())

    async def jobs(self, request: Request) -> Response:
        try:
            limit = min(500, max(1, int(request.query_params.get("limit", "100"))))
            offset = max(0, int(request.query_params.get("offset", "0")))
            jobs = await asyncio.to_thread(self.job_runner.list_jobs, limit=limit, offset=offset)
            return JSONResponse({"jobs": jobs})
        except (TypeError, ValueError):
            return JSONResponse({"error": "Invalid job page."}, status_code=400)

    async def job(self, request: Request) -> Response:
        try:
            return JSONResponse(await asyncio.to_thread(self.job_runner.get_job, request.path_params["job_id"]))
        except KeyError:
            return _job_not_found()

    async def detail(self, request: Request) -> Response:
        try:
            offset = max(0, int(request.query_params.get("offset", "0")))
            limit = min(10000, max(1, int(request.query_params.get("limit", "2000"))))
            return JSONResponse(await asyncio.to_thread(
                self.job_runner.store.get_detail_page,
                request.path_params["job_id"], request.path_params["source_id"], offset=offset, limit=limit,
            ))
        except KeyError:
            return JSONResponse({"error": "Job detail not found or expired."}, status_code=404)
        except (TypeError, ValueError):
            return JSONResponse({"error": "Invalid detail page."}, status_code=400)

    async def approval_code(self, request: Request) -> Response:
        try:
            code = await asyncio.to_thread(self.job_runner.get_approval_code, request.path_params["job_id"])
        except KeyError:
            return _job_not_found()
        return JSONResponse({"approval_code": code or None})

    async def pending_call(self, request: Request) -> Response:
        try:
            preview = await asyncio.to_thread(self.job_runner.pending_approval_preview, request.path_params["job_id"])
        except KeyError:
            return _job_not_found()
        if preview is None:
            return JSONResponse({"error": "No pending write."}, status_code=404)
        return JSONResponse(preview)

    async def approve(self, request: Request) -> Response:
        try:
            accepted = self.job_runner.approve_from_dashboard(request.path_params["job_id"])
        except KeyError:
            return _job_not_found()
        return JSONResponse({"approved": accepted})

    async def cancel(self, request: Request) -> Response:
        try:
            return JSONResponse(self.job_runner.cancel_job(request.path_params["job_id"]))
        except KeyError:
            return _job_not_found()

    async def service_control(self, request: Request) -> Response:
        command = request.path_params["command"]
        if command not in {"stop", "restart"}:
            return JSONResponse({"error": "Unsupported control."}, status_code=404)
        # A restart keeps the extensions this Hub runs with (for example -Ui).
        flags = [flag for extension in self.extensions for flag in extension.hubctl_flags] if command == "restart" else []
        self.launch_control([command, *flags])
        return JSONResponse({"accepted": True, "command": command})

    async def request_shutdown(self, _request: Request) -> Response:
        if self.shutdown_callback is None:
            return JSONResponse({"error": "Service shutdown is unavailable."}, status_code=503)
        self.shutdown_callback()
        return JSONResponse({"accepted": True})


def create_dashboard_routes(
    runtime: Any,
    job_runner: Any,
    sessions: DashboardSessions,
    *,
    control_launcher: Callable[[list[str]], None] | None = None,
    shutdown_callback: Callable[[], None] | None = None,
    extensions: Sequence[DashboardExtension] = (),
) -> list[Route]:
    handlers = DashboardHandlers(
        runtime, job_runner, sessions,
        control_launcher=control_launcher, shutdown_callback=shutdown_callback, extensions=extensions,
    )
    return handlers.routes()


def _job_not_found() -> Response:
    return JSONResponse({"error": "Job not found."}, status_code=404)


def _launch_control(arguments: list[str]) -> None:
    """Run `hubctl.ps1 <command> [flags]` detached, without a window."""
    script = HUB_ROOT / "scripts" / "hubctl.ps1"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", str(script), *arguments],
        cwd=HUB_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
    )
