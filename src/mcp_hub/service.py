from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Any, Callable

import uvicorn

from .audit import AuditLog
from .config import HUB_ROOT, HubConfig, load_config
from .extensions import ACTIVITY_UI, BITNET, KNOWN_EXTENSIONS, read_launch_extensions, saved_extensions
from .http import create_http_app
from .agents.config import load_agent_configs
from .dashboard.api import create_dashboard_routes
from .dashboard.sessions import DashboardSessions
from .playbooks import Playbook, load_playbooks
from .projects import load_projects
from .jobs.runner import JobRunner
from .jobs.store import JobStore
from .preflight import refresh_windows_path
from .server import HubRuntime, create_mcp_server
from .service_config import load_service_config, new_admin_token, write_connection_file
from .settings import HttpSettings, Settings, load_settings


async def run_http() -> None:
    """Run the shared service on 127.0.0.1 and the configured port (settings.http.port)."""
    settings = _load_settings()
    service_config = load_service_config(port=settings.http.port)
    admin_token = new_admin_token()
    audit = AuditLog(logs=settings.logs)
    await asyncio.to_thread(_publish_connection, service_config, admin_token, audit)
    requested = read_launch_extensions(audit=audit) | saved_extensions()
    config, playbooks = _load_hub_config(audit)
    agent_configs = load_agent_configs()
    projects = load_projects()
    job_store = _open_job_store(settings.jobs.retention_days)
    runtime = HubRuntime(config, audit, settings, playbooks=playbooks, projects=projects)
    try:
        async with AsyncExitStack() as stack:
            await runtime.connect(stack)
            job_runner = JobRunner(
                store=job_store, runtime=runtime, projects=projects, agents=agent_configs, limits=settings.jobs,
            )
            job_runner.resume_queued_jobs()
            job_runner.watch_pending_approvals()
            hub_root = getattr(config, "root", None) or HUB_ROOT
            extensions = install_extensions(
                requested, runtime, job_store, audit, job_runner=job_runner, root=hub_root,
            )
            tokens = (service_config.token, admin_token)
            await _serve(runtime, job_runner, extensions, service_config.host, service_config.port, tokens, job_store)
    finally:
        job_store.close()


async def _serve(
    runtime: HubRuntime, job_runner: JobRunner, extensions: dict[str, Any] | Any, host: str, port: int,
    tokens: tuple[str, str], job_store: JobStore,
) -> None:
    """`tokens` is (client token, admin token)."""
    ext_map = extensions if isinstance(extensions, dict) else ({ACTIVITY_UI: extensions} if extensions is not None else {})
    shutdown_event = asyncio.Event()
    app = _build_http_app(runtime, job_runner, tokens, host, port, shutdown_event, ext_map)
    installed_names = list(ext_map.keys())
    runtime.audit.write(
        "hub.started",
        servers=list(runtime.config.servers),
        tool_count=len(runtime.public_tools) + len(job_runner.tool_definitions()),
        transport="streamable-http",
        host=host,
        port=port,
        extensions=installed_names,
    )
    for ext in ext_map.values():
        if hasattr(ext, "start"):
            res = ext.start()
            if asyncio.iscoroutine(res):
                await res
    ui = ext_map.get(ACTIVITY_UI)
    try:
        await _serve_until_stopped(
            app, host, port, shutdown_event, job_store, ui.close_listeners if ui is not None else None,
            drain_seconds=getattr(runtime, "settings", Settings()).http.shutdown_drain_seconds,
        )
    finally:
        await job_runner.close()
        for ext in ext_map.values():
            if hasattr(ext, "close"):
                res = ext.close()
                if asyncio.iscoroutine(res):
                    await res
        runtime.audit.write("hub.stopped")


