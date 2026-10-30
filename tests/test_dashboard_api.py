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

    def child_states(self):
        return {"sample-child": "connected"}

    def activity_snapshot(self):
        return {
            "children": {"sample-child": {"tool_count": 2, "active_calls": 0}},
            "events": [], "sequence": 0, "stream_id": "fake-stream",
        }


class FakeTracker:
    def __init__(self):
        self.subscribers = set()

    def subscribe(self):
        queue = asyncio.Queue(maxsize=4)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
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
            "arguments_preview": '{\n  "issue_key": "EX-1",\n  "token": "[REDACTED]"\n}',
            "truncated": False,
        }

    async def approve_from_dashboard(self, job_id):
        self.approved = getattr(self, "approved", []) + [job_id]
        return job_id == "pending-job"

    async def cancel_job(self, job_id):
        if job_id != "pending-job":
            raise KeyError(job_id)
        return {"job_id": job_id, "cancelled": True, "status": "cancelled"}


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
    """The dashboard with the activity UI extension installed."""

    def setUp(self) -> None:
        from mcp_hub.activity_ui.routes import ActivityRoutes
        from mcp_hub.dashboard.api import create_dashboard_routes
        from mcp_hub.dashboard.sessions import DashboardSessions
        from mcp_hub.extensions import ACTIVITY_UI, DashboardExtension
        from mcp_hub.http import create_http_app

        self.token = "test-dashboard-bearer-token-value"
        self.sessions = DashboardSessions()
        self.jobs = FakeJobRunner()
        self.runtime = FakeRuntime()
        self.tracker = FakeTracker()
        self.launched = []
        extension = DashboardExtension(
            name=ACTIVITY_UI,
            routes=ActivityRoutes(self.runtime, self.tracker, self.jobs.store).routes(),
            status_fields=lambda: {"activity": self.runtime.activity_snapshot(), "clients": []},
            hubctl_flags=("-Ui",),
        )
        self.routes = create_dashboard_routes(
            self.runtime, self.jobs, self.sessions, control_launcher=self.launched.append, extensions=(extension,),
        )
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
        self.assertEqual(status.json()["extensions"], {"activityUi": True})
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
            subscriber = next(iter(self.tracker.subscribers))
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
        self.assertFalse(self.tracker.subscribers)

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

    def _paired_client(self, client: TestClient) -> None:
        pair = client.post(
            "/dashboard/pair",
            headers={"Host": "127.0.0.1:8765", "Authorization": f"Bearer {self.token}"},
        ).json()["pairing_token"]
        client.get(f"/dashboard/?pair={pair}", headers={"Host": "127.0.0.1:8765"})

    def test_static_assets_are_served_from_allowlist_with_media_types(self) -> None:
        from mcp_hub.dashboard.api import DASHBOARD_ROOT, STATIC_FILES

        with TestClient(self.app) as client:
            denied = client.get("/dashboard/app.js", headers={"Host": "127.0.0.1:8765"})
            self._paired_client(client)
            responses = {
                path: client.get(f"/dashboard/{path}", headers={"Host": "127.0.0.1:8765"})
                for path in ("app.js", "i18n.js", "barn.js", "style.css", "locales/en.json", "locales/vi.json")
            }

        self.assertEqual(denied.status_code, 401)
        for path, response in responses.items():
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("default-src 'self'", response.headers.get("content-security-policy", ""))
        for path in ("app.js", "i18n.js", "barn.js"):
            self.assertTrue(responses[path].headers["content-type"].startswith("application/javascript"))
        self.assertTrue(responses["style.css"].headers["content-type"].startswith("text/css"))
        for path in ("locales/en.json", "locales/vi.json"):
            self.assertTrue(responses[path].headers["content-type"].startswith("application/json"))
            self.assertIsInstance(responses[path].json(), dict)
        published_modules = {path for path in STATIC_FILES if path.endswith(".js")}
        self.assertEqual(published_modules, {path.name for path in DASHBOARD_ROOT.glob("*.js")})

    def test_static_assets_outside_allowlist_return_not_found(self) -> None:
        with TestClient(self.app) as client:
            self._paired_client(client)
            responses = [
                client.get(f"/dashboard/{path}", headers={"Host": "127.0.0.1:8765"})
                for path in (
                    "api.py", "sessions.py", "index.html", "locales/fr.json", "locales/",
                    "missing.js", "..%2Fapi.py", "locales/..%2F..%2Fdashboard%2Fapi.py", "locales/../api.py",
                )
            ]
        for response in responses:
            self.assertEqual(response.status_code, 404, response.url)
            self.assertNotIn("from __future__", response.text)

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

    def test_restart_keeps_the_activity_ui_flag_and_stop_does_not_need_it(self) -> None:
        with TestClient(self.app) as client:
            self._paired_client(client)
            for command in ("restart", "stop"):
                client.post(f"/dashboard/api/control/{command}", headers={"Host": "127.0.0.1:8765"})
        self.assertEqual(self.launched, [["restart", "-Ui"], ["stop"]])


