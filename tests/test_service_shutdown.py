from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from starlette.applications import Starlette


class ServiceShutdownTest(unittest.TestCase):
    def test_authenticated_shutdown_event_stops_uvicorn_lifecycle(self) -> None:
        from mcp_hub.service import _stop_when_requested

        async def exercise() -> bool:
            event = asyncio.Event()
            server = SimpleNamespace(should_exit=False)
            task = asyncio.create_task(_stop_when_requested(server, event))
            event.set()
            await task
            return server.should_exit

        self.assertTrue(asyncio.run(exercise()))

    def test_uvicorn_drain_deadline_is_bounded_for_hubctl_restart(self) -> None:
        from mcp_hub.service import _create_uvicorn_server

        server = _create_uvicorn_server(Starlette(), host="127.0.0.1", port=8765)
        self.assertEqual(server.config.timeout_graceful_shutdown, 20)


class ServiceExtensionLifecycleTest(unittest.TestCase):
    def test_activity_ui_starts_before_serving_and_flushes_after_the_job_runner_closes(self) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        from mcp_hub import service

        order = MagicMock()
        runtime = SimpleNamespace(audit=MagicMock(), config=SimpleNamespace(servers={}), public_tools=[])
        jobs = SimpleNamespace(tool_definitions=lambda: [], close=AsyncMock(side_effect=lambda: order.runner_close()))
        ui = SimpleNamespace(
            start=order.ui_start, close=AsyncMock(side_effect=lambda: order.ui_close()), close_listeners=order.listeners,
        )

        async def serve(*args):
            order.serve(args[-1])

        with patch.object(service, "_build_http_app", return_value=object()), \
                patch.object(service, "_serve_until_stopped", serve):
            asyncio.run(service._serve(runtime, jobs, ui, "127.0.0.1", 8765, "token", object()))
        self.assertEqual(
            [name for name, *_ in order.mock_calls], ["ui_start", "serve", "runner_close", "ui_close"],
        )
        self.assertIs(order.serve.call_args.args[0], order.listeners)
        started = runtime.audit.write.call_args_list[0]
        self.assertEqual(started.kwargs["extensions"], ["activity-ui"])


class ServiceMainTest(unittest.TestCase):
    def test_main_refreshes_path_before_serving(self) -> None:
        from unittest.mock import MagicMock, patch

        from mcp_hub import service

        order = MagicMock()
        with (
            patch.object(service, "refresh_windows_path", order.refresh),
            patch.object(service, "run_http", order.run_http),
            patch.object(service.asyncio, "run", order.asyncio_run),
        ):
            service.main()
        self.assertEqual([call[0] for call in order.mock_calls[:1]], ["refresh"])
        order.asyncio_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
