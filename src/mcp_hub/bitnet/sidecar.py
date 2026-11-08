from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..audit import AuditLog
from ..settings import BitnetSettings
from .client import BitnetClient


def _read_install(root: Path) -> tuple[Path, Path] | None:
    path = root / "vendor" / "bitnet" / "install.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        server = root / data["server"]
        model = root / data["model"]
        if server.is_file() and model.is_file():
            return server, model
    except Exception:
        return None
    return None


def _spawn_process(
    popen: Callable[..., subprocess.Popen],
    cmd: list[str],
    log_path: Path,
) -> tuple[subprocess.Popen, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    kwargs: dict[str, Any] = {"creationflags": flags} if flags else {}
    proc = popen(cmd, stdout=subprocess.DEVNULL, stderr=log_file, **kwargs)
    return proc, log_file


class Sidecar:
    """Supervises the local llama-server process."""

    def __init__(
        self,
        root: Path,
        settings: BitnetSettings,
        audit: AuditLog,
        *,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        client: BitnetClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        log_dir: Path | None = None,
    ) -> None:
        self.root = root
        self.settings = settings
        self.audit = audit
        self._popen = popen
        self.client = client or BitnetClient(settings.port)
        self._sleep = sleep
        self.log_dir = log_dir or (root / "logs" / "children")
        self.state: str = "off"
        self._restarts: int = 0
        self._process: subprocess.Popen | None = None
        self._log_file: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stopped: bool = False

    def status_fields(self) -> dict[str, Any]:
        return {"bitnet": {"state": self.state, "restarts": self._restarts}}

    def _set_state(self, new_state: str, error_type: str | None = None) -> None:
        if self.state == new_state and not error_type:
            return
        self.state = new_state
        fields: dict[str, Any] = {"state": new_state, "restarts": self._restarts}
        if error_type:
            fields["error_type"] = error_type
        if self.audit:
            self.audit.write("bitnet.sidecar.state", **fields)

    def _build_cmd(self, server: Path, model: Path) -> list[str]:
        threads = self.settings.threads
        if threads <= 0:
            threads = max(1, (os.cpu_count() or 2) // 2)
        return [
            str(server),
            "-m",
            str(model),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.settings.port),
            "-t",
            str(threads),
            "-c",
            "4096",
        ]

    async def _wait_healthy(self, timeout: float = 60.0) -> bool:
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if self._stopped:
                return False
            if self._process is not None and self._process.poll() is not None:
                return False
            if await self.client.healthy(timeout=0.5):
                return True
            await self._sleep(0.5)
        return False

    async def _terminate_process(self) -> None:
        proc = self._process
        self._process = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                for _ in range(50):
                    if proc.poll() is not None:
                        break
                    await self._sleep(0.1)
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    async def _try_spawn(self, cmd: list[str]) -> bool:
        try:
            log_path = self.log_dir / "bitnet.log"
            proc, log_file = await asyncio.to_thread(_spawn_process, self._popen, cmd, log_path)
            self._process, self._log_file = proc, log_file
            return True
        except Exception as exc:
            self._set_state("unavailable", error_type=type(exc).__name__)
            return False

    async def _watch_loop(self, server: Path, model: Path) -> None:
        consecutive_failures = 0
        cmd = self._build_cmd(server, model)
        while not self._stopped:
            await self._sleep(5.0)
            if self._stopped:
                break
            exited = self._process is None or self._process.poll() is not None
            if not exited:
                healthy = await self.client.healthy(timeout=1.0)
                consecutive_failures = 0 if healthy else (consecutive_failures + 1)
            if exited or consecutive_failures >= 3:
                if self._restarts < self.settings.max_restarts:
                    self._restarts += 1
                    self._set_state("restarting")
                    await self._terminate_process()
                    if not await self._try_spawn(cmd):
                        break
                    if await self._wait_healthy(60.0):
                        self._set_state("ready")
                        consecutive_failures = 0
                else:
                    self._set_state("unavailable", error_type="ExceededMaxRestarts")
                    await self._terminate_process()
                    break

    async def _run(self) -> None:
        paths = await asyncio.to_thread(_read_install, self.root)
        if paths is None:
            self._set_state("unavailable", error_type="NotInstalled")
            return
        server, model = paths
        self._set_state("starting")
        cmd = self._build_cmd(server, model)
        if not await self._try_spawn(cmd):
            return
        if not await self._wait_healthy(60.0):
            self._set_state("unavailable", error_type="StartupFailed")
            await self._terminate_process()
            return
        self._set_state("ready")
        await self._watch_loop(server, model)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._terminate_process()
        if self.client:
            await self.client.aclose()
        self._set_state("stopped")
