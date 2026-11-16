from __future__ import annotations

import hmac
from collections.abc import Sequence
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server import Server
from mcp.server.transport_security import TransportSecuritySettings

class LocalBearerMiddleware:
    """The client token reaches MCP; the admin token reaches the local desktop API.

    The tokens differ so an AI client or bridge cannot change settings or control the Hub.
    """

    def __init__(
        self, app: ASGIApp, token: str, host: str, port: int, admin_token: str | None = None,
    ) -> None:
        self.app = app
        self.token = token
        self.admin_token = admin_token
        self.allowed_hosts = {f"{host}:{port}"}
        self.allowed_origins = {f"http://{host}:{port}"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # No websocket or other scope is served; refuse instead of passing it through unauthenticated.
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        secured_send = _with_security_headers(send, dashboard=scope.get("path", "").startswith("/dashboard"))
        rejection = self._rejection(scope, headers)
        if rejection is None:
            await self.app(scope, receive, secured_send)
        else:
            await rejection(scope, receive, secured_send)

    def _rejection(self, scope: Scope, headers: dict[bytes, bytes]) -> Response | None:
        host = headers.get(b"host", b"").decode("latin-1")
        origin = headers.get(b"origin", b"").decode("latin-1")
        if host not in self.allowed_hosts:
            return PlainTextResponse("Invalid Host header", status_code=421)
        if origin and origin not in self.allowed_origins:
            return PlainTextResponse("Invalid Origin header", status_code=403)
        path = scope.get("path", "")
        if path == "/health":
            return None
        if not path.startswith("/dashboard"):
            return None if self._bearer_matches(headers, self.token) else _unauthorized()
        if self._bearer_matches(headers, self.admin_token):
            return None
        if self._bearer_matches(headers, self.token):
            return PlainTextResponse("This route requires the Hub's admin token", status_code=403)
        return _unauthorized()

    @staticmethod
    def _bearer_matches(headers: dict[bytes, bytes], token: str | None) -> bool:
        authorization = headers.get(b"authorization", b"")
        supplied = authorization[7:] if authorization.startswith(b"Bearer ") else b""
        return bool(supplied and token) and hmac.compare_digest(supplied, token.encode("utf-8"))

def _unauthorized() -> Response:
    return PlainTextResponse("Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"})


def _with_security_headers(send: Send, *, dashboard: bool) -> Send:
    async def secured_send(message) -> None:
        if message["type"] == "http.response.start":
            response_headers = list(message.get("headers", []))
            response_headers.extend([
                (b"x-content-type-options", b"nosniff"),
                (b"referrer-policy", b"no-referrer"),
            ])
            if dashboard:
                response_headers.extend([
                    # Authenticated local API responses can include settings and activity metadata.
                    (b"cache-control", b"no-store"),
                ])
            message = {**message, "headers": response_headers}
        await send(message)

    return secured_send


async def _health(_request: Request) -> Response:
    return JSONResponse({"ok": True})


def create_http_app(
    server: Server,
    token: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    custom_routes=None,
    middleware: Sequence[tuple[type, dict[str, Any]]] = (),
    admin_token: str | None = None,
) -> ASGIApp:
    """Build the authenticated app. `middleware` (class, options) pairs run inside the bearer check.

    `token` is the client token (MCP endpoint only); without `admin_token` no bearer reaches the local API.
    """
    if host != "127.0.0.1":
        raise ValueError("The shared Hub must bind to 127.0.0.1")
    if not token:
        raise ValueError("The shared Hub bearer token must not be empty")
    if admin_token is not None and (not admin_token or hmac.compare_digest(admin_token, token)):
        raise ValueError("The admin token must be set and differ from the client token")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("The shared Hub port must be between 1 and 65535")

    origin = f"http://{host}:{port}"
    app = server.streamable_http_app(
        host=host,
        custom_starlette_routes=[Route("/health", _health, methods=["GET"]), *(custom_routes or [])],
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{host}:{port}"],
            allowed_origins=[origin],
        ),
    )
    for middleware_class, options in middleware:
        # Added before the bearer check so it runs inside it: only authorized requests reach it.
        app.add_middleware(middleware_class, **options)
    app.add_middleware(
        LocalBearerMiddleware, token=token, host=host, port=port, admin_token=admin_token,
    )
    return app
