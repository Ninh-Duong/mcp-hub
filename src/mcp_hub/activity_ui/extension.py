from __future__ import annotations

from typing import Any

from mcp.server import Server, ServerRequestContext
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.types import SubscriptionsListenRequestParams, SubscriptionsListenResult

from ..audit import AuditLog
from ..extensions import ACTIVITY_UI, DashboardExtension
from ..server import HubRuntime
from .client_sessions import ClientSessions
from .middleware import ClientSessionMiddleware
from .recorder import ActivityRecorder
from .routes import ActivityRoutes
from .tracker import ActivityCall, ActivityTracker


class ActivityUi:
    """The optional activity UI: live call activity, the daily history, and connected AI clients.

    It is a `CallObserver` and an `ActivitySource` for the Hub runtime. Every hook is in-memory;
    finished calls reach the store through the background `ActivityRecorder`.
    """

    def __init__(self, runtime: HubRuntime, store: Any | None, audit: AuditLog) -> None:
        self.runtime = runtime
        self.tracker = ActivityTracker()
        self.client_sessions = ClientSessions()
        self.recorder = ActivityRecorder(store, audit) if store is not None else None
        self._routes = ActivityRoutes(runtime, self.tracker, store)
        self._listen_handler = ListenHandler(InMemorySubscriptionBus())

    # CallObserver
    def call_started(self, server_id: str, tool_name: str, client: str, agent_role: str | None) -> ActivityCall:
        return self.tracker.begin(server_id, tool_name, client, agent_role)

    def call_finished(self, token: ActivityCall, state: str, error_type: str | None, duration_ms: int) -> None:
        record = self.tracker.finish(token, state, error_type, duration_ms)
        if self.recorder is not None:
            self.recorder.record(record)

    def client_seen(self, ctx: Any) -> None:
        self.client_sessions.note(ctx)

    # ActivitySource
    def activity_snapshot(self, server_ids: tuple[str, ...], tool_counts: dict[str, int]) -> dict[str, Any]:
        return self.tracker.snapshot(server_ids, tool_counts)

    def client_snapshot(self) -> list[dict[str, Any]]:
        return self.client_sessions.snapshot()

    # Wiring
    async def listen(
        self, ctx: ServerRequestContext, params: SubscriptionsListenRequestParams,
    ) -> SubscriptionsListenResult:
        # An open listen stream is how a stateless 2026-07-28 client (the stdio bridge) shows as connected.
        stream_id = self.client_sessions.open_stream(self.client_sessions.note(ctx))
        try:
            return await self._listen_handler(ctx, params)
        finally:
            self.client_sessions.close_stream(stream_id)

    def attach_server(self, server: Server) -> None:
        """Serve `subscriptions/listen` so bridge clients show as connected."""
        server.add_request_handler("subscriptions/listen", SubscriptionsListenRequestParams, self.listen)

    def middleware(self) -> tuple[tuple[type, dict[str, Any]], ...]:
        return ((ClientSessionMiddleware, {"client_sessions": self.client_sessions}),)

    def dashboard_extension(self) -> DashboardExtension:
        return DashboardExtension(
            name=ACTIVITY_UI,
            routes=self._routes.routes(),
            status_fields=self.status_fields,
            hubctl_flags=("-Ui",),
        )

    def status_fields(self) -> dict[str, Any]:
        return {"activity": self.runtime.activity_snapshot(), "clients": self.runtime.client_snapshot()}

    def start(self) -> None:
        if self.recorder is not None:
            self.recorder.start()

    def close_listeners(self) -> None:
        """End open listen streams gracefully so shutdown need not wait for the drain deadline."""
        self._listen_handler.close()

    async def close(self) -> None:
        """Write every finished call that is still queued."""
        if self.recorder is not None:
            await self.recorder.close()


def install(runtime: HubRuntime, *, store: Any | None, audit: AuditLog) -> ActivityUi:
    """Install the activity UI on a runtime. Only `service.py` calls this, and only when requested at start."""
    ui = ActivityUi(runtime, store, audit)
    runtime.add_observer(ui)
    runtime.activity_source = ui
    return ui
