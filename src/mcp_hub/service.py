from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, suppress
from typing import Any, Callable

import uvicorn

from .audit import AuditLog
from .config import HubConfig, load_config
from .extensions import ACTIVITY_UI, KNOWN_EXTENSIONS, read_launch_extensions
from .http import create_http_app
from .agents.config import load_agent_configs
from .dashboard.api import create_dashboard_routes
from .dashboard.sessions import DashboardSessions
from .jobs.projects import load_projects
from .jobs.runner import JobRunner
from .jobs.store import JobStore
from .preflight import refresh_windows_path
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
    requested = read_launch_extensions()
    config = _load_hub_config(audit)
    agent_configs = load_agent_configs()
    projects = load_projects()
    job_store = _open_job_store()
    runtime = HubRuntime(config, audit)
    try:
        async with AsyncExitStack() as stack:
            await runtime.connect(stack)
            job_runner = JobRunner(store=job_store, runtime=runtime, projects=projects, agents=agent_configs)
            job_runner.resume_queued_jobs()
            ui = install_extensions(requested, runtime, job_store, audit)
            await _serve(runtime, job_runner, ui, listen_host, listen_port, service_config.token, job_store)
    finally:
        job_store.close()


async def _serve(
    runtime: HubRuntime, job_runner: JobRunner, ui: Any | None, host: str, port: int, token: str, job_store: JobStore,
) -> None:
    shutdown_event = asyncio.Event()
    app = _build_http_app(runtime, job_runner, token, host, port, shutdown_event, ui)
    runtime.audit.write(
        "hub.started",
        servers=list(runtime.config.servers),
        tool_count=len(runtime.public_tools) + len(job_runner.tool_definitions()),
        transport="streamable-http",
        host=host,
        port=port,
        extensions=[ACTIVITY_UI] if ui is not None else [],
    )
    if ui is not None:
        ui.start()
    try:
        await _serve_until_stopped(
            app, host, port, shutdown_event, job_store, ui.close_listeners if ui is not None else None,
        )
    finally:
        await job_runner.close()
        if ui is not None:
            await ui.close()  # after the runner, so calls it interrupted are written too
        runtime.audit.write("hub.stopped")


def install_extensions(requested: frozenset[str], runtime: HubRuntime, store: JobStore, audit: AuditLog) -> Any | None:
    """Install the extensions requested for this start; the default is none (fast mode)."""
    for name in sorted(requested - KNOWN_EXTENSIONS):
        audit.write("extension.unknown", level=logging.WARNING, extension=name)
    if ACTIVITY_UI not in requested:
        return None
    from .activity_ui import install  # imported only when requested; core modules never import it

    return install(runtime, store=store, audit=audit)


def _load_hub_config(audit: AuditLog) -> HubConfig:
    try:
        return load_config()
    except Exception as exc:
        audit.write("config.load.failed", level=logging.ERROR, error_type=type(exc).__name__, error=str(exc))
        raise


def _open_job_store() -> JobStore:
    """Open the job store and settle work left over from a previous run."""
    job_store = JobStore()
    job_store.mark_running_interrupted()
    job_store.purge_expired()
    return job_store


def _build_http_app(
    runtime: HubRuntime, job_runner: JobRunner, token: str, host: str, port: int,
    shutdown_event: asyncio.Event, ui: Any | None = None,
):
    sessions = DashboardSessions()
    server = create_mcp_server(runtime, job_runner)
    if ui is not None:
        ui.attach_server(server)
    routes = create_dashboard_routes(
        runtime, job_runner, sessions, shutdown_callback=shutdown_event.set,
        extensions=(ui.dashboard_extension(),) if ui is not None else (),
    )
    return create_http_app(
        server, token, host=host, port=port, custom_routes=routes, sessions=sessions,
        middleware=ui.middleware() if ui is not None else (),
    )


async def _serve_until_stopped(
    app, host: str, port: int, shutdown_event: asyncio.Event, job_store: JobStore,
    close_listeners: Callable[[], None] | None = None,
) -> None:
    server = _create_uvicorn_server(app, host=host, port=port)
    shutdown_task = asyncio.create_task(
        _stop_when_requested(server, shutdown_event, close_listeners), name="mcp-hub-shutdown",
    )
    purge_task = asyncio.create_task(_purge_expired_loop(job_store), name="mcp-hub-job-purge")
    try:
        await server.serve()
    finally:
        await _cancel(shutdown_task)
        await _cancel(purge_task)


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _purge_expired_loop(store: JobStore) -> None:
    while True:
        await asyncio.sleep(24 * 60 * 60)
        await asyncio.to_thread(store.purge_expired)


async def _stop_when_requested(
    server: uvicorn.Server, shutdown_event: asyncio.Event, close_listeners: Callable[[], None] | None = None,
) -> None:
    await shutdown_event.wait()
    if close_listeners is not None:
        close_listeners()  # bridge listen streams would otherwise hold the drain open
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
    # Children find a runtime that preflight installed with winget after this user signed in.
    refresh_windows_path()
    asyncio.run(run_http())
