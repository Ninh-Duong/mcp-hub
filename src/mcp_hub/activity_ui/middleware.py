from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send


class ClientSessionMiddleware:
    """Tracks authenticated MCP sessions; must sit inside LocalBearerMiddleware."""

    def __init__(self, app: ASGIApp, client_sessions, path: str = "/mcp") -> None:
        self.app = app
        self.client_sessions = client_sessions
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "").rstrip("/") != self.path:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        session_id = headers.get(b"mcp-session-id", b"").decode("latin-1") or None
        if session_id and scope.get("method") == "DELETE":
            self.client_sessions.close(session_id)
            await self.app(scope, receive, send)
            return

        opened: str | None = None

        async def tracking_send(message) -> None:
            nonlocal opened
            if message["type"] == "http.response.start":
                opened = self._on_response_start(message, session_id) or opened
            await send(message)

        if session_id:
            self.client_sessions.begin(session_id)
        try:
            await self.app(scope, receive, tracking_send)
        finally:
            if session_id or opened:
                self.client_sessions.end(session_id or opened)

    def _on_response_start(self, message, session_id: str | None) -> str | None:
        """Return the session ID a successful initialize response opens, if any."""
        status = message.get("status")
        if session_id and status == 404:
            self.client_sessions.close(session_id)  # unknown or expired session
        if session_id is not None or status != 200:
            return None
        # The opening initialize request learns its session ID from the response.
        for key, value in message.get("headers", []):
            if key.lower() == b"mcp-session-id":
                opened = value.decode("latin-1")
                self.client_sessions.begin(opened)
                return opened
        return None
