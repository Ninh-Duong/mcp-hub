from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from starlette.applications import Starlette
from starlette.testclient import TestClient

from mcp_hub.bitnet.installer import InstallError
from mcp_hub.bitnet.setup import INSTALL_ROUTE, BitnetSetup
from mcp_hub.settings import BitnetSettings


def write_install(root: Path) -> None:
    base = root / "vendor" / "bitnet"
    (base / "runtime").mkdir(parents=True, exist_ok=True)
    (base / "runtime" / "llama-server.exe").write_bytes(b"x")
    (base / "model.gguf").write_bytes(b"x")
    (base / "install.json").write_text(json.dumps({
        "server": "vendor/bitnet/runtime/llama-server.exe", "model": "vendor/bitnet/model.gguf",
    }), encoding="utf-8")


class FakeInstaller:
    def __init__(self, root: Path, *, error: str | None = None, gate: threading.Event | None = None) -> None:
        self.root, self.error, self.gate = root, error, gate
        self.cancel = threading.Event()

    def run(self, progress) -> Path:
        progress("model", 42)
        if self.gate is not None:
            self.gate.wait(5)
        if self.error:
            raise InstallError(self.error)
        write_install(self.root)
        return self.root / "vendor" / "bitnet" / "install.json"


class BitnetSetupTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.audit = MagicMock()
        self.enable = MagicMock()

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    def setup_with(self, installer: FakeInstaller, sidecar=None) -> BitnetSetup:
        return BitnetSetup(self.root, BitnetSettings(), self.audit, sidecar=sidecar,
                           installer_factory=lambda: installer, enable=self.enable)

    def events(self) -> list[str]:
        return [call.args[0] for call in self.audit.write.call_args_list]

    async def test_not_installed_is_reported_even_with_bitnet_off(self) -> None:
        status = self.setup_with(FakeInstaller(self.root)).status_fields()["bitnet"]
        self.assertEqual((status["installed"], status["enabled"], status["state"]), (False, False, "not_installed"))
        self.assertFalse(status["restart_needed"])

    async def test_an_install_reports_progress_then_asks_for_a_restart(self) -> None:
        gate = threading.Event()
        setup = self.setup_with(FakeInstaller(self.root, gate=gate))
        self.assertTrue(setup.start_install())
        self.assertFalse(setup.start_install())  # one install at a time
        for _ in range(100):
            if setup.progress == 42:
                break
            await asyncio.sleep(0.01)
        status = setup.status_fields()["bitnet"]
        self.assertEqual((status["state"], status["install"]["step"], status["install"]["progress"]),
                         ("installing", "model", 42))
        gate.set()
        await asyncio.wait_for(setup._task, 5)
        status = setup.status_fields()["bitnet"]
        self.assertEqual((status["installed"], status["state"], status["restart_needed"]), (True, "off", True))
        self.enable.assert_called_once_with("bitnet", True)
        self.assertEqual(self.events(), ["bitnet.install.started", "bitnet.install.step", "bitnet.install.finished"])

    async def test_a_failed_install_keeps_its_error_for_the_app(self) -> None:
        setup = self.setup_with(FakeInstaller(self.root, error="network"))
        setup.start_install()
        await asyncio.wait_for(setup._task, 5)
        status = setup.status_fields()["bitnet"]
        self.assertEqual((status["installed"], status["state"], status["install"]["error"]),
                         (False, "not_installed", "network"))
        self.enable.assert_not_called()
        self.assertIn("bitnet.install.failed", self.events())
        self.assertTrue(setup.start_install())  # Retry
        await asyncio.wait_for(setup._task, 5)

    async def test_with_bitnet_on_the_sidecar_starts_without_a_hub_restart(self) -> None:
        sidecar = MagicMock(state="unavailable", _restarts=0, restart=AsyncMock())
        setup = self.setup_with(FakeInstaller(self.root), sidecar=sidecar)
        setup.start_install()
        await asyncio.wait_for(setup._task, 5)
        sidecar.restart.assert_awaited_once()
        status = setup.status_fields()["bitnet"]
        self.assertEqual((status["enabled"], status["restart_needed"]), (True, False))

    async def test_close_cancels_a_running_install(self) -> None:
        installer = FakeInstaller(self.root, gate=threading.Event())
        setup = self.setup_with(installer)
        setup.start_install()
        await asyncio.sleep(0)
        installer.gate.set()
        await setup.close()
        self.assertTrue(installer.cancel.is_set())

    async def test_an_unexpected_error_never_leaves_the_install_busy(self) -> None:
        installer = FakeInstaller(self.root)
        installer.run = MagicMock(side_effect=RuntimeError("boom"))
        setup = self.setup_with(installer)
        setup.start_install()
        await asyncio.wait_for(setup._task, 5)
        self.assertEqual(setup.status_fields()["bitnet"]["install"]["error"], "unexpected")
        self.assertFalse(setup.running)


class InstallRouteTest(unittest.TestCase):
    def test_the_route_starts_once_and_refuses_when_installed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            setup = BitnetSetup(root, BitnetSettings(), None, enable=MagicMock(),
                                installer_factory=lambda: FakeInstaller(root, error="release"))
            with TestClient(Starlette(routes=list(setup.routes()))) as client:
                answer = client.post(INSTALL_ROUTE)
                self.assertEqual(answer.status_code, 202)
                self.assertTrue(answer.json()["started"])
            write_install(root)
            setup.installed = True
            with TestClient(Starlette(routes=list(setup.routes()))) as client:
                self.assertEqual(client.post(INSTALL_ROUTE).status_code, 409)
            self.assertEqual(setup.dashboard_extension().name, "bitnet-setup")


if __name__ == "__main__":
    unittest.main()
