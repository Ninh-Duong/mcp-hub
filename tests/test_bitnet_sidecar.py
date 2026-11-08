from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from mcp_hub.bitnet.sidecar import Sidecar, _read_install, _spawn_process
from mcp_hub.settings import BitnetSettings


class FakeProcess:
    def __init__(self, exit_code: int | None = None) -> None:
        self._poll = exit_code
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self._poll

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self._poll = -9


class FakeClient:
    def __init__(self, health_results: list[bool] | None = None) -> None:
        self._results = list(health_results or [True])
        self.closed = False

    async def healthy(self, timeout: float = 1.0) -> bool:
        if self._results:
            return self._results.pop(0)
        return True

    async def aclose(self) -> None:
        self.closed = True


async def wait_for_state(
    sidecar: Sidecar, expected_states: set[str], timeout: float = 2.0,
) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if sidecar.state in expected_states:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"Timed out waiting for {expected_states}, state: {sidecar.state}")


class TestBitnetSidecar(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        install_dir = self.root / "vendor" / "bitnet"
        install_dir.mkdir(parents=True)
        server_bin = install_dir / "llama-server.exe"
        server_bin.write_text("bin", encoding="utf-8")
        model_file = install_dir / "model.gguf"
        model_file.write_text("model", encoding="utf-8")
        install_json = install_dir / "install.json"
        install_json.write_text(
            json.dumps({
                "server": "vendor/bitnet/llama-server.exe",
                "model": "vendor/bitnet/model.gguf",
                "version": 1,
            }),
            encoding="utf-8",
        )
        self.sidecar: Sidecar | None = None

    async def asyncTearDown(self) -> None:
        if self.sidecar is not None:
            await self.sidecar.stop()
            self.sidecar = None
        self._temp_dir.cleanup()

    def test_read_install_missing_or_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            empty_root = Path(td)
            self.assertIsNone(_read_install(empty_root))
            bad_dir = empty_root / "vendor" / "bitnet"
            bad_dir.mkdir(parents=True)
            (bad_dir / "install.json").write_text("invalid json", encoding="utf-8")
            self.assertIsNone(_read_install(empty_root))

    def test_spawn_process_calls_popen(self) -> None:
        fake_popen = MagicMock(return_value=FakeProcess())
        log_file = self.root / "bitnet.log"
        proc, handle = _spawn_process(fake_popen, ["cmd"], log_file)
        self.assertIsNotNone(proc)
        self.assertFalse(handle.closed)
        handle.close()
        fake_popen.assert_called_once()

    async def test_not_installed_sets_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            audit = MagicMock()
            self.sidecar = Sidecar(Path(td), BitnetSettings(), audit)
            await self.sidecar.start()
            await wait_for_state(self.sidecar, {"unavailable"})
            self.assertEqual(self.sidecar.state, "unavailable")
            self.assertEqual(
                self.sidecar.status_fields(), {"bitnet": {"state": "unavailable", "restarts": 0}},
            )
            audit.write.assert_called_with(
                "bitnet.sidecar.state", state="unavailable", restarts=0, error_type="NotInstalled",
            )

    async def test_healthy_reaches_ready(self) -> None:
        audit = MagicMock()
        client = FakeClient([True])
        fake_proc = FakeProcess()
        fake_popen = MagicMock(return_value=fake_proc)

        async def fake_sleep(_: float) -> None:
            await asyncio.sleep(0.001)

        self.sidecar = Sidecar(
            self.root, BitnetSettings(), audit, popen=fake_popen, client=client, sleep=fake_sleep,
        )
        await self.sidecar.start()
        await wait_for_state(self.sidecar, {"ready"})
        self.assertEqual(self.sidecar.state, "ready")
        self.assertEqual(self.sidecar.status_fields(), {"bitnet": {"state": "ready", "restarts": 0}})
        await self.sidecar.stop()
        self.assertEqual(self.sidecar.state, "stopped")

    async def test_crash_and_restart_to_ready(self) -> None:
        audit = MagicMock()
        settings = BitnetSettings(max_restarts=2)
        proc1 = FakeProcess()
        proc2 = FakeProcess()
        procs = [proc1, proc2]

        def popen_factory(*args: Any, **kwargs: Any) -> FakeProcess:
            return procs.pop(0)

        client = FakeClient([True, True, True])

        async def fake_sleep(_: float) -> None:
            await asyncio.sleep(0.001)

        self.sidecar = Sidecar(
            self.root, settings, audit, popen=popen_factory, client=client, sleep=fake_sleep,
        )
        await self.sidecar.start()
        await wait_for_state(self.sidecar, {"ready"})
        self.assertEqual(self.sidecar.state, "ready")

        proc1._poll = 1
        # Wait for restart
        start_wait = time.monotonic()
        while time.monotonic() - start_wait < 2.0:
            if self.sidecar._restarts == 1 and self.sidecar.state == "ready":
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.sidecar.state, "ready")
        self.assertEqual(self.sidecar._restarts, 1)

    async def test_crash_past_max_restarts_sets_unavailable(self) -> None:
        audit = MagicMock()
        settings = BitnetSettings(max_restarts=1)
        proc1 = FakeProcess()
        proc2 = FakeProcess(exit_code=1)
        procs = [proc1, proc2]

        def popen_factory(*args: Any, **kwargs: Any) -> FakeProcess:
            return procs.pop(0)

        client = FakeClient([True, True, False, False])

        async def fake_sleep(_: float) -> None:
            await asyncio.sleep(0.001)

        self.sidecar = Sidecar(
            self.root, settings, audit, popen=popen_factory, client=client, sleep=fake_sleep,
        )
        await self.sidecar.start()
        await wait_for_state(self.sidecar, {"ready"})
        self.assertEqual(self.sidecar.state, "ready")

        proc1._poll = 1
        await wait_for_state(self.sidecar, {"unavailable"})
        self.assertEqual(self.sidecar.state, "unavailable")
        self.assertEqual(self.sidecar.status_fields()["bitnet"]["restarts"], 1)

    async def test_stop_terminates_and_kills(self) -> None:
        audit = MagicMock()
        client = FakeClient([True])
        fake_proc = FakeProcess()
        fake_popen = MagicMock(return_value=fake_proc)

        async def fake_sleep(_: float) -> None:
            await asyncio.sleep(0.001)

        self.sidecar = Sidecar(
            self.root, BitnetSettings(), audit, popen=fake_popen, client=client, sleep=fake_sleep,
        )
        await self.sidecar.start()
        await wait_for_state(self.sidecar, {"ready"})
        self.assertEqual(self.sidecar.state, "ready")

        await self.sidecar.stop()
        self.assertTrue(fake_proc.terminated)
        self.assertTrue(fake_proc.killed)
        self.assertEqual(self.sidecar.state, "stopped")
        self.assertTrue(client.closed)

    async def test_popen_runs_off_event_loop(self) -> None:
        audit = MagicMock()
        client = FakeClient([True])
        fake_proc = FakeProcess()
        fake_popen = MagicMock(return_value=fake_proc)

        async def fake_sleep(_: float) -> None:
            await asyncio.sleep(0.001)

        with patch("mcp_hub.bitnet.sidecar.asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:
            self.sidecar = Sidecar(
                self.root, BitnetSettings(), audit, popen=fake_popen, client=client, sleep=fake_sleep,
            )
            await self.sidecar.start()
            await wait_for_state(self.sidecar, {"ready"})
            spawn_calls = [
                c for c in mock_to_thread.call_args_list if c.args and c.args[0] == _spawn_process
            ]
            self.assertGreaterEqual(len(spawn_calls), 1)
