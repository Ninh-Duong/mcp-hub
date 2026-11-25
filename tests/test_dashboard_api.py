from __future__ import annotations

from tests.helpers.fakes import FakeRuntime
import unittest
from types import SimpleNamespace

from mcp.server import Server
from starlette.testclient import TestClient


CLIENT_TOKEN = "test-client-bearer-token-value-0000"




class FakeActivityStore:
    def activity_report(self, start, end, **filters):
        return {"summary": {"total_calls": 0}, "filters": {**filters, "start": start.isoformat(), "end": end.isoformat()}}


class DashboardAPITest(unittest.TestCase):
    """The Hub API used by the desktop app, without browser routes."""

    def setUp(self) -> None:
        from mcp_hub.activity_ui.routes import ActivityRoutes
        from mcp_hub.dashboard.api import create_dashboard_routes
        from mcp_hub.extensions import ACTIVITY_UI, DashboardExtension
        from mcp_hub.http import create_http_app

        self.token = "test-dashboard-bearer-token-value"
        self.runtime = FakeRuntime()
        self.jobs = SimpleNamespace(store=FakeActivityStore())
        self.launched = []
        tracker = SimpleNamespace(subscribe=lambda: None, unsubscribe=lambda _queue: None, subscribers=set())
        extension = DashboardExtension(
            name=ACTIVITY_UI,
            routes=ActivityRoutes(self.runtime, tracker, self.jobs.store).routes(),
            status_fields=lambda: {"activity": self.runtime.activity_snapshot(), "clients": []},
            hubctl_flags=("-Ui",),
        )
        routes = create_dashboard_routes(self.runtime, control_launcher=self.launched.append, extensions=(extension,))
        self.app = create_http_app(
            Server("dashboard-test"), CLIENT_TOKEN, host="127.0.0.1", port=8765,
            admin_token=self.token, custom_routes=routes,
        )
        self.headers = {"Host": "127.0.0.1:8765", "Authorization": f"Bearer {self.token}"}

    def test_website_and_job_review_routes_are_not_found(self) -> None:
        with TestClient(self.app) as client:
            responses = [
                client.get("/dashboard/", headers=self.headers),
                client.get("/dashboard/app.js", headers=self.headers),
                client.post("/dashboard/pair", headers=self.headers),
                client.get("/dashboard/api/jobs", headers=self.headers),
                client.post("/dashboard/api/jobs/pending-job/approve", headers=self.headers),
            ]
        self.assertEqual([response.status_code for response in responses], [404] * 5)

    def test_desktop_status_and_activity_apis_remain_admin_authenticated(self) -> None:
        with TestClient(self.app) as client:
            denied = client.get("/dashboard/api/status", headers={"Host": "127.0.0.1:8765"})
            status = client.get("/dashboard/api/status", headers=self.headers)
            activity = client.get("/dashboard/api/activity?date=2026-10-28", headers=self.headers)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["children"]["sample-child"], "connected")
        self.assertEqual(status.json()["activity"]["stream_id"], "fake-stream")
        self.assertNotIn("agents", status.json())
        self.assertNotIn("projects", status.json())
        self.assertEqual(activity.status_code, 200)

    def test_desktop_control_keeps_extension_restart_flag(self) -> None:
        with TestClient(self.app) as client:
            restarted = client.post("/dashboard/api/control/restart", headers=self.headers)
            stopped = client.post("/dashboard/api/control/stop", headers=self.headers)
        self.assertEqual(restarted.json(), {"accepted": True, "command": "restart"})
        self.assertEqual(stopped.json(), {"accepted": True, "command": "stop"})
        self.assertEqual(self.launched, [["restart", "-Ui"], ["stop"]])

    def test_desktop_api_responses_are_not_cached(self) -> None:
        with TestClient(self.app) as client:
            status = client.get("/dashboard/api/status", headers=self.headers)
        self.assertEqual(status.headers["cache-control"], "no-store")

    def test_pending_approval_routes_require_admin_token_and_forward_decisions(self) -> None:
        from mcp_hub.dashboard.api import create_dashboard_routes
        from mcp_hub.http import create_http_app

        decisions: list[tuple[str, bool]] = []

        class _FakeJobs:
            async def list_pending_approvals(self):
                return [{"job_id": "job-1", "tool_name": "jira__sync_ticket", "agent": "jira",
                         "expires_at": "2026-11-26T01:00:00+00:00", "argument_keys": ["force", "issue_key"]}]

            async def resolve_pending_approval(self, job_id: str, *, approve: bool):
                decisions.append((job_id, approve))
                return {"job_id": job_id, "action": "approve" if approve else "reject", "applied": True}

        routes = create_dashboard_routes(self.runtime, job_runner=_FakeJobs())
        app = create_http_app(
            Server("approval-api"), CLIENT_TOKEN, host="127.0.0.1", port=8765,
            admin_token=self.token, custom_routes=routes,
        )
        client_headers = {"Host": "127.0.0.1:8765", "Authorization": f"Bearer {CLIENT_TOKEN}"}
        with TestClient(app) as client:
            self.assertEqual(client.get("/dashboard/api/jobs/pending", headers=client_headers).status_code, 403)
            self.assertEqual(
                client.post("/dashboard/api/jobs/job-1/decision", headers=client_headers, json={"action": "approve"}).status_code,
                403,
            )
            listed = client.get("/dashboard/api/jobs/pending", headers=self.headers)
            status = client.get("/dashboard/api/status", headers=self.headers)
            bad = client.post("/dashboard/api/jobs/job-1/decision", headers=self.headers, json={"action": "maybe"})
            ok = client.post("/dashboard/api/jobs/job-1/decision", headers=self.headers, json={"action": "approve"})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["pending"][0]["job_id"], "job-1")
        self.assertEqual(status.json()["pending_approvals"][0]["tool_name"], "jira__sync_ticket")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(ok.json(), {"job_id": "job-1", "action": "approve", "applied": True})
        self.assertEqual(decisions, [("job-1", True)])

    def test_accept_pins_route_requires_admin_token_and_forwards_server_id(self) -> None:
        from mcp_hub.dashboard.api import create_dashboard_routes
        from mcp_hub.http import create_http_app

        calls: list[str | None] = []
        self.runtime.accept_pins = lambda sid=None: (calls.append(sid) or [{"server": sid or "all", "tool": "read"}])
        routes = create_dashboard_routes(self.runtime)
        app = create_http_app(
            Server("pins-api"), CLIENT_TOKEN, host="127.0.0.1", port=8765,
            admin_token=self.token, custom_routes=routes,
        )
        client_headers = {"Host": "127.0.0.1:8765", "Authorization": f"Bearer {CLIENT_TOKEN}"}
        with TestClient(app) as client:
            self.assertEqual(client.post("/dashboard/api/security/pins/accept", headers=client_headers).status_code, 403)
            bad = client.post("/dashboard/api/security/pins/accept", headers=self.headers, json={"server_id": ""})
            ok_all = client.post("/dashboard/api/security/pins/accept", headers=self.headers, json={})
            ok_one = client.post("/dashboard/api/security/pins/accept", headers=self.headers, json={"server_id": "child"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(ok_all.json(), {"accepted": [{"server": "all", "tool": "read"}]})
        self.assertEqual(ok_one.json(), {"accepted": [{"server": "child", "tool": "read"}]})
        self.assertEqual(calls, [None, "child"])
