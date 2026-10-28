from __future__ import annotations

import unittest
from types import SimpleNamespace

from mcp.server import Server
from starlette.testclient import TestClient


class FakeRuntime:
    config = SimpleNamespace(detected=("sample-child",), servers={"sample-child": object()}, unavailable={})
    clients = {"sample-child": object()}


class FakeJobRunner:
    def agent_status(self):
        return {"coordinator": {"provider": "openai", "model": "example-model", "configured": False}}

    def list_jobs(self, **_kwargs):
        return []

    def list_projects(self):
        return []

    def pending_approval_preview(self, job_id):
        if job_id != "pending-job":
            return None
        return {
            "tool_name": "jira__jira_sync_ticket",
            "expires_at": "2026-10-28T00:10:00+00:00",
            "arguments_preview": '{"issue_key":"EX-1","token":"[REDACTED]"}',
        }


class DashboardAPITest(unittest.TestCase):
    def setUp(self) -> None:
        from mcp_hub.dashboard.api import create_dashboard_routes
        from mcp_hub.dashboard.sessions import DashboardSessions
        from mcp_hub.http import create_http_app

        self.token = "test-dashboard-bearer-token-value"
        self.sessions = DashboardSessions()
        routes = create_dashboard_routes(FakeRuntime(), FakeJobRunner(), self.sessions, control_launcher=lambda _cmd: None)
        self.app = create_http_app(
            Server("dashboard-test"), self.token, host="127.0.0.1", port=8765,
            custom_routes=routes, sessions=self.sessions,
        )

    def test_dashboard_requires_pairing_and_then_uses_http_only_session(self) -> None:
        with TestClient(self.app, follow_redirects=False) as client:
            denied = client.get("/dashboard/", headers={"Host": "127.0.0.1:8765"})
            self.assertEqual(denied.status_code, 401)

            pair_response = client.post(
                "/dashboard/pair",
                headers={"Host": "127.0.0.1:8765", "Authorization": f"Bearer {self.token}"},
            )
            pairing_token = pair_response.json()["pairing_token"]
            paired = client.get(
                f"/dashboard/?pair={pairing_token}",
                headers={"Host": "127.0.0.1:8765"},
            )
            self.assertEqual(paired.status_code, 303)
            self.assertIn("hub_session", paired.headers["set-cookie"])
            self.assertNotIn(self.token, paired.headers["set-cookie"])

            dashboard = client.get("/dashboard/", headers={"Host": "127.0.0.1:8765"})
            status = client.get("/dashboard/api/status", headers={"Host": "127.0.0.1:8765"})

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("mcp-hub", dashboard.text)
        self.assertEqual(dashboard.headers.get("referrer-policy"), "no-referrer")
        self.assertIn("frame-ancestors 'none'", dashboard.headers.get("content-security-policy", ""))
        self.assertEqual(status.json()["children"]["sample-child"], "connected")
        self.assertNotIn(self.token, status.text)

    def test_dashboard_rejects_cross_origin_requests(self) -> None:
        with TestClient(self.app) as client:
            pair = client.post(
                "/dashboard/pair",
                headers={"Host": "127.0.0.1:8765", "Authorization": f"Bearer {self.token}"},
            ).json()["pairing_token"]
            client.get(f"/dashboard/?pair={pair}", headers={"Host": "127.0.0.1:8765"})
            response = client.get(
                "/dashboard/api/status",
                headers={"Host": "127.0.0.1:8765", "Origin": "http://attacker.example"},
            )
        self.assertEqual(response.status_code, 403)

    def test_dashboard_exposes_redacted_pending_write_preview(self) -> None:
        with TestClient(self.app) as client:
            pair = client.post(
                "/dashboard/pair",
                headers={"Host": "127.0.0.1:8765", "Authorization": f"Bearer {self.token}"},
            ).json()["pairing_token"]
            client.get(f"/dashboard/?pair={pair}", headers={"Host": "127.0.0.1:8765"})
            response = client.get(
                "/dashboard/api/jobs/pending-job/pending-call",
                headers={"Host": "127.0.0.1:8765"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tool_name"], "jira__jira_sync_ticket")
        self.assertIn("[REDACTED]", response.json()["arguments_preview"])
        self.assertNotIn(self.token, response.text)

    def test_authenticated_shutdown_requests_cooperative_service_exit(self) -> None:
        from mcp_hub.dashboard.api import create_dashboard_routes

        shutdown_requests = []
        routes = create_dashboard_routes(
            FakeRuntime(), FakeJobRunner(), self.sessions,
            shutdown_callback=lambda: shutdown_requests.append(True),
        )
        from mcp_hub.http import create_http_app
        app = create_http_app(
            Server("shutdown-test"), self.token, custom_routes=routes, sessions=self.sessions,
        )
        with TestClient(app) as client:
            denied = client.post("/dashboard/api/service/shutdown", headers={"Host": "127.0.0.1:8765"})
            accepted = client.post(
                "/dashboard/api/service/shutdown",
                headers={"Host": "127.0.0.1:8765", "Authorization": f"Bearer {self.token}"},
            )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json(), {"accepted": True})
        self.assertEqual(shutdown_requests, [True])


if __name__ == "__main__":
    unittest.main()
