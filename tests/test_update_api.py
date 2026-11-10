"""Authenticated update API behavior."""
from __future__ import annotations

import json
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.server import Server
from starlette.testclient import TestClient

from mcp_hub.dashboard.api import create_dashboard_routes
from mcp_hub.dashboard.sessions import DashboardSessions
from mcp_hub.dashboard.update_api import UpdateHandlers
from mcp_hub.http import create_http_app
from tests.test_dashboard_api import FakeJobRunner, FakeRuntime

TOKEN = "test-update-admin-token-value-000000000"
HEADERS = {"Host": "127.0.0.1:8765", "Authorization": f"Bearer {TOKEN}"}


class UpdateApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.launched: list[list[str]] = []
        handlers = UpdateHandlers(self.launched.append)
        routes = create_dashboard_routes(FakeRuntime(), FakeJobRunner(), DashboardSessions(), updates=handlers)
        self.client = TestClient(create_http_app(
            Server("update-test"), "test-client-bearer-token-value-0000", custom_routes=routes,
            sessions=DashboardSessions(), admin_token=TOKEN,
        )).__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_update_routes_require_admin_token(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch(
            "mcp_hub.update_state.state_path", return_value=Path(folder) / "update.json",
        ):
            self.assertEqual(self.client.get("/dashboard/api/update", headers={"Host": "127.0.0.1:8765"}).status_code, 401)
            self.assertEqual(self.client.post("/dashboard/api/update", headers={"Host": "127.0.0.1:8765"}).status_code, 401)

    def test_get_returns_status_and_start_returns_same_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch("mcp_hub.update_state.state_path", return_value=Path(folder) / "u.json"):
            before = self.client.get("/dashboard/api/update", headers=HEADERS)
            self.assertEqual(before.json()["state"], "idle")
            response = self.client.post("/dashboard/api/update", headers=HEADERS)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["state"], "running")
            self.assertEqual(self.launched[0][0], "update")
            self.assertIn("-RunId", self.launched[0])

    def test_retry_validates_ids_and_launches_only_selected(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch("mcp_hub.update_state.state_path", return_value=Path(folder) / "u.json"):
            from mcp_hub import update_state

            update_state.write({"run_id": "previous", "state": "partial", "components": [
                {"id": "child-a", "kind": "child", "state": "failed", "phase": "pull", "error_code": "git_failed"},
                {"id": "child-b", "kind": "child", "state": "failed", "phase": "pull", "error_code": "git_failed"},
            ]})
            bad = self.client.post("/dashboard/api/update/retry", headers=HEADERS, content=json.dumps({"components": ["../x"]}))
            self.assertEqual(bad.status_code, 400)
            ok = self.client.post("/dashboard/api/update/retry", headers=HEADERS,
                                  content=json.dumps({"components": ["child-b", "child-a"]}))
            self.assertEqual(ok.status_code, 200)
            self.assertIn("child-a,child-b", self.launched[-1])

    def test_launch_failure_is_sanitized(self) -> None:
        handlers = UpdateHandlers(lambda _args: (_ for _ in ()).throw(OSError("private/path")))
        from mcp_hub.dashboard.update_api import update_state

        with tempfile.TemporaryDirectory() as folder, patch.object(update_state, "state_path", return_value=Path(folder) / "u.json"):
            response = TestClient(create_http_app(
                Server("update-failed"), "test-client-bearer-token-value-0000",
                custom_routes=handlers.routes(), admin_token=TOKEN,
            )).post("/dashboard/api/update", headers=HEADERS)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private/path", response.text)
            self.assertEqual(response.json()["events"][-1]["error_code"], "process_start_failed")

    def test_concurrent_start_requests_launch_only_one_process(self) -> None:
        launched: list[list[str]] = []

        def launch(arguments: list[str]) -> None:
            launched.append(arguments)
            time.sleep(0.05)

        handlers = UpdateHandlers(launch)

        async def send_both():
            return await asyncio.gather(handlers.start(None), handlers.start(None))

        with tempfile.TemporaryDirectory() as folder, patch(
            "mcp_hub.update_state.state_path", return_value=Path(folder) / "update.json",
        ):
            responses = asyncio.run(send_both())
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        self.assertEqual(len(launched), 1)


if __name__ == "__main__":
    unittest.main()
