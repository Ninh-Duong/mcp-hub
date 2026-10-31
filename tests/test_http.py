from __future__ import annotations

import asyncio
import socket
import unittest

import httpx2
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server, ServerRequestContext
from mcp.types import ListToolsResult, PaginatedRequestParams, Tool
from starlette.testclient import TestClient


class SharedHTTPAuthenticationTest(unittest.TestCase):
    def setUp(self) -> None:
        from mcp.server import Server

        try:
            from mcp_hub.http import create_http_app
        except ImportError as exc:
            self.fail(f"shared HTTP app is not implemented: {exc}")

        self.token = "test-local-token"
        server = Server("http-test")
        self.app = create_http_app(server, self.token, host="127.0.0.1", port=8765)

    def test_mcp_endpoint_rejects_missing_bearer_token(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/mcp",
                headers={"Host": "127.0.0.1:8765", "Content-Type": "application/json"},
                content=b"{}",
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_mcp_endpoint_accepts_token_before_mcp_parsing(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/mcp",
                headers={
                    "Host": "127.0.0.1:8765",
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                content=b"not-json",
            )

        self.assertNotEqual(response.status_code, 401)

    def test_non_ascii_bearer_token_is_rejected_not_crashed(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/mcp",
                headers=[(b"host", b"127.0.0.1:8765"), (b"authorization", "Bearer tést".encode("latin-1"))],
                content=b"{}",
            )

        self.assertEqual(response.status_code, 401)

    def test_websocket_scopes_are_refused_without_reaching_the_app(self) -> None:
        from mcp_hub.http import LocalBearerMiddleware

        reached: list[str] = []
        sent: list[dict] = []

        async def app(scope, receive, send) -> None:
            reached.append(scope["type"])

        async def send(message) -> None:
            sent.append(message)

        async def receive() -> dict:
            return {"type": "websocket.connect"}

        middleware = LocalBearerMiddleware(app, self.token, "127.0.0.1", 8765)
        asyncio.run(middleware({"type": "websocket", "path": "/mcp", "headers": []}, receive, send))
        asyncio.run(middleware({"type": "lifespan"}, receive, send))
        self.assertEqual(reached, ["lifespan"])
        self.assertEqual(sent, [{"type": "websocket.close", "code": 1008}])

    def test_health_endpoint_only_reports_liveness_without_token(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/health", headers={"Host": "127.0.0.1:8765"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_mcp_endpoint_rejects_untrusted_host_and_origin(self) -> None:
        with TestClient(self.app) as client:
            bad_host = client.post(
                "/mcp",
                headers={
                    "Host": "localhost:8765",
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                content=b"{}",
            )
            bad_origin = client.post(
                "/mcp",
                headers={
                    "Host": "127.0.0.1:8765",
                    "Origin": "http://localhost:8765",
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                content=b"{}",
            )

        self.assertEqual(bad_host.status_code, 421)
        self.assertEqual(bad_origin.status_code, 403)

    def test_http_app_rejects_public_bind_and_invalid_port(self) -> None:
        from mcp.server import Server
        from mcp_hub.http import create_http_app

        with self.assertRaises(ValueError):
            create_http_app(Server("public"), self.token, host="0.0.0.0")
        with self.assertRaises(ValueError):
            create_http_app(Server("bad-port"), self.token, port=True)

    def test_the_admin_token_must_differ_from_the_client_token(self) -> None:
        from mcp_hub.http import create_http_app

        for admin in ("", self.token):
            with self.subTest(admin=admin), self.assertRaises(ValueError):
                create_http_app(Server("same-token"), self.token, admin_token=admin)

    def test_the_bridge_ignores_proxy_settings(self) -> None:
        """A system or environment proxy must never see the bearer token sent to 127.0.0.1."""
        from mcp_hub import bridge

        client = bridge.hub_http_client(self.token)
        try:
            self.assertFalse(client.trust_env)
        finally:
            asyncio.run(client.aclose())


    def test_two_http_clients_can_list_tools_at_the_same_time(self) -> None:
        from mcp_hub.http import create_http_app

        async def list_tools(_ctx: ServerRequestContext, _params: PaginatedRequestParams | None) -> ListToolsResult:
            return ListToolsResult(tools=[Tool(
                name="shared__status",
                description="Shared tool.",
                inputSchema={"type": "object", "properties": {}},
            )])

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = Server("http-concurrency-test", on_list_tools=list_tools)
        app = create_http_app(server, self.token, port=port)

        async def exercise() -> None:
            http_server = uvicorn.Server(uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="critical",
                access_log=False,
            ))
            server_task = asyncio.create_task(http_server.serve())
            try:
                for _ in range(200):
                    if http_server.started:
                        break
                    await asyncio.sleep(0.025)
                self.assertTrue(http_server.started)

                async def list_shared_tool() -> list[str]:
                    async with httpx2.AsyncClient(
                        headers={"Authorization": f"Bearer {self.token}"},
                        timeout=10.0,
                    ) as http_client:
                        async with Client(streamable_http_client(
                            f"http://127.0.0.1:{port}/mcp",
                            http_client=http_client,
                        )) as client:
                            result = await client.list_tools()
                            return [tool.name for tool in result.tools]

                names_a, names_b = await asyncio.gather(list_shared_tool(), list_shared_tool())
                self.assertEqual(names_a, ["shared__status"])
                self.assertEqual(names_b, ["shared__status"])
            finally:
                http_server.should_exit = True
                await server_task

        asyncio.run(exercise())


class TokenSeparationTest(unittest.TestCase):
    """B1/B3: an AI client's token reaches only the MCP endpoint; it can neither pair a browser, change
    settings, control the Hub, nor approve a write. The admin token can do all but approve."""

    CLIENT, ADMIN = "client-token-value-0123456789abcdef", "admin-token-value-0123456789abcdef"
    ROUTES = (
        ("POST", "/dashboard/pair"),
        ("GET", "/dashboard/api/status"),
        ("GET", "/dashboard/api/settings"),
        ("GET", "/dashboard/api/settings/autostart"),
        ("POST", "/dashboard/api/control/stop"),
        ("POST", "/dashboard/api/service/shutdown"),
    )
    APPROVALS = (("POST", "/dashboard/api/jobs/a/approve"), ("GET", "/dashboard/api/jobs/a/approval-code"))

    def setUp(self) -> None:
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        from mcp_hub.dashboard.sessions import DashboardSessions
        from mcp_hub.http import create_http_app

        async def ok(_request) -> JSONResponse:
            return JSONResponse({"ok": True})

        paths = {path for _method, path in (*self.ROUTES, *self.APPROVALS)}
        routes = [Route(path, ok, methods=["GET", "POST"]) for path in paths]
        self.sessions = DashboardSessions()
        self.app = create_http_app(
            Server("tokens"), self.CLIENT, custom_routes=routes, sessions=self.sessions, admin_token=self.ADMIN,
        )

    def call(self, client: TestClient, method: str, path: str, token: str | None = None):
        headers = {"Host": "127.0.0.1:8765", **({"Authorization": f"Bearer {token}"} if token else {})}
        return client.request(method, path, headers=headers)

    def test_the_client_token_reaches_only_the_mcp_endpoint(self) -> None:
        with TestClient(self.app) as client:
            for method, path in (*self.ROUTES, *self.APPROVALS):
                with self.subTest(path=path):
                    self.assertEqual(self.call(client, method, path, self.CLIENT).status_code, 403)
            mcp = client.post("/mcp", headers={"Host": "127.0.0.1:8765", "Authorization": f"Bearer {self.CLIENT}",
                                               "Content-Type": "application/json"}, content=b"not-json")
            self.assertNotIn(mcp.status_code, (401, 403))
            admin_on_mcp = self.call(client, "POST", "/mcp", self.ADMIN)
            self.assertEqual(admin_on_mcp.status_code, 401)

    def test_the_admin_token_reaches_the_dashboard_api_but_never_an_approval(self) -> None:
        with TestClient(self.app) as client:
            for method, path in self.ROUTES:
                with self.subTest(path=path):
                    self.assertEqual(self.call(client, method, path, self.ADMIN).status_code, 200)
            for method, path in self.APPROVALS:
                with self.subTest(path=path):
                    self.assertEqual(self.call(client, method, path, self.ADMIN).status_code, 403)

    def test_a_paired_session_approves_but_never_reaches_the_settings(self) -> None:
        session = self.sessions.consume_pairing_token(self.sessions.issue_pairing_token())
        with TestClient(self.app) as client:
            client.cookies.set("hub_session", session)
            for method, path in (*self.APPROVALS, ("GET", "/dashboard/api/status")):
                with self.subTest(path=path):
                    self.assertEqual(self.call(client, method, path).status_code, 200)
            # Settings run hubctl and change Hub files: a cookie a page on another local port could ride is not enough.
            for path in ("/dashboard/api/settings", "/dashboard/api/settings/autostart"):
                with self.subTest(path=path):
                    self.assertEqual(self.call(client, "GET", path).status_code, 401)
