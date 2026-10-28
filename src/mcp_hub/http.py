from __future__ import annotations

import hmac
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server import Server
from mcp.server.transport_security import TransportSecuritySettings


class LocalBearerMiddleware:
    def __init__(self, app: ASGIApp, token: str, host: str, port: int, sessions=None) -> None:
        self.app = app
        self.token = token
        self.sessions = sessions
        self.allowed_hosts = {f"{host}:{port}"}
        self.allowed_origins = {f"http://{host}:{port}"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}

        async def secured_send(message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ])
                if scope.get("path", "").startswith("/dashboard"):
                    response_headers.append((
                        b"content-security-policy",
                        b"default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                    ))
                message = {**message, "headers": response_headers}
            await send(message)

        host = headers.get(b"host", b"").decode("latin-1")
        origin = headers.get(b"origin", b"").decode("latin-1")
        if host not in self.allowed_hosts:
            response = PlainTextResponse("Invalid Host header", status_code=421)
        elif origin and origin not in self.allowed_origins:
            response = PlainTextResponse("Invalid Origin header", status_code=403)
        elif scope.get("path") == "/health":
            await self.app(scope, receive, secured_send)
            return
        elif self._dashboard_session_authorized(scope, headers):
            await self.app(scope, receive, secured_send)
            return
        else:
            authorization = headers.get(b"authorization", b"").decode("latin-1")
            supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
            if not supplied or not hmac.compare_digest(supplied, self.token):
                response = PlainTextResponse(
                    "Unauthorized",
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            else:
                await self.app(scope, receive, secured_send)
                return

        await response(scope, receive, secured_send)

    def _dashboard_session_authorized(self, scope: Scope, headers: dict[bytes, bytes]) -> bool:
        if not scope.get("path", "").startswith("/dashboard") or self.sessions is None:
            return False
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if supplied and hmac.compare_digest(supplied, self.token):
            return True
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
) -> ASGIApp:
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
    app.add_middleware(LocalBearerMiddleware, token=token, host=host, port=port, sessions=sessions)
    return app