def install_extensions(
    requested: frozenset[str],
    runtime: HubRuntime,
    store: JobStore | None = None,
    audit: AuditLog | None = None,
    *,
    job_runner: JobRunner | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Install the extensions requested for this start; the default is none (fast mode)."""
    for name in sorted(requested - KNOWN_EXTENSIONS):
        if audit is not None:
            audit.write("extension.unknown", level=logging.WARNING, extension=name)
    installed: dict[str, Any] = {}
    if ACTIVITY_UI in requested:
        from .activity_ui import install as install_activity_ui

        installed[ACTIVITY_UI] = install_activity_ui(runtime, store=store, audit=audit)
    hub_root = root or getattr(getattr(runtime, "config", None), "root", None) or HUB_ROOT
    if BITNET in requested:
        from .bitnet import install as install_bitnet

        installed[BITNET] = install_bitnet(runtime, job_runner, root=hub_root, audit=audit)
    else:
        # Only BitNet's install state and route, so the desktop app can offer the install; no tool-call hooks.
        from .bitnet import install_setup
        from .bitnet.setup import SETUP_EXTENSION

        installed[SETUP_EXTENSION] = install_setup(runtime, root=hub_root, audit=audit)
    return installed


def _publish_connection(service_config: Any, admin_token: str, audit: AuditLog) -> None:
    """Tell hubctl and the desktop app where the Hub listens and this run's admin token."""
    try:
        write_connection_file(service_config, admin_token)
    except OSError as exc:
        audit.write("connection_file.failed", level=logging.WARNING, error_type=type(exc).__name__)


def _load_settings() -> Settings:
    """The Hub config's `settings` block; a bad value stops the start and is logged like a bad config."""
    try:
        return load_settings()
    except Exception as exc:
        audit = AuditLog()
        audit.write("config.load.failed", level=logging.ERROR, error_type=type(exc).__name__, error=str(exc))
        audit.close()
        raise


def _load_hub_config(audit: AuditLog) -> tuple[HubConfig, dict[str, Playbook]]:
    """The Hub config and the playbooks; either one invalid stops the start and is logged."""
    try:
        return load_config(), load_playbooks()
    except Exception as exc:
        audit.write("config.load.failed", level=logging.ERROR, error_type=type(exc).__name__, error=str(exc))
        raise


def _open_job_store(retention_days: int) -> JobStore:
    """Open the job store and settle work left over from a previous run."""
    job_store = JobStore(retention_days=retention_days)
    job_store.mark_running_interrupted()
    job_store.purge_expired()
    return job_store


def _build_http_app(
    runtime: HubRuntime, job_runner: JobRunner, tokens: tuple[str, str], host: str, port: int,
    shutdown_event: asyncio.Event, extensions: dict[str, Any] | Any = None,
):
    ext_map = extensions if isinstance(extensions, dict) else ({ACTIVITY_UI: extensions} if extensions is not None else {})
    sessions = DashboardSessions(settings=getattr(runtime, "settings", Settings()).dashboard)
    server = create_mcp_server(runtime, job_runner)
    ui = ext_map.get(ACTIVITY_UI)
    if ui is not None:
        ui.attach_server(server)
    dashboard_exts = tuple(
        ext.dashboard_extension() for ext in ext_map.values() if hasattr(ext, "dashboard_extension")
    )
    routes = create_dashboard_routes(
        runtime, job_runner, sessions, shutdown_callback=shutdown_event.set,
        extensions=dashboard_exts,
    )
    token, admin_token = tokens
    middleware = ui.middleware() if ui is not None else ()
    return create_http_app(
        server, token, host=host, port=port, custom_routes=routes, sessions=sessions,
        middleware=middleware, admin_token=admin_token,
    )


async def _serve_until_stopped(
    app, host: str, port: int, shutdown_event: asyncio.Event, job_store: JobStore,
    close_listeners: Callable[[], None] | None = None,
    drain_seconds: float = HttpSettings().shutdown_drain_seconds,
) -> None:
    server = _create_uvicorn_server(app, host=host, port=port, drain_seconds=drain_seconds)
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


def _create_uvicorn_server(
    app, *, host: str, port: int, drain_seconds: float = HttpSettings().shutdown_drain_seconds,
) -> uvicorn.Server:
    return uvicorn.Server(uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        timeout_graceful_shutdown=drain_seconds,
    ))


def main() -> None:
    # Children find a runtime that preflight installed with winget after this user signed in.
    refresh_windows_path()
    asyncio.run(run_http())


if __name__ == "__main__":
    main()
