"""BitNet install state and the install route, always present so the desktop app can offer the install.

`service.py` installs this part even when the BitNet extension is off: it adds one route and the `bitnet`
status block, and never touches a tool call. The install itself runs on a worker thread (installer.py).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .. import hub_settings
from ..audit import AuditLog
from ..extensions import BITNET, DashboardExtension
from ..settings import BitnetSettings
from .installer import Installer, InstallError
from .sidecar import Sidecar, _read_install

INSTALL_ROUTE = "/dashboard/api/bitnet/install"
SETUP_EXTENSION = "bitnet-setup"


class BitnetSetup:
    """Whether BitNet is installed, the running install's step and percent, and the last install error."""

    def __init__(
        self,
        root: Path,
        settings: BitnetSettings,
        audit: AuditLog | None,
        *,
        sidecar: Sidecar | None = None,
        installer_factory: Callable[[], Installer] | None = None,
        enable: Callable[[str, bool], None] = hub_settings.set_extension,
    ) -> None:
        self.root, self.settings, self.audit, self.sidecar = root, settings, audit, sidecar
        self._new_installer = installer_factory or (lambda: Installer(root, settings))
        self._enable = enable
        self.installed = _read_install(root) is not None
        self.step: str | None = None
        self.progress: int | None = None
        self.error: str | None = None
        self._installer: Installer | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def state(self) -> str:
        if self.running:
            return "installing"
        if self.sidecar is not None:
            return self.sidecar.state
        return "off" if self.installed else "not_installed"

    def status_fields(self) -> dict[str, Any]:
        enabled = self.sidecar is not None
        return {"bitnet": {
            "installed": self.installed,
            "enabled": enabled,
            "state": self.state(),
            "restarts": getattr(self.sidecar, "_restarts", 0) if enabled else 0,
            # BitNet's planner and condenser join the Hub only at start: an install without them needs a restart.
            "restart_needed": self.installed and not enabled,
            "install": {"running": self.running, "step": self.step, "progress": self.progress, "error": self.error},
        }}

    def start_install(self) -> bool:
        """Start an install unless one runs or BitNet is already installed; return whether one started."""
        if self.running or self.installed:
            return False
        self.step, self.progress, self.error = "prepare", None, None
        self._installer = self._new_installer()
        self._task = asyncio.create_task(self._install(self._installer), name="mcp-hub-bitnet-install")
        return True

    async def _install(self, installer: Installer) -> None:
        self._write("bitnet.install.started")
        try:
            await asyncio.to_thread(installer.run, self._report)
        except InstallError as exc:
            self.error = exc.code
            self._write("bitnet.install.failed", level=logging.WARNING, error_type=exc.code)
            return
        except Exception as exc:  # never leave the install looking busy
            self.error = "unexpected"
            self._write("bitnet.install.failed", level=logging.WARNING, error_type=type(exc).__name__)
            return
        finally:
            self.step, self.progress = None, None
        self.installed = True
        await self._after_install()

    async def _after_install(self) -> None:
        try:
            await asyncio.to_thread(self._enable, BITNET, True)
        except Exception as exc:  # the user can still switch BitNet on in Settings
            self._write("bitnet.install.enable_failed", level=logging.WARNING, error_type=type(exc).__name__)
        self._write("bitnet.install.finished")
        if self.sidecar is not None:
            await self.sidecar.restart()

    def _report(self, step: str, progress: int | None) -> None:
        """Called on the worker thread: plain attribute writes, and one audit line per new step."""
        if step != self.step:
            self._write("bitnet.install.step", step=step)
        self.step, self.progress = step, progress

    def _write(self, event: str, level: int = logging.INFO, **fields: Any) -> None:
        if self.audit is not None:
            self.audit.write(event, level=level, **fields)

    async def install_route(self, _request: Request) -> Response:
        if self.installed and not self.running:
            return JSONResponse({"error": "BitNet is already installed.", **self.status_fields()}, status_code=409)
        started = self.start_install()
        return JSONResponse({"started": started, **self.status_fields()}, status_code=202)

    def routes(self) -> tuple[Route, ...]:
        return (Route(INSTALL_ROUTE, self.install_route, methods=["POST"]),)

    def dashboard_extension(self) -> DashboardExtension:
        return DashboardExtension(name=SETUP_EXTENSION, routes=self.routes(), status_fields=self.status_fields)

    async def close(self) -> None:
        """Stop a running install at the next chunk; partial files are removed on the next try."""
        if self._installer is not None:
            self._installer.cancel.set()
        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass
