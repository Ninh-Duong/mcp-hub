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
