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


if __name__ == "__main__":
    unittest.main()
