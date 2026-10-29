from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from contextlib import AsyncExitStack

import uvicorn

from .audit import AuditLog
from .config import load_config
from .http import create_http_app
from .agents.config import load_agent_configs
from .dashboard.api import create_dashboard_routes
from .dashboard.sessions import DashboardSessions
from .jobs.projects import load_projects
from .jobs.runner import JobRunner
from .jobs.store import JobStore
from .server import HubRuntime, create_mcp_server
from .service_config import load_service_config

SHUTDOWN_DRAIN_SECONDS = 20


async def run_http(host: str | None = None, port: int | None = None) -> None:
    service_config = load_service_config()
    listen_host = host or service_config.host
    listen_port = port if port is not None else service_config.port
    if listen_host != service_config.host or listen_port != service_config.port:
        raise ValueError("HTTP bind settings must match the private service configuration")

    audit = AuditLog()
    try:
        config = load_config()
    except Exception as exc:
        audit.write("config.load.failed", level=logging.ERROR, error_type=type(exc).__name__, error=str(exc))
        raise

    agent_configs = load_agent_configs()
    projects = load_projects()
    job_store = JobStore()
    job_store.mark_running_interrupted()
    job_store.mark_tool_calls_interrupted()
    job_store.purge_expired()

    runtime = HubRuntime(config, audit, activity_store=job_store)
    try:
        async with AsyncExitStack() as stack:
            await runtime.connect(stack)
            job_runner = JobRunner(store=job_store, runtime=runtime, projects=projects, agents=agent_configs)
            job_runner.resume_queued_jobs()
            mcp_server = create_mcp_server(runtime, job_runner)
            sessions = DashboardSessions()
            shutdown_event = asyncio.Event()
            app = create_http_app(
                mcp_server,
                service_config.token,
                host=listen_host,
                port=listen_port,
                custom_routes=create_dashboard_routes(
                    runtime, job_runner, sessions, shutdown_callback=shutdown_event.set,
                ),
                sessions=sessions,
            )
            audit.write(
                "hub.started",
                servers=list(config.servers),
                tool_count=len(runtime.public_tools) + len(job_runner.tool_definitions()),
                transport="streamable-http",
                host=listen_host,
                port=listen_port,
            )
            server = _create_uvicorn_server(app, host=listen_host, port=listen_port)
            shutdown_task = asyncio.create_task(
                _stop_when_requested(server, shutdown_event), name="mcp-hub-shutdown",
            )
            purge_task = asyncio.create_task(_purge_expired_loop(job_store), name="mcp-hub-job-purge")
            try:
                await server.serve()
            finally:
                shutdown_task.cancel()
                with suppress(asyncio.CancelledError):
                    await shutdown_task
                purge_task.cancel()
                with suppress(asyncio.CancelledError):
                    await purge_task
                await job_runner.close()
                audit.write("hub.stopped")
    finally:
        job_store.close()


async def _purge_expired_loop(store: JobStore) -> None:
    while True:
        await asyncio.sleep(24 * 60 * 60)
        store.purge_expired()


async def _stop_when_requested(server: uvicorn.Server, shutdown_event: asyncio.Event) -> None:
    await shutdown_event.wait()
    server.should_exit = True


def _create_uvicorn_server(app, *, host: str, port: int) -> uvicorn.Server:
    return uvicorn.Server(uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        timeout_graceful_shutdown=SHUTDOWN_DRAIN_SECONDS,
    ))


def main() -> None:
    asyncio.run(run_http())
