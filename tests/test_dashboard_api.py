from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace

from mcp.server import Server
from starlette.requests import Request
from starlette.testclient import TestClient


class FakeRuntime:
    config = SimpleNamespace(detected=("sample-child",), servers={"sample-child": object()}, unavailable={})
    clients = {"sample-child": object()}

    def __init__(self):
        self.subscribers = set()

    def activity_snapshot(self):
        return {
            "children": {"sample-child": {"tool_count": 2, "active_calls": 0}},
            "events": [], "sequence": 0, "stream_id": "fake-stream",
        }

    def subscribe_activity(self):
        queue = asyncio.Queue(maxsize=4)
        self.subscribers.add(queue)
        return queue

    def unsubscribe_activity(self, queue):
        self.subscribers.discard(queue)


class FakeJobRunner:
    def __init__(self):
        self.store = FakeActivityStore()

    def agent_status(self):
        return {"coordinator": {"provider": "openai", "model": "example-model", "configured": False}}

    def list_projects(self):
        return []

    def pending_approval_preview(self, job_id):
        if job_id != "pending-job":
            return None
        return {
            "tool_name": "jira__sync_ticket",
            "expires_at": "2026-10-28T00:10:00+00:00",
            "arguments_preview": '{"issue_key":"EX-1","token":"[REDACTED]"}',
        }


class FakeActivityStore:
    def activity_report(self, start, end, **filters):
        return {
            "summary": {"total_calls": 1, "total_duration_ms": 12, "failures": 0},
            "by_child": [{"server_id": "sample-child", "calls": 1, "duration_ms": 12, "failures": 0}],
            "by_tool": [{"tool": "inspect", "calls": 1, "duration_ms": 12, "failures": 0}],
            "by_client": [{"client": "Antigravity", "calls": 1}],
            "events": [{"server_id": "sample-child", "tool": "inspect", "client": "Antigravity", "state": "succeeded"}],
            "filters": {**filters, "start": start.isoformat(), "end": end.isoformat()},
        }


class DashboardAPITest(unittest.TestCase):
    def setUp(self) -> None:
        from mcp_hub.dashboard.api import create_dashboard_routes
        from mcp_hub.dashboard.sessions import DashboardSessions
        from mcp_hub.http import create_http_app

        self.token = "test-dashboard-bearer-token-value"
        self.sessions = DashboardSessions()
        self.jobs = FakeJobRunner()
        self.runtime = FakeRuntime()
        self.routes = create_dashboard_routes(self.runtime, self.jobs, self.sessions, control_launcher=lambda _cmd: None)
        self.app = create_http_app(
            Server("dashboard-test"), self.token, host="127.0.0.1", port=8765,
            custom_routes=self.routes, sessions=self.sessions,
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
        self.assertEqual(status.json()["activity"]["children"]["sample-child"]["tool_count"], 2)
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

    def test_activity_stream_requires_pairing(self) -> None:
        with TestClient(self.app) as client:
            response = client.get(
                "/dashboard/api/activity/stream", headers={"Host": "127.0.0.1:8765"},
            )
        self.assertEqual(response.status_code, 401)

    def test_activity_stream_sends_snapshot_and_live_metadata_event(self) -> None:
        route = next(route for route in self.routes if route.path == "/dashboard/api/activity/stream")
        scope = {
            "type": "http", "method": "GET", "path": route.path, "raw_path": route.path.encode(),
            "query_string": b"", "headers": [], "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 8765), "scheme": "http", "http_version": "1.1",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
        }

        async def exercise():
            response = await route.endpoint(Request(scope))
            chunks = response.body_iterator
            snapshot = await anext(chunks)
            subscriber = next(iter(self.runtime.subscribers))
            await subscriber.put({"resync": True})
            resync = await anext(chunks)
            await subscriber.put({
                "stream_id": "fake-stream", "sequence": 1, "call_id": 4,
                "server_id": "sample-child", "tool": "inspect", "state": "started",
                "at": "2026-10-29T00:00:00+00:00", "client": "Codex", "agent_role": None,
            })
            event = await anext(chunks)
            await chunks.aclose()
            return snapshot, resync, event

        snapshot, resync, event = asyncio.run(exercise())
        snapshot_text = snapshot.decode() if isinstance(snapshot, bytes) else snapshot
        resync_text = resync.decode() if isinstance(resync, bytes) else resync
        event_text = event.decode() if isinstance(event, bytes) else event
        self.assertIn("event: snapshot", snapshot_text)
        self.assertIn('"stream_id":"fake-stream"', snapshot_text)
        self.assertIn("event: snapshot", resync_text)
        self.assertIn("event: activity", event_text)
        self.assertIn('"call_id":4', event_text)
        self.assertNotIn("arguments", event_text)
        self.assertFalse(self.runtime.subscribers)

    def test_daily_activity_api_requires_pairing_and_applies_filters(self) -> None:
        with TestClient(self.app, follow_redirects=False) as client:
            denied = client.get(
                "/dashboard/api/activity?date=2026-10-28&server_id=sample-child&client=Antigravity",
                headers={"Host": "127.0.0.1:8765"},
            )
            pair = client.post(
                "/dashboard/pair",
                headers={"Host": "127.0.0.1:8765", "Authorization": f"Bearer {self.token}"},
            ).json()["pairing_token"]
            client.get(f"/dashboard/?pair={pair}", headers={"Host": "127.0.0.1:8765"})
            response = client.get(
                "/dashboard/api/activity?date=2026-10-28&server_id=sample-child&client=Antigravity",
                headers={"Host": "127.0.0.1:8765"},
            )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["total_calls"], 1)
        self.assertEqual(response.json()["events"][0]["client"], "Antigravity")
        self.assertEqual(response.json()["filters"]["server_id"], "sample-child")
        start = datetime.fromisoformat(response.json()["filters"]["start"])
        end = datetime.fromisoformat(response.json()["filters"]["end"])
        self.assertEqual((end - start).total_seconds(), 24 * 60 * 60)
        self.assertNotIn(self.token, response.text)

    def test_daily_activity_rejects_invalid_date(self) -> None:
        with TestClient(self.app) as client:
            pair = client.post(
                "/dashboard/pair",
                headers={"Host": "127.0.0.1:8765", "Authorization": f"Bearer {self.token}"},
            ).json()["pairing_token"]
            client.get(f"/dashboard/?pair={pair}", headers={"Host": "127.0.0.1:8765"})
            response = client.get(
                "/dashboard/api/activity?date=not-a-date", headers={"Host": "127.0.0.1:8765"},
            )
        self.assertEqual(response.status_code, 400)

    def test_daily_activity_rejects_an_unbounded_offset(self) -> None:
        with TestClient(self.app) as client:
            pair = client.post(
                "/dashboard/pair",
                headers={"Host": "127.0.0.1:8765", "Authorization": f"Bearer {self.token}"},
            ).json()["pairing_token"]
            client.get(f"/dashboard/?pair={pair}", headers={"Host": "127.0.0.1:8765"})
            response = client.get(
                "/dashboard/api/activity?date=2026-10-28&offset=9223372036854775808",
                headers={"Host": "127.0.0.1:8765"},
            )
        self.assertEqual(response.status_code, 400)

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
        self.assertEqual(response.json()["tool_name"], "jira__sync_ticket")
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
