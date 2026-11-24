from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import AsyncExitStack
from pathlib import Path
from time import perf_counter
from collections.abc import Mapping
from typing import Any, NamedTuple

import anyio
from mcp import Client
from mcp.server import Server, ServerRequestContext
from mcp.server.caching import CacheHint
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from mcp.types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    GetPromptRequestParams,
    GetPromptResult,
    ListPromptsResult,
    ListToolsResult,
    NotificationParams,
    PaginatedRequestParams,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
    Tool,
)

from . import config as hub_config
from .audit import AuditLog, default_log_dir, describe_exception, redact
from .audit_events import TOOL_ARGUMENTS_INVALID
from .batch import BATCH_TOOL, BatchMixin, batch_tool
from .child_lifecycle import ChildLifecycleMixin
from .children import Backoff, ChildOpener, ChildSupervisor, ChildUnavailable, open_stdio_client
from .client_profiles import ClientProfileCatalog, load_client_profiles
from .clients import request_client_name
from .results.condense import RuleCondenser
from .config_sources import ConfigSources, gaps_after_reports
from .config import HubConfig, ServerConfig, ToolRoute
from .events import HubEvents
from .extensions import ActivitySource, CallObserver, PlanAssistant, ResultCondenser, empty_activity
from .extensions import ProcessingEvent, ProcessingObserver, emit_processing
from .results.full_results import FullResultStore
from .planning import confirm as plan_confirm, policy
from .planning.playbooks import Playbook, load_playbooks
from .planning.policy import PLAN_TOOL, CallFacts, PlanBook
from .projects import ProjectConfig
from .results.cache import ResultCache
from .schema_rewrite import validate_args
from .settings import Settings
from .progress import ProgressSender, forwarded_meta, progress_sender, progress_token
from .tool_meta import is_read_only_tool
from .instructions import repo_relative, server_instructions, child_contract
from .planning.hub_plan import PlanningMixin
from .planning.workflow_runtime import WorkflowRuntimeMixin, WORKFLOW_TOOL, workflow_tool
from .call_observers import CallObserverMixin, ChildCallState
from .bootstrap import load_start_inputs
from .results.runtime import FULL_RESULT_TOOL, ResultsMixin, error_result, full_result_tool
from .results.shape import SUMMARY_REDACTION_MARGIN, result_error_summary, result_succeeded
from .tool_pins import PinScreeningMixin, PinStore, fingerprint


# Characters scanned for redaction beyond the summary limit, so a secret cut at the limit is still matched.
# A denied tool name comes from the client, so the log keeps a short, redacted copy of it.
DENIED_NAME_LIMIT = 128


def enable_core_condenser(runtime: HubRuntime) -> None:
    """Attach `RuleCondenser` and `FullResultStore` to the runtime when missing."""
    bitnet = runtime.settings.bitnet
    if runtime.condenser is None:
        runtime.condenser = RuleCondenser(max_chars=bitnet.condense_min_chars)
    if runtime.full_results is None:
        runtime.full_results = FullResultStore(
            bitnet.full_result_max_entries, bitnet.full_result_ttl_seconds, max_bytes=bitnet.full_result_max_bytes,
        )


def tool_pins_path() -> Path:
    """The tool pin file: config/private/tool_pins.json under the private root."""
    return hub_config.PRIVATE_ROOT / "tool_pins.json"


_DEFAULT_SETTINGS = Settings()


