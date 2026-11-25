"""Namespaced MCP resource proxying across connected child MCPs.

Each child resource URI is exposed as `mcp-hub://<server-id>/<percent-encoded-child-uri>` so
resources from multiple children never collide and clients can route `resources/read` cleanly.
"""
from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from mcp.server import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.types import (
    INVALID_PARAMS,
    ListResourcesResult,
    ListResourceTemplatesResult,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    ReadResourceResult,
    TextResourceContents,
)

from .audit import AuditLog, describe_exception, redact_secrets

RESOURCE_SCHEME = "mcp-hub"


def namespace_uri(server_id: str, uri: str) -> str:
    """Wrap one child resource URI under `mcp-hub://<server-id>/...`."""
    return f"{RESOURCE_SCHEME}://{server_id}/{quote(uri, safe='')}"


def namespace_template(server_id: str, uri_template: str) -> str:
    """Wrap a child RFC 6570 URI template while preserving `{...}` template placeholders."""
    return f"{RESOURCE_SCHEME}://{server_id}/{quote(uri_template, safe='{}')}"


def parse_namespaced_uri(uri: str) -> tuple[str, str]:
    """Decode `mcp-hub://<server-id>/<child-uri>` into `(server_id, child_uri)`."""
    parsed = urlsplit(uri)
    if parsed.scheme != RESOURCE_SCHEME or not parsed.netloc or not parsed.path.startswith("/"):
        raise MCPError(INVALID_PARAMS, "Invalid mcp-hub resource URI.")
    raw = parsed.path[1:]
    if parsed.query:
        raw += f"?{parsed.query}"
    if parsed.fragment:
        raw += f"#{parsed.fragment}"
    child_uri = unquote(raw)
    if not child_uri:
        raise MCPError(INVALID_PARAMS, "Invalid mcp-hub resource URI.")
    return parsed.netloc, child_uri


def _redact_contents(contents: list[Any], server_id: str, redact_fn: Any) -> list[Any]:
    rewritten: list[Any] = []
    for item in contents:
        updates: dict[str, Any] = {"uri": namespace_uri(server_id, str(item.uri))}
        if redact_fn is not None and isinstance(item, TextResourceContents):
            updates["text"] = redact_fn(item.text)
        rewritten.append(item.model_copy(update=updates))
    return rewritten


class ResourcesMixin:
    """MCP resource listing, template listing, and proxied read on HubRuntime."""

    async def _fetch_child_resources(self, server_id: str, method: str, wrap: Any) -> list[Any]:
        client = self.clients.get(server_id)
        server_cfg = self.config.servers.get(server_id)
        if client is None or server_cfg is None:
            return []
        try:
            page = await asyncio.wait_for(getattr(client, method)(), timeout=min(server_cfg.timeout_seconds, 10.0))
        except Exception:
            return []
        return wrap(server_id, page)

    async def _gather_resources(self, ctx: ServerRequestContext, method: str, wrap: Any) -> list[Any]:
        # ponytail: single fan-out helper shared by resources/list and resources/templates/list.
        if self.observers:
            self.notify_client(ctx)
        server_ids = [sid for sid in self.config.servers if sid in self.clients]
        batches = await asyncio.gather(*(self._fetch_child_resources(sid, method, wrap) for sid in server_ids))
        return [item for batch in batches for item in batch]

    async def list_resources(
        self, ctx: ServerRequestContext, params: PaginatedRequestParams | None,
    ) -> ListResourcesResult:
        del params
        items = await self._gather_resources(
            ctx,
            "list_resources",
            lambda sid, page: [
                item.model_copy(update={"uri": namespace_uri(sid, str(item.uri))}) for item in page.resources
            ],
        )
        return ListResourcesResult(resources=items)

    async def list_resource_templates(
        self, ctx: ServerRequestContext, params: PaginatedRequestParams | None,
    ) -> ListResourceTemplatesResult:
        del params
        items = await self._gather_resources(
            ctx,
            "list_resource_templates",
            lambda sid, page: [
                item.model_copy(update={"uri_template": namespace_template(sid, item.uri_template)})
                for item in page.resource_templates
            ],
        )
        return ListResourceTemplatesResult(resource_templates=items)

    async def read_resource(
        self, ctx: ServerRequestContext, params: ReadResourceRequestParams,
    ) -> ReadResourceResult:
        if self.observers:
            self.notify_client(ctx)
        request_id = str(ctx.request_id) if ctx.request_id is not None else "unknown"
        server_id, child_uri = parse_namespaced_uri(str(params.uri))
        server_cfg = self.config.servers.get(server_id)
        if server_cfg is None:
            raise MCPError(INVALID_PARAMS, f"Unknown child MCP: {server_id}")
        client = self.clients.get(server_id)
        if client is None:
            self.audit.write(
                "resource.read.failed", level=logging.WARNING, request_id=request_id,
                server=server_id, error_type="ChildUnavailable",
            )
            raise MCPError(INVALID_PARAMS, f"Child MCP {server_id} is unavailable.")
        started = perf_counter()
        self.audit.write("resource.read.started", request_id=request_id, server=server_id)
        try:
            result = await asyncio.wait_for(client.read_resource(child_uri), timeout=server_cfg.timeout_seconds)
        except Exception as exc:
            error_type, message = describe_exception(exc)
            self.audit.write(
                "resource.read.failed", level=logging.ERROR, request_id=request_id, server=server_id,
                duration_ms=round((perf_counter() - started) * 1000, 1), error_type=error_type, error=message,
            )
            raise MCPError(INVALID_PARAMS, f"Child resource read failed. See Hub log request_id={request_id}") from exc
        redact_fn = (
            (self.audit.redact_result_text if isinstance(self.audit, AuditLog) else redact_secrets)
            if self.settings.security.redact_results else None
        )
        self.audit.write(
            "resource.read.succeeded", request_id=request_id, server=server_id,
            duration_ms=round((perf_counter() - started) * 1000, 1),
        )
        return result.model_copy(update={"contents": _redact_contents(list(result.contents), server_id, redact_fn)})
