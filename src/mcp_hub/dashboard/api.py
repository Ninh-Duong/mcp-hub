from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

from ..config import HUB_ROOT
from ..jobs.store import MAX_ACTIVITY_OFFSET
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

    def status_payload() -> dict[str, Any]:
        children = {
            server_id: "connected" if server_id in runtime.clients else runtime.config.unavailable.get(server_id, "unavailable")
            for server_id in dict.fromkeys((*runtime.config.detected, *runtime.config.servers, *runtime.config.unavailable))
        }
        return {
            "ok": True, "children": children, "agents": job_runner.agent_status(),
            "projects": job_runner.list_projects(),
            "activity": runtime.activity_snapshot() if hasattr(runtime, "activity_snapshot") else {"children": {}, "events": []},
        }

    async def status(_request: Request) -> Response:
        return JSONResponse(status_payload())

    async def activity_stream(_request: Request) -> Response:
        subscribe = getattr(runtime, "subscribe_activity", None)
        unsubscribe = getattr(runtime, "unsubscribe_activity", None)
        if not callable(subscribe) or not callable(unsubscribe):
            return JSONResponse({"error": "Live activity is unavailable."}, status_code=503)
        subscriber = subscribe()

        def snapshot() -> dict[str, Any]:
            children = {
                server_id: "connected" if server_id in runtime.clients else runtime.config.unavailable.get(server_id, "unavailable")
                for server_id in dict.fromkeys((*runtime.config.detected, *runtime.config.servers, *runtime.config.unavailable))
            }
            activity = runtime.activity_snapshot()
            return {"children": children, "activity": activity}

        async def messages():
            try:
                current = snapshot()
                activity = current["activity"]
                stream_id = activity.get("stream_id", "unknown")
                sequence = activity.get("sequence", 0)
                yield _sse_message("snapshot", current, f"{stream_id}:{sequence}")
                while True:
                    try:
                        event = await asyncio.wait_for(subscriber.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event.get("resync"):
                        current = snapshot()
                        activity = current["activity"]
                        stream_id = activity.get("stream_id", "unknown")
                        sequence = activity.get("sequence", 0)
                        yield _sse_message("snapshot", current, f"{stream_id}:{sequence}")
                        continue
                    event_id = f"{event.get('stream_id', 'unknown')}:{event.get('sequence', 0)}"
                    yield _sse_message("activity", event, event_id)
            finally:
                unsubscribe(subscriber)

        return StreamingResponse(
            messages(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def activity(request: Request) -> Response:
        store = getattr(job_runner, "store", None)
        if store is None or not hasattr(store, "activity_report"):
            return JSONResponse({"error": "Activity history is unavailable."}, status_code=503)
        date_text = request.query_params.get("date") or date.today().isoformat()
        try:
            selected = date.fromisoformat(date_text)
            offset = max(0, int(request.query_params.get("offset", "0")))
            if offset > MAX_ACTIVITY_OFFSET:
                raise ValueError("Activity offset is too large")
            limit = min(250, max(1, int(request.query_params.get("limit", "100"))))
        except (TypeError, ValueError):
            return JSONResponse({"error": "Invalid activity query."}, status_code=400)
        sort_by = request.query_params.get("sort", "calls")
        if sort_by not in {"calls", "duration"}:
            return JSONResponse({"error": "Invalid activity sort."}, status_code=400)
        start = datetime.combine(selected, time.min).astimezone().astimezone(timezone.utc)
        end = datetime.combine(selected + timedelta(days=1), time.min).astimezone().astimezone(timezone.utc)
        try:
            report = store.activity_report(
                start,
                end,
                server_id=request.query_params.get("server_id") or None,
                client=request.query_params.get("client") or None,
                tool=request.query_params.get("tool") or None,
                sort_by=sort_by,
                offset=offset,
                limit=limit,
            )
        except ValueError:
            return JSONResponse({"error": "Invalid activity query."}, status_code=400)
        return JSONResponse({
            **report,
            "date": selected.isoformat(),
            "timezone": str(datetime.now().astimezone().tzinfo),
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
        Route("/dashboard/api/activity", activity, methods=["GET"]),
        Route("/dashboard/api/activity/stream", activity_stream, methods=["GET"]),
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


def _sse_message(event_name: str, data: dict[str, Any], event_id: str) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event_name}\ndata: {payload}\n\n"


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