class HubRuntime(ChildLifecycleMixin, PinScreeningMixin, WorkflowRuntimeMixin, BatchMixin, PlanningMixin, ResultsMixin, CallObserverMixin):
    """Child connections, the allowlist, and routing.

    Extensions add observers with `add_observer`; with none installed a call is route -> audit -> child.
    """

    def __init__(
        self, config: HubConfig, audit: AuditLog, settings: Settings = _DEFAULT_SETTINGS, *,
        playbooks: Mapping[str, Playbook] | None = None, projects: Mapping[str, ProjectConfig] | None = None,
        client_profiles: ClientProfileCatalog | None = None, shared_service: bool = False,
        processing_observer: ProcessingObserver | None = None, pin_path: Path | None = None,
    ) -> None:
        self.config = config
        self.audit = audit
        self.settings = settings
        # Live sessions only: a child's entry exists exactly while it is connected.
        self.clients: dict[str, Client] = {}
        self.routes: dict[str, ToolRoute] = {}
        self.public_tools: list[Tool] = []
        self.discovered_tool_counts: dict[str, int] = {}
        self.observers: tuple[CallObserver, ...] = ()
        self.activity_source: ActivitySource | None = None
        self.processing_observer = processing_observer
        self.result_cache = ResultCache(max_entries=settings.cache.max_entries, max_bytes=settings.cache.max_bytes)
        self.child_opener: ChildOpener = open_stdio_client
        self.child_backoff = Backoff(
            initial=settings.children.reconnect_initial_seconds, maximum=settings.children.reconnect_max_seconds,
        )
        audit_path = getattr(audit, "path", None)
        self.child_log_dir: Path = (audit_path.parent if isinstance(audit_path, Path) else default_log_dir()) / "children"
        self.supervisors: dict[str, ChildSupervisor] = {}
        self.config_sources = ConfigSources(audit, settings.children.config_status_timeout_seconds)
        self._discovered: dict[str, dict[str, Tool]] = {}
        self._read_only: set[str] = set()
        self._setup_planning(settings, playbooks, projects, client_profiles, shared_service)
        self.events = HubEvents(settings.protocol.list_changed_debounce_seconds, on_publish=self._audit_tools_published)
        self._background: set[asyncio.Task[None]] = set()
        self._setup_pins(pin_path, settings)
        self._starting = False

    def _setup_planning(
        self, settings: Settings, playbooks: Mapping[str, Playbook] | None,
        projects: Mapping[str, ProjectConfig] | None, client_profiles: ClientProfileCatalog | None,
        shared_service: bool,
    ) -> None:
        """Initialize plan book, playbooks, projects, client profiles, and optional result helpers."""
        self._call_facts: dict[str, CallFacts] = {}
        self.plans = PlanBook(settings.policy.max_plans, repeat_after_seconds=settings.policy.repeat_after_seconds)
        self.confirm_tokens = plan_confirm.ConfirmTokens(settings.policy.confirm_token_seconds)
        self.playbooks: Mapping[str, Playbook] = load_playbooks() if playbooks is None else playbooks
        self.projects: Mapping[str, ProjectConfig] = projects or {}
        self.client_profiles: ClientProfileCatalog = (
            load_client_profiles(private_root=None) if client_profiles is None else client_profiles
        )
        self.shared_service = shared_service
        self._health_tools = frozenset(settings.policy.health_check_tools)
        self.plan_assistant: PlanAssistant | None = None
        self.condenser: ResultCondenser | None = None
        self.full_results: FullResultStore | None = None

    def _setup_pins(self, pin_path: Path | None, settings: Settings) -> None:
        """Pins are kept only when a pin file is given (the service passes the private one) and the mode is warn."""
        self.pins: PinStore | None = (
            PinStore(pin_path) if pin_path is not None and settings.security.tool_pin_mode != "off" else None
        )
        self._max_description_chars = settings.security.max_description_chars
        self._pin_changes: dict[str, tuple[str, ...]] = {}
        # Pin saves take turns in the order they were scheduled, so the newest snapshot is written last (F3).
        self._pin_save_lock = asyncio.Lock()

    def add_observer(self, observer: CallObserver) -> None:
        self.observers = (*self.observers, observer)

    def notify_client(self, ctx: Any) -> None:
        """Tell observers an MCP request arrived; a no-op without observers."""
        for observer in self.observers:
            try:
                observer.client_seen(ctx)
            except Exception as exc:
                self._observer_failed(exc)


    def enable_core_condenser(self) -> None:
        """Install core deterministic condensing and full-result storage when not set."""
        enable_core_condenser(self)

    async def close_children(self) -> None:
        """Stop every supervisor and its child process; no reconnect follows."""
        await asyncio.gather(*(supervisor.stop() for supervisor in self.supervisors.values()))


    def _register_routes(self, only_server: str | None = None) -> str:
        """Rebuild routes and public tools from the latest tools of each child.

        `route.skipped` is written for every missing tool, or only for `only_server`'s after a reconnect.
        """
        self._check_tools(only_server)
        routes: dict[str, ToolRoute] = {}
        batch = batch_tool(self.settings.batch.max_calls)
        batch = batch.model_copy(update={"input_schema": policy.with_plan_id(batch.input_schema)})
        hub_tools = [_hub_status_tool(), policy.plan_tool(self.playbooks), batch, workflow_tool()]
        child_tools: list[Tool] = []
        read_only: set[str] = set()
        facts: dict[str, CallFacts] = {}
        parts: list[tuple[str, str, str]] = []
        for route in _in_list_order(self.config.routes, self.config.servers):
            child_tool = self._discovered.get(route.server_id, {}).get(route.tool_name)
            if child_tool is None:
                if only_server in (None, route.server_id):
                    self.audit.write(
                        "route.skipped",
                        level=logging.WARNING,
                        feature=route.feature,
                        server=route.server_id,
                        tool=route.tool_name,
                        reason="server unavailable" if route.server_id not in self._discovered else "tool not found",
                    )
                continue
            routes[route.exposed_name] = route
            parts.append((route.server_id, route.tool_name, fingerprint(child_tool)))
            child_tools.append(_public_tool(route, child_tool))
            if is_read_only_tool(child_tool):
                read_only.add(route.exposed_name)
            facts[route.exposed_name] = CallFacts(
                route.exposed_name, route.server_id, route.exposed_name in read_only, policy.tool_accepts_env(child_tool),
            )
        self.routes, self.public_tools, self._read_only, self._call_facts = routes, [*hub_tools, *child_tools], read_only, facts
        return _tools_signature(parts)

    async def list_tools(
        self,
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        if self.observers:
            self.notify_client(ctx)
        tools = [*self.public_tools, full_result_tool()] if self.full_results is not None else self.public_tools
        self.audit.write(
            "hub.tools.listed",
            request_id=str(ctx.request_id) if ctx.request_id is not None else None,
            tool_count=len(tools),
        )
        return ListToolsResult(tools=tools)

    def tools_by_server(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for name, route in self.routes.items():
            grouped.setdefault(route.server_id, []).append(name)
        return grouped

    def child_ids(self) -> tuple[str, ...]:
        """Detected, configured, and unavailable children in a stable order, without duplicates."""
        return tuple(dict.fromkeys((*self.config.detected, *self.config.servers, *self.config.unavailable)))

    def config_incomplete(self) -> dict[str, list[str]]:
        """Children that started with required settings still empty: key paths only, never values.

        A group the child reports as set through `config_status` (for example from its own local files) is left out.
        """
        return gaps_after_reports(self.config.incomplete, self.config.incomplete_groups, self.config_sources.reports())

    def config_source_reports(self) -> dict[str, dict[str, str]]:
        """Each connected child's `config_status` report: group ids and source words only."""
        return self.config_sources.reports()

    def child_states(self) -> dict[str, str]:
        """Each known child's state: connected, connecting, reconnecting, a configured reason
        (such as disabled or credentials required), or unavailable."""
        return {server_id: self._child_state(server_id) for server_id in self.child_ids()}

    def _child_state(self, server_id: str) -> str:
        if server_id in self.clients:
            return "connected"
        supervisor = self.supervisors.get(server_id)
        if supervisor is not None:
            return supervisor.state
        return self.config.unavailable.get(server_id, "unavailable")

    def activity_snapshot(self) -> dict[str, Any]:
        """Live activity from the activity UI extension; empty when it is not installed."""
        if self.activity_source is None:
            return empty_activity()
        return self.activity_source.activity_snapshot(self.child_ids(), self.discovered_tool_counts)

    def client_snapshot(self) -> list[dict[str, Any]]:
        """Connected AI clients from the activity UI extension; empty when it is not installed."""
        return [] if self.activity_source is None else self.activity_source.client_snapshot()

    async def list_prompts(
        self,
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None,
    ) -> ListPromptsResult:
        return ListPromptsResult(
            prompts=[
                Prompt(
                    name=server_id,
                    description=f"Work with the {server_id} child MCP through mcp-hub ({len(tools)} tools)",
                    arguments=[PromptArgument(name="task", description="What you want done", required=False)],
                )
                for server_id, tools in self.tools_by_server().items()
            ]
        )

    async def get_prompt(self, ctx: ServerRequestContext, params: GetPromptRequestParams) -> GetPromptResult:
        tools = self.tools_by_server().get(params.name)
        if tools is None:
            raise MCPError(INVALID_PARAMS, f"Unknown prompt: {params.name}")
        parts = [f"Use only these mcp-hub tools from the '{params.name}' child MCP: {', '.join(tools)}."]
        contract = child_contract(params.name)
        if contract:
            parts.append(contract)
        task = (params.arguments or {}).get("task")
        if task:
            parts.append(f"Task: {task}")
        return GetPromptResult(
            description=f"{params.name} via mcp-hub",
            messages=[PromptMessage(role="user", content=TextContent(type="text", text="\n\n".join(parts)))],
        )

    async def call_tool(self, ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
        request_id = str(ctx.request_id) if ctx.request_id is not None else "unknown"
        client_name = "unknown"
        if self.observers:
            self.notify_client(ctx)
            client_name = request_client_name(ctx)
        arguments = params.arguments or {}
        if params.name == "hub__status":
            return self._hub_status(request_id)
        if params.name == FULL_RESULT_TOOL and self.full_results is not None:
            return self._get_full_result(arguments, request_id)
        if params.name == PLAN_TOOL:
            plan_client = client_name if self.observers else request_client_name(ctx)
            return await self._create_plan(arguments, request_id, plan_client)
        if params.name == WORKFLOW_TOOL:
            return self._workflow_operation(arguments, request_id)
        if params.name == BATCH_TOOL:
            return await self._call_batch(arguments, request_id, client_name)
        return await self._call_planned(params.name, arguments, request_id, client_name, _child_options(ctx, params))


    async def _call_planned_core(
        self, name: str, arguments: dict[str, Any], request_id: str, client_name: str,
        options: ChildOptions | None = None,
    ) -> CallToolResult:
        """A client's direct child call, checked against its plan before it reaches the child."""
        plan_id, arguments = policy.split_plan_id(arguments)
        plan = self.plans.get(plan_id)
        facts = self._call_facts.get(name)
        violation = None
        if facts is not None and self.policy_mode != "off":  # an unrouted tool is denied by the allowlist
            violation = policy.check(plan, facts, arguments, self._health_tools, plan_id)
        if violation is not None and self.policy_mode == "enforce":
            return self._policy_denied(name, request_id, violation)
        result = await self._workflow_child_call(
            plan, name, arguments, request_id, client_name, options,
        )
        policy.record(plan, name, arguments, result_succeeded(result))
        result = await self._maybe_condense(name, result, plan.task if plan else None, request_id)
        if violation is None:
            return result
        self._policy_audit("tool.call.policy_warned", name, request_id, violation)
        warning = TextContent(type="text", text=f"Policy warning: {violation.message}")
        return result.model_copy(update={"content": [warning, *result.content]})


    def _hub_status(self, request_id: str) -> CallToolResult:
        fields = {"request_id": request_id, "feature": "hub", "server": "mcp-hub", "tool": "hub__status"}
        self.audit.write("tool.call.started", **fields)
        children = self.child_states()
        self.audit.write("tool.call.succeeded", **fields, duration_ms=0)
        status: dict[str, Any] = {
            "detected": len(self.config.detected),
            "connected": len(self.clients),
            "children": children,
            "result_cache": self.result_cache.stats(),
        }
        changed = self._changed_pins()
        if changed:  # the field appears only while a pinned definition differs from its pin
            status["tool_pins"] = {"changed": changed}
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(status))])


    def _screen_arguments(self, route: ToolRoute, arguments: dict[str, Any], request_id: str) -> None:
        """In warn mode, audit arguments that do not match the child's input schema; the call still runs (FR-012)."""
        if self.settings.protocol.validate_arguments != "warn":
            return
        child_tool = self._discovered.get(route.server_id, {}).get(route.tool_name)
        paths = validate_args(getattr(child_tool, "input_schema", None), arguments)
        if paths:
            self.audit.write(
                TOOL_ARGUMENTS_INVALID, level=logging.WARNING, request_id=request_id, feature=route.feature,
                server=route.server_id, tool=route.tool_name, fields=paths,
            )

    async def call_child_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        request_id: str = "unknown",
        client_name: str = "unknown",
        agent_role: str | None = None,
        options: ChildOptions | None = None,
    ) -> CallToolResult:
        # A caller that copied a client tool schema may pass the plan ID along; a child never receives it.
        arguments = policy.split_plan_id(arguments)[1]
        route = self.routes.get(name)
        if route is None:
            denied = redact(str(name)[:DENIED_NAME_LIMIT + SUMMARY_REDACTION_MARGIN], limit=DENIED_NAME_LIMIT)
            self.audit.write("tool.call.denied", level=logging.WARNING, request_id=request_id, tool=denied)
            return error_result("This tool is not enabled in the Hub configuration.")
        self._screen_arguments(route, arguments, request_id)
        tokens = self._observe_start(route, client_name, agent_role) if self.observers else None
        call = ChildCallState(route, request_id, tokens, perf_counter())
        if self.processing_observer is not None:
            emit_processing(self.processing_observer, ProcessingEvent(
                "child_call", "started", "child", route.server_id, route.tool_name,
            ))
        self.audit.write(
            "tool.call.started", request_id=request_id, feature=route.feature, server=route.server_id, tool=route.tool_name,
        )
        try:
            result, cached = await self._invoke_child(route, arguments, options)
        except asyncio.CancelledError:
            self._complete_call(call, "interrupted", "CancelledError",
                                "tool.call.interrupted", logging.WARNING, error_type="CancelledError")
            raise
        except ChildUnavailable:
            self._complete_call(call, "failed", "ChildUnavailable",
                                "tool.call.failed", logging.ERROR, error_type="ChildUnavailable")
            return error_result(f"Child MCP {route.server_id} is reconnecting; retry shortly. request_id={request_id}")
        except Exception as exc:
            error_type, error = describe_exception(exc)
            self._complete_call(call, "failed", error_type, "tool.call.failed", logging.ERROR,
                                error_type=error_type, error=error)
            return error_result(f"Child MCP call failed. See Hub log request_id={request_id}.")
        error_summary = result_error_summary(result)
        if error_summary is not None:
            extra = {"error_summary": error_summary} if error_summary else {}
            self._complete_call(call, "failed", "ToolError", "tool.call.failed", logging.ERROR, **extra)
        else:
            event = "tool.call.cached" if cached else "tool.call.succeeded"
            self._complete_call(call, "succeeded", None, event, logging.INFO)
        return result

    async def _invoke_child(
        self, route: ToolRoute, arguments: dict[str, Any], options: ChildOptions | None = None,
    ) -> tuple[CallToolResult, bool]:
        """Reach the child, through the result cache for read-only tools with a TTL; returns (result, cached)."""
        name = route.exposed_name
        if route.cache_seconds > 0 and name in self._read_only:
            return await self.result_cache.get_or_call(
                name, arguments, route.cache_seconds, lambda: self._call_child(route, arguments, options),
                store_if=result_succeeded,
            )
        try:
            return await self._call_child(route, arguments, options), False
        finally:
            if name not in self._read_only:
                # A tool that may write makes this child's cached reads stale, whatever its outcome.
                self.result_cache.invalidate(self._server_tools(route.server_id))

    async def _call_child(
        self, route: ToolRoute, arguments: dict[str, Any], options: ChildOptions | None = None,
    ) -> CallToolResult:
        client = self.clients.get(route.server_id)
        if client is None:
            raise ChildUnavailable(route.server_id)
        with anyio.fail_after(self.config.servers[route.server_id].timeout_seconds):
            return await client.call_tool(route.tool_name, arguments, **_child_call_kwargs(options))

    def _server_tools(self, server_id: str) -> list[str]:
        return [name for name, route in self.routes.items() if route.server_id == server_id]


