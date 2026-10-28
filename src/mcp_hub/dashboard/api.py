from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ..config import HUB_ROOT
from .sessions import DashboardSessions


DASHBOARD_ROOT = Path(__file__).parent


def create_dashboard_routes(
    runtime: Any,
    job_runner: Any,
    sessions: DashboardSessions,
    *,
    control_launcher: Callable[[str], None] | None = None,
    shutdown_callback: Callable[[], None] | None = None,
) -> list[Route]:
    launch_control = control_launcher or _launch_control

    async def dashboard_entry(request: Request) -> Response:
        pair = request.query_params.get("pair")
        if pair:
            session = sessions.consume_pairing_token(pair)
            if not session:
                return JSONResponse({"error": "Dashboard pairing token is invalid or expired."}, status_code=401)
            response = RedirectResponse("/dashboard/", status_code=303)
            response.set_cookie(
                "hub_session", session, httponly=True, samesite="strict", path="/dashboard", max_age=12 * 60 * 60,
            )
            return response
        return FileResponse(DASHBOARD_ROOT / "index.html", media_type="text/html; charset=utf-8")

    async def issue_pairing(request: Request) -> Response:
        token = sessions.issue_pairing_token()
        return JSONResponse({"pairing_token": token, "expires_in_seconds": 120})

    async def status(_request: Request) -> Response:
        children = {
            server_id: "connected" if server_id in runtime.clients else runtime.config.unavailable.get(server_id, "unavailable")
            for server_id in dict.fromkeys((*runtime.config.detected, *runtime.config.servers, *runtime.config.unavailable))
        }
        return JSONResponse({
            "ok": True, "children": children, "agents": job_runner.agent_status(),
            "projects": job_runner.list_projects(),
        })

    async def jobs(request: Request) -> Response:
        try:
            limit = min(500, max(1, int(request.query_params.get("limit", "100"))))
            offset = max(0, int(request.query_params.get("offset", "0")))
            return JSONResponse({"jobs": job_runner.list_jobs(limit=limit, offset=offset)})
        except (TypeError, ValueError):
            return JSONResponse({"error": "Invalid job page."}, status_code=400)

    async def job(request: Request) -> Response:
        try:
            return JSONResponse(job_runner.get_job(request.path_params["job_id"]))
        except KeyError:
            return JSONResponse({"error": "Job not found."}, status_code=404)

    async def detail(request: Request) -> Response:
        try:
            offset = max(0, int(request.query_params.get("offset", "0")))
            limit = min(10000, max(1, int(request.query_params.get("limit", "2000"))))
            return JSONResponse(job_runner.store.get_detail_page(
                request.path_params["job_id"], request.path_params["source_id"], offset=offset, limit=limit,
            ))
        except KeyError:
            return JSONResponse({"error": "Job detail not found or expired."}, status_code=404)
        except (TypeError, ValueError):
            return JSONResponse({"error": "Invalid detail page."}, status_code=400)

    async def approval_code(request: Request) -> Response:
        try:
            code = job_runner.get_approval_code(request.path_params["job_id"])
        except KeyError:
            return JSONResponse({"error": "Job not found."}, status_code=404)
        if not code:
            return JSONResponse({"approval_code": None})
        return JSONResponse({"approval_code": code})

    async def pending_call(request: Request) -> Response:
        try:
            preview = job_runner.pending_approval_preview(request.path_params["job_id"])
        except KeyError:
            return JSONResponse({"error": "Job not found."}, status_code=404)
        if preview is None:
            return JSONResponse({"error": "No pending write."}, status_code=404)
        return JSONResponse(preview)

    async def approve(request: Request) -> Response:
        try:
            accepted = job_runner.approve_from_dashboard(request.path_params["job_id"])
        except KeyError:
            return JSONResponse({"error": "Job not found."}, status_code=404)
        return JSONResponse({"approved": accepted})

    async def cancel(request: Request) -> Response:
        try:
            return JSONResponse(job_runner.cancel_job(request.path_params["job_id"]))
        except KeyError:
            return JSONResponse({"error": "Job not found."}, status_code=404)

    async def service_control(request: Request) -> Response:
        command = request.path_params["command"]
        if command not in {"stop", "restart"}:
            return JSONResponse({"error": "Unsupported control."}, status_code=404)
        launch_control(command)
        return JSONResponse({"accepted": True, "command": command})

    async def request_shutdown(_request: Request) -> Response:
        if shutdown_callback is None:
            return JSONResponse({"error": "Service shutdown is unavailable."}, status_code=503)
        shutdown_callback()
        return JSONResponse({"accepted": True})

    return [
        Route("/dashboard", dashboard_entry, methods=["GET"]),
        Route("/dashboard/", dashboard_entry, methods=["GET"]),
        Route("/dashboard/app.js", lambda _request: FileResponse(DASHBOARD_ROOT / "app.js", media_type="application/javascript"), methods=["GET"]),
        Route("/dashboard/style.css", lambda _request: FileResponse(DASHBOARD_ROOT / "style.css", media_type="text/css"), methods=["GET"]),
        Route("/dashboard/pair", issue_pairing, methods=["POST"]),
        Route("/dashboard/api/status", status, methods=["GET"]),
        Route("/dashboard/api/jobs", jobs, methods=["GET"]),
        Route("/dashboard/api/jobs/{job_id:str}", job, methods=["GET"]),
        Route("/dashboard/api/jobs/{job_id:str}/details/{source_id:str}", detail, methods=["GET"]),
        Route("/dashboard/api/jobs/{job_id:str}/approval-code", approval_code, methods=["GET"]),
        Route("/dashboard/api/jobs/{job_id:str}/pending-call", pending_call, methods=["GET"]),
        Route("/dashboard/api/jobs/{job_id:str}/approve", approve, methods=["POST"]),
        Route("/dashboard/api/jobs/{job_id:str}/cancel", cancel, methods=["POST"]),
        Route("/dashboard/api/control/{command:str}", service_control, methods=["POST"]),
        Route("/dashboard/api/service/shutdown", request_shutdown, methods=["POST"]),
    ]


def _launch_control(command: str) -> None:
    script = HUB_ROOT / "scripts" / "hubctl.ps1"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", str(script), command],
        cwd=HUB_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
    )
