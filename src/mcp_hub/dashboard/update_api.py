"""Admin-only update discovery, orchestration and persisted progress API."""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone
from typing import Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .. import control, update_state

_SERVER_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class UpdateHandlers:
    def __init__(self, launch: Callable[[list[str]], None] = control.launch_hubctl) -> None:
        self.launch = launch
        self._launch_lock = asyncio.Lock()

    def routes(self) -> list[Route]:
        return [
            Route("/dashboard/api/update", self.status, methods=["GET"]),
            Route("/dashboard/api/update", self.start, methods=["POST"]),
            Route("/dashboard/api/update/check", self.check, methods=["POST"]),
            Route("/dashboard/api/update/retry", self.retry, methods=["POST"]),
        ]

    async def status(self, _request: Request) -> Response:
        return JSONResponse(await asyncio.to_thread(update_state.read))

    async def start(self, _request: Request) -> Response:
        async with self._launch_lock:
            if await self._is_running():
                return self._already_running()
            return await self._launch(["update"])

    async def check(self, _request: Request) -> Response:
        async with self._launch_lock:
            if await self._is_running():
                return self._already_running()
            return await self._launch(["update", "-Check"])

    async def retry(self, request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            body = None
        ids = body.get("components") if isinstance(body, dict) else None
        if not isinstance(ids, list) or not ids or len(ids) > update_state.MAX_COMPONENTS:
            return JSONResponse({"error": "Expected a non-empty components list."}, status_code=400)
        if any(not isinstance(item, str) or not _SERVER_ID.fullmatch(item) for item in ids):
            return JSONResponse({"error": "Invalid component id."}, status_code=400)
        async with self._launch_lock:
            current = await asyncio.to_thread(update_state.read)
            if current.get("state") == "running":
                return self._already_running()
            failed = {item["id"] for item in current.get("components", [])
                      if item.get("state") == "failed" and isinstance(item.get("id"), str)}
            if not set(ids).issubset(failed):
                return JSONResponse({"error": "Retry accepts failed component ids from the latest run."}, status_code=400)
            return await self._launch(["update", "-RetryIds", ",".join(sorted(set(ids)))])

    async def _is_running(self) -> bool:
        return (await asyncio.to_thread(update_state.read)).get("state") == "running"

    @staticmethod
    def _already_running() -> Response:
        return JSONResponse({"error": "An update is already running."}, status_code=409)

    async def _launch(self, arguments: list[str]) -> Response:
        run_id = str(uuid.uuid4())
        mode = "check" if "-Check" in arguments else ("retry" if "-RetryIds" in arguments else "update")
        value = {"run_id": run_id, "state": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                 "updated_at": datetime.now(timezone.utc).isoformat(), "components": [],
                 "events": [{"component": "mcp-hub", "phase": mode, "state": "running",
                             "error_code": None, "at": datetime.now(timezone.utc).isoformat()}]}
        try:
            await asyncio.to_thread(update_state.write, value)
            await asyncio.to_thread(self.launch, [*arguments, "-RunId", run_id])
        except OSError:
            value.update(state="partial")
            value["events"][-1].update(state="failed", error_code="process_start_failed")
            await asyncio.to_thread(update_state.write, value)
            return JSONResponse(update_state.read(), status_code=503)
        return JSONResponse(value)