class ChildOptions(NamedTuple):
    """What a client's call adds for its child: a progress relay (None without a token) and the forwarded `_meta`."""

    progress: ProgressSender | None
    meta: dict[str, Any]


def _child_options(ctx: Any, params: CallToolRequestParams) -> ChildOptions:
    """The progress relay and the forwarded `_meta` of a client's tool call (FR-010)."""
    token = progress_token(params.meta)
    relay = None if token is None else progress_sender(ctx, token)
    return ChildOptions(relay, forwarded_meta(params.meta))


def _child_call_kwargs(options: ChildOptions | None) -> dict[str, Any]:
    """The optional arguments of the SDK's call_tool; a call without them looks as it always did."""
    kwargs: dict[str, Any] = {}
    if options is None:
        return kwargs
    if options.progress is not None:
        kwargs["progress_callback"] = options.progress
    if options.meta:
        kwargs["meta"] = options.meta
    return kwargs


def _in_list_order(routes: tuple[ToolRoute, ...], servers: Mapping[str, ServerConfig]) -> list[ToolRoute]:
    """Routes in `tools/list` order: each child in config order, then its tools by exposed name (FR-008)."""
    rank = {server_id: index for index, server_id in enumerate(servers)}
    return sorted(routes, key=lambda route: (rank.get(route.server_id, len(rank)), route.exposed_name))