class CoreDashboardTest(unittest.TestCase):
    """The default fast mode: no activity UI extension."""

    def setUp(self) -> None:
        from mcp_hub.dashboard.api import create_dashboard_routes
        from mcp_hub.dashboard.sessions import DashboardSessions
        from mcp_hub.http import create_http_app

        self.token = "test-dashboard-bearer-token-value"
        self.sessions = DashboardSessions()
        self.jobs = FakeJobRunner()
        self.launched = []
        routes = create_dashboard_routes(FakeRuntime(), self.jobs, self.sessions, control_launcher=self.launched.append)
        self.app = create_http_app(Server("core-dashboard"), self.token, custom_routes=routes, sessions=self.sessions)
        self.headers = {"Host": "127.0.0.1:8765", "Authorization": f"Bearer {self.token}"}

    def test_status_reports_the_extension_off_without_activity_or_clients(self) -> None:
        with TestClient(self.app) as client:
            status = client.get("/dashboard/api/status", headers=self.headers).json()
        self.assertEqual(status["extensions"], {"activityUi": False})
        self.assertNotIn("activity", status)
        self.assertNotIn("clients", status)

    def test_activity_routes_are_not_served(self) -> None:
        with TestClient(self.app) as client:
            responses = [
                client.get(path, headers=self.headers)
                for path in ("/dashboard/api/activity?date=2026-10-28", "/dashboard/api/activity/stream")
            ]
        self.assertEqual([response.status_code for response in responses], [404, 404])

    def test_restart_without_the_extension_has_no_flag(self) -> None:
        with TestClient(self.app) as client:
            client.post("/dashboard/api/control/restart", headers=self.headers)
        self.assertEqual(self.launched, [["restart"]])

    def test_control_reports_unavailable_when_powershell_cannot_be_located(self) -> None:
        from unittest.mock import patch

        from pathlib import Path

        from mcp_hub import control

        with patch.dict(control.os.environ, {}, clear=True), self.assertRaises(OSError):
            control.windows_powershell()
        with patch.dict(control.os.environ, {"SystemRoot": "/sysroot"}):
            self.assertEqual(
                Path(control.windows_powershell()).parts[-4:],
                ("System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
            )
        from mcp_hub.dashboard.api import create_dashboard_routes
        from mcp_hub.http import create_http_app

        def failing_launcher(_arguments: list[str]) -> None:
            raise OSError("no SystemRoot")

        routes = create_dashboard_routes(FakeRuntime(), self.jobs, self.sessions, control_launcher=failing_launcher)
        app = create_http_app(Server("core-dashboard"), self.token, custom_routes=routes, sessions=self.sessions)
        with TestClient(app) as client:
            response = client.post("/dashboard/api/control/stop", headers=self.headers)
        self.assertEqual(response.status_code, 503)

    def test_blocking_job_reads_run_off_the_event_loop(self) -> None:
        import asyncio as real_asyncio
        from unittest.mock import patch

        from mcp_hub.dashboard import api as dashboard_api

        offloaded = []
        original = real_asyncio.to_thread

        async def recording_to_thread(function, *args, **kwargs):
            offloaded.append(function.__name__)
            return await original(function, *args, **kwargs)

        self.jobs.list_jobs = lambda **_page: []
        self.jobs.get_job = lambda _job_id: {"job_id": "a"}
        with patch.object(dashboard_api.asyncio, "to_thread", recording_to_thread), TestClient(self.app) as client:
            client.get("/dashboard/api/jobs", headers=self.headers)
            client.get("/dashboard/api/jobs/a", headers=self.headers)
            pair = client.post("/dashboard/pair", headers=self.headers).json()["pairing_token"]
            client.get(f"/dashboard/?pair={pair}", headers={"Host": "127.0.0.1:8765"})
            client.get("/dashboard/api/jobs/pending-job/pending-call", headers={"Host": "127.0.0.1:8765"})
        self.assertEqual(offloaded, ["<lambda>", "<lambda>", "pending_approval_preview"])

    def test_write_approval_routes_refuse_the_bearer_token(self) -> None:
        with TestClient(self.app) as client:
            responses = [
                client.get("/dashboard/api/jobs/pending-job/pending-call", headers=self.headers),
                client.get("/dashboard/api/jobs/pending-job/approval-code", headers=self.headers),
                client.post("/dashboard/api/jobs/pending-job/approve", headers=self.headers),
            ]
            # Other dashboard routes still accept the bearer token (hubctl uses them).
            status = client.get("/dashboard/api/status", headers=self.headers)
        self.assertEqual([response.status_code for response in responses], [403, 403, 403])
        self.assertEqual(status.status_code, 200)

    def test_write_approval_routes_accept_the_paired_session(self) -> None:
        with TestClient(self.app) as client:
            pair = client.post("/dashboard/pair", headers=self.headers).json()["pairing_token"]
            client.get(f"/dashboard/?pair={pair}", headers={"Host": "127.0.0.1:8765"})
            preview = client.get("/dashboard/api/jobs/pending-job/pending-call", headers={"Host": "127.0.0.1:8765"})
        self.assertEqual(preview.status_code, 200)

    def test_paired_dashboard_approves_and_cancels_through_the_async_runner(self) -> None:
        with TestClient(self.app) as client:
            pair = client.post("/dashboard/pair", headers=self.headers).json()["pairing_token"]
            client.get(f"/dashboard/?pair={pair}", headers={"Host": "127.0.0.1:8765"})
            approved = client.post("/dashboard/api/jobs/pending-job/approve", headers={"Host": "127.0.0.1:8765"})
            cancelled = client.post("/dashboard/api/jobs/pending-job/cancel", headers=self.headers)
            missing = client.post("/dashboard/api/jobs/other-job/cancel", headers=self.headers)
        self.assertEqual(approved.json(), {"approved": True})
        self.assertEqual(self.jobs.approved, ["pending-job"])
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(missing.status_code, 404)

    def test_undecryptable_job_data_is_a_json_error_not_a_crash(self) -> None:
        def unreadable(*_args, **_kwargs):
            raise OSError("DPAPI failure")

        self.jobs.list_jobs = unreadable
        self.jobs.get_job = unreadable
        with TestClient(self.app) as client:
            responses = [client.get(path, headers=self.headers) for path in ("/dashboard/api/jobs", "/dashboard/api/jobs/a")]
        for response in responses:
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json(), {"error": "Job data could not be decrypted."})

    def test_dashboard_responses_are_not_cached(self) -> None:
        with TestClient(self.app) as client:
            status = client.get("/dashboard/api/status", headers=self.headers)
        self.assertEqual(status.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
