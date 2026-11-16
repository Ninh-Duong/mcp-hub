"""Call observers on HubRuntime: the activity and processing hooks for each child call. An observer failure is counted and logged, never raised into the call."""
from __future__ import annotations

import logging
from time import perf_counter
from typing import Any, NamedTuple

from .clients import normalize_client_name
from .config import ToolRoute
from .extensions import ProcessingEvent, emit_processing


class ChildCallState(NamedTuple):
    """One child call in flight: its route, request id, activity tokens, and start time."""
    route: ToolRoute
    request_id: str
    tokens: tuple[Any, ...] | None
    started: float


class CallObserverMixin:
    """Methods of HubRuntime, moved out of server.py."""

    def _observe_start(self, route: ToolRoute, client_name: str, agent_role: str | None) -> tuple[Any, ...]:
        client = normalize_client_name(client_name)
        tokens: list[Any] = []
        for observer in self.observers:
            try:
                tokens.append(observer.call_started(route.server_id, route.tool_name, client, agent_role))
            except Exception as exc:
                tokens.append(None)
                self._observer_failed(exc)
        return tuple(tokens)

    def _observe_finish(self, tokens: tuple[Any, ...], state: str, error_type: str | None, duration_ms: int) -> None:
        for observer, token in zip(self.observers, tokens, strict=False):
            if token is None:
                continue
            try:
                observer.call_finished(token, state, error_type, duration_ms)
            except Exception as exc:
                self._observer_failed(exc)

    def _observer_failed(self, exc: Exception) -> None:
        # An extension must never break a tool call; only the error type is logged.
        self.audit.write("extension.observer.failed", level=logging.ERROR, error_type=type(exc).__name__)

    def _complete_call(
        self,
        call: ChildCallState,
        state: str,
        activity_error: str | None,
        event: str,
        level: int,
        **audit_fields: Any,
    ) -> None:
        """Record the end of a child call for observers and in the audit log."""
        duration_ms = round((perf_counter() - call.started) * 1000)
        if self.processing_observer is not None:
            self._processing_call_finished(call, state, activity_error, event, duration_ms)
        if call.tokens is not None:
            self._observe_finish(call.tokens, state, activity_error, duration_ms)
        route = call.route
        self.audit.write(
            event, level=level, request_id=call.request_id, feature=route.feature, server=route.server_id,
            tool=route.tool_name, duration_ms=duration_ms, **audit_fields,
        )

    def _processing_call_finished(
        self, call: ChildCallState, state: str, error_type: str | None, event: str, duration_ms: int,
    ) -> None:
        error = None if error_type is None else {
            "CancelledError": "cancelled", "ChildUnavailable": "unavailable", "ToolError": "tool_error",
            "TimeoutError": "timeout",
        }.get(error_type, "unexpected")
        emit_processing(self.processing_observer, ProcessingEvent(
            "child_call", "succeeded" if state == "succeeded" else "failed",
            "cache" if event == "tool.call.cached" else "child", call.route.server_id, call.route.tool_name,
            duration_ms, error,
        ))