def _tools_signature(parts: list[tuple[str, str, str]]) -> str:
    """One digest over each exposed tool's server, name and definition fingerprint, in server then name order."""
    return hashlib.sha256(json.dumps(sorted(parts), separators=(",", ":")).encode("utf-8")).hexdigest()


def _hub_status_tool() -> Tool:
    return Tool(
        name="hub__status",
        description="Show detected child MCPs and whether each is connected, disabled, or needs credentials.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        annotations={"readOnlyHint": True},
    )


def _public_tool(route: ToolRoute, child_tool: Tool) -> Tool:
    """Expose a child tool under its Hub name, forwarding its schemas and annotations unchanged."""
    description = child_tool.description or ""
    fields: dict[str, Any] = {
        "name": route.exposed_name,
        "description": f"[{route.feature} via {route.server_id}] {description}".strip(),
        "input_schema": policy.with_plan_id(child_tool.input_schema),
    }
    if getattr(child_tool, "title", None):
        fields["title"] = child_tool.title
    for field in ("output_schema", "annotations", "icons"):
        value = getattr(child_tool, field, None)
        if value is not None:
            fields[field] = value
    return Tool(**fields)


# Codex reads only the first 512 characters of the instructions: keep this summary within them.


# The client rules the instructions end with; a cut note names this file (relative to the Hub checkout).


