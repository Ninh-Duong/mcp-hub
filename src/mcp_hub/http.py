from __future__ import annotations

import hmac
import re
from collections.abc import Sequence
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server import Server
from mcp.server.transport_security import TransportSecuritySettings

# Dashboard routes that reveal or use a write approval; only a paired browser session may call them.
_SESSION_ONLY_PATH = re.compile(r"^/dashboard/api/jobs/[^/]+/(?:approve|approval-code|pending-call)/?$")


class LocalBearerMiddleware:
    def __init__(self, app: ASGIApp, token: str, host: str, port: int, sessions=None) -> None:
        self.app = app
        self.token = token
        self.sessions = sessions
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
        if _SESSION_ONLY_PATH.match(path):
            # Approving a write is a human step: the bearer token every AI client holds must not do it.
            if self._session_authorized(scope, headers):
                return None
            return PlainTextResponse("This action requires the paired dashboard session", status_code=403)
        if self._session_authorized(scope, headers) or self._bearer_authorized(headers):
            return None
        return PlainTextResponse("Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"})

    def _bearer_authorized(self, headers: dict[bytes, bytes]) -> bool:
        authorization = headers.get(b"authorization", b"")
        supplied = authorization[7:] if authorization.startswith(b"Bearer ") else b""
        return bool(supplied) and hmac.compare_digest(supplied, self.token.encode("utf-8"))

    def _session_authorized(self, scope: Scope, headers: dict[bytes, bytes]) -> bool:
        """A paired browser: a valid dashboard session cookie, or a pairing token on the entry page."""
        if not scope.get("path", "").startswith("/dashboard") or self.sessions is None:
            return False
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get(b"cookie", b"").decode("latin-1"))
        except (ValueError, UnicodeError):
            return False
        session = cookies.get("hub_session")
        if session and self.sessions.is_session_valid(session.value):
            return True
        if scope.get("path") in {"/dashboard", "/dashboard/"}:
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            pairing = query.get("pair", [None])[0]
            return self.sessions.is_pairing_valid(pairing)
        return False


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
                    (
                        b"content-security-policy",
                        b"default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                    ),
                    # Approval codes and job data must not stay in a browser or proxy cache.
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
    sessions=None,
    middleware: Sequence[tuple[type, dict[str, Any]]] = (),
) -> ASGIApp:
    """Build the authenticated app. `middleware` (class, options) pairs run inside the bearer check."""
    if host != "127.0.0.1":
        raise ValueError("The shared Hub must bind to 127.0.0.1")
    if not token:
        raise ValueError("The shared Hub bearer token must not be empty")
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
    app.add_middleware(LocalBearerMiddleware, token=token, host=host, port=port, sessions=sessions)
    return app