def create_mcp_server(runtime: HubRuntime, job_runner: Any | None = None) -> Server:
    # Job tools are static: build them and their name set once, not on every request.
    job_tools = tuple(job_runner.tool_definitions()) if job_runner is not None else ()
    job_tool_names = frozenset(tool.name for tool in job_tools)

    async def list_tools(ctx: ServerRequestContext, params: PaginatedRequestParams | None) -> ListToolsResult:
        result = await runtime.list_tools(ctx, params)
        if not job_tools:
            return result
        return ListToolsResult(tools=[*result.tools, *job_tools])

    async def call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
        if params.name in job_tool_names:
            runtime.notify_client(ctx)
            return await job_runner.call_tool(params.name, params.arguments or {})
        return await runtime.call_tool(ctx, params)

    async def initialized(ctx: ServerRequestContext, _params: NotificationParams | None) -> None:
        runtime.notify_client(ctx)

    server = Server(
        "mcp-hub",
        instructions=server_instructions(runtime, job_runner is not None),
        cache_hints={"tools/list": CacheHint(ttl_ms=runtime.settings.protocol.tools_list_ttl_ms)},
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_prompts=runtime.list_prompts,
        on_subscriptions_listen=runtime.events.listen_handler,
        on_get_prompt=runtime.get_prompt,
    )
    # Names a client to observers as soon as it connects, before its first tool call.
    server.add_notification_handler("notifications/initialized", NotificationParams, initialized)
    return server


async def serve() -> None:
    audit = AuditLog()
    inputs = load_start_inputs(audit)
    runtime = HubRuntime(
        inputs.config, audit, inputs.settings, playbooks=inputs.playbooks, projects=inputs.projects,
        client_profiles=inputs.client_profiles, shared_service=False, pin_path=tool_pins_path(),
    )
    async with AsyncExitStack() as stack:
        await runtime.connect(stack)
        runtime.enable_core_condenser()
        server = create_mcp_server(runtime)
        audit.write(
            "hub.started",
            config=repo_relative(inputs.config.config_path),
            servers=list(inputs.config.servers),
            tool_count=len(runtime.public_tools),
        )
        try:
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
        finally:
            audit.write("hub.stopped")


def main() -> None:
    asyncio.run(serve())
