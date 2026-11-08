from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, TypeVar

from mcp.types import CallToolResult, TextContent, Tool

from ..agents.config import AGENT_ROLES, AgentConfig
from ..agents.coordinator import _findings_payload, synthesize
from ..agents.providers import (
    PACKAGE_MISSING_MESSAGE, PROVIDER_PACKAGES, AnthropicProvider, GeminiProvider, ModelProvider, OpenAIProvider,
    provider_package_installed,
)
from ..agents.specialists import (
    EvidenceRef, SpecialistExecutor, SpecialistResult, SpecialistError, ToolCallOutcome,
)
from ..audit import redact
from ..extensions import DraftSummarizer, PlanAssistant, PlanContext, ResultCondenser
from ..plan_merge import fold, parse_window, roles_for_servers
from ..result_cache import cache_key
from ..security.dpapi import WINDOWS_ONLY_MESSAGE
from ..server import is_read_only_tool
from ..settings import JobSettings
from .policy import SideEffectPolicy
from ..projects import ProjectConfig, validate_project_env
from .store import JobStore, TERMINAL_STATES


# Job limits (active and queued jobs, the job time window, child calls per job, the approval-code lifetime)
# come from settings.jobs. The coordinator's summary runs after the specialists finish or reach the job
# window, bounded by this.
COORDINATOR_SECONDS = 90
# The approval preview shows the full arguments up to this many characters, then marks the cut.
MAX_PREVIEW_CHARS = 64 * 1024
EVENT_PAGE_LIMIT = 100
# A restored approval wait is settled this long after its window ends, so the store clock has passed it.
_EXPIRY_MARGIN_SECONDS = 0.25
_TIME_LIMIT_NOTE = "Investigation reached the {limit} time limit; findings are partial."
_TIMED_OUT_LIMITATION = "Specialist stopped at the {limit} job time limit."
# Argument names whose values the approval preview masks; every other argument is shown unchanged.
_SECRET_ARGUMENT_RE = re.compile(
    r"(?i)(?:token|secret|password|passwd|pwd|passphrase|authorization|bearer|api[_-]?key|access[_-]?key"
    r"|private[_-]?key|credential|cookie|session[_-]?id)"
)
_SQL_RE = re.compile(r"\b(sql|database|db|table|query|stored procedure|bang|du lieu|co so du lieu)\b")
_UNKNOWN_TOOL = object()
_T = TypeVar("_T")

_DETERMINISTIC_TOOLS: dict[str, tuple[str, ...]] = {
    "logs": ("logs__get_openobserve_service_logs", "logs__search_openobserve_logs"),
    "sql": ("sql__search_context",),
}


def _build_deterministic_args(
    role: str, props: dict[str, Any], project: Any, request: dict[str, Any],
) -> dict[str, Any] | None:
    args: dict[str, Any] = {}
    if role == "logs":
        env = request.get("env", "")
        target = project.logs.get(env) if project and project.logs else None
        if target is None:
            return None
        if "organization" in props:
            args["organization"] = target.organization
        if "stream" in props:
            args["stream"] = target.stream
        if "env" in props:
            args["env"] = env
        if "start_time" in props and request.get("since"):
            args["start_time"] = request["since"]
        if "end_time" in props and request.get("until"):
            args["end_time"] = request["until"]
        if "query" in props:
            args["query"] = request.get("question", "")
    elif role == "sql":
        if "query" in props:
            args["query"] = request.get("question", "")
        if "env" in props:
            args["env"] = request.get("env", "")
    return args


_ENV = {"type": "string", "enum": ["dev", "stg", "prod"]}
_JOB_ID = {"job_id": {"type": "string"}}
# (name, description, properties, required, read-only hint) for each job tool the Hub exposes.
_JOB_TOOLS: tuple[tuple[str, str, dict[str, Any], tuple[str, ...], bool], ...] = (
    ("hub__list_projects", "List registered project aliases and their allowed environments without local roots.",
     {}, (), True),
    ("hub__submit_issue", "Start an asynchronous issue investigation using configured logs, source, Jira, and PR "
     "agents. At most {max_queued} jobs can wait in the queue.",
     {"project": {"type": "string"}, "question": {"type": "string"}, "env": _ENV,
      "issue_key": {"type": "string"}, "rcid": {"type": "string"},
      "since": {"type": "string", "format": "date-time"},
      "until": {"type": "string", "format": "date-time"}},
     ("project", "question"), False),
    ("hub__get_job", "Get investigation state, summary, evidence references, and metadata timeline.",
     _JOB_ID, ("job_id",), True),
    ("hub__get_job_detail", "Read one bounded page of a stored, encrypted tool result by job and source ID.",
     {**_JOB_ID, "source_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
      "limit": {"type": "integer", "minimum": 1, "maximum": 10000}},
     ("job_id", "source_id"), True),
    ("hub__provide_job_input", "Supply a validated environment to an investigation waiting for input.",
     {**_JOB_ID, "env": _ENV}, ("job_id", "env"), False),
    ("hub__approve_job", "Approve the exact pending write in a job using a one-time code shown in the dashboard.",
     {**_JOB_ID, "code": {"type": "string"}}, ("job_id", "code"), False),
    ("hub__cancel_job", "Cancel a queued, waiting, or running investigation.",
     _JOB_ID, ("job_id",), False),
)


def _object_schema(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = list(required)
    schema["additionalProperties"] = False
    return schema


def _limit_text(seconds: float) -> str:
    """`15-minute` for whole minutes, else `90-second`: the job window as the job's notes name it."""
    return f"{seconds / 60:g}-minute" if seconds >= 60 and seconds % 60 == 0 else f"{seconds:g}-second"


def create_provider(config: AgentConfig) -> ModelProvider:
    if not config.configured or not config.provider or not config.model or not config.api_key:
        raise ValueError("Agent is not configured")
    provider_types = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "gemini": GeminiProvider,
    }
    provider_type = provider_types.get(config.provider)
    if provider_type is None:
        raise ValueError("Unsupported provider")
    if not provider_package_installed(config.provider):
        raise ValueError(PACKAGE_MISSING_MESSAGE)
    return provider_type(config.model, config.api_key)


async def _db(function: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    """Run one store (SQLite/DPAPI) operation off the event loop."""
    return await asyncio.to_thread(function, *args, **kwargs)


class _JobSlot:
    """One job's hold on an active-job slot; released while the job waits for an approval."""

    def __init__(self, slots: asyncio.Semaphore) -> None:
        self._slots = slots
        self.held = False

    async def acquire(self) -> None:
        if not self.held:
            await self._slots.acquire()
            self.held = True

    def release(self) -> None:
        if self.held:
            self.held = False
            self._slots.release()


class JobRunner:
    def __init__(
        self,
        *,
        store: JobStore,
        runtime: Any,
        projects: dict[str, ProjectConfig],
        agents: dict[str, AgentConfig],
        provider_factory: Callable[[AgentConfig], ModelProvider] = create_provider,
        policy: SideEffectPolicy | None = None,
        limits: JobSettings = JobSettings(),
        plan_assistant: PlanAssistant | None = None,
        condenser: ResultCondenser | None = None,
        summarizer: DraftSummarizer | None = None,
        plan_timeout_seconds: float = 3.0,
        condense_min_chars: int = 6000,
        condense_timeout_seconds: float = 8.0,
    ) -> None:
        self.store = store
        self.limits = limits
        self.plan_assistant = plan_assistant
        self.condenser = condenser
        self.summarizer = summarizer
        self.plan_timeout_seconds = plan_timeout_seconds
        self.condense_min_chars = condense_min_chars
        self.condense_timeout_seconds = condense_timeout_seconds
        self._job_window = timedelta(seconds=limits.max_job_seconds)
        self.runtime = runtime
        self.projects = projects
        self.agents = agents
        audit = getattr(runtime, "audit", None)
        add_secrets = getattr(audit, "add_secrets", None)
        if callable(add_secrets):
            add_secrets(config.api_key for config in agents.values() if config and config.api_key)
        self.providers = _create_providers(agents, provider_factory)
        self.policy = policy or SideEffectPolicy()
        self._slots = asyncio.Semaphore(limits.max_active)
        self._submit_lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._timers: set[asyncio.Task[None]] = set()
        self._job_slots: dict[str, _JobSlot] = {}
        self._approval_locks: dict[str, asyncio.Lock] = {}
        self._approval_events: dict[str, asyncio.Event] = {}
        self._approval_codes: dict[str, str] = {}
        self._tool_counts: dict[str, int] = {}
        self._tool_count_lock = asyncio.Lock()
        # Per job: canonical (tool, arguments) key -> (source_id, result) of the first identical call.
        self._job_results: dict[str, dict[str, tuple[str, CallToolResult]]] = {}

    def tool_definitions(self) -> list[Tool]:
        return [
            Tool(
                name=name,
                description=description.format(max_queued=self.limits.max_queued),
                inputSchema=_object_schema(properties, required),
                annotations={"readOnlyHint": read_only},
            )
            for name, description, properties, required, read_only in _JOB_TOOLS
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None) -> CallToolResult:
        try:
            value = await self._dispatch(name, arguments or {})
        except (KeyError, ValueError) as exc:
            return CallToolResult(content=[TextContent(type="text", text=str(exc))], is_error=True)
        except Exception as exc:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Hub job request failed ({type(exc).__name__})")],
                is_error=True,
            )
        if value is _UNKNOWN_TOOL:
            return CallToolResult(content=[TextContent(type="text", text="Unknown Hub job tool")], is_error=True)
        return self._json(value)

    async def _dispatch(self, name: str, args: dict[str, Any]) -> Any:
        if name == "hub__list_projects":
            return self.list_projects()
        if name == "hub__submit_issue":
            return await self.submit_issue(**args)
        if name == "hub__get_job":
            return await _db(self.get_job, args["job_id"])
        if name == "hub__get_job_detail":
            return await _db(
                self.store.get_detail_page,
                args["job_id"], args["source_id"], offset=args.get("offset", 0), limit=args.get("limit", 2000),
            )
        if name == "hub__provide_job_input":
            return await self.provide_input(args["job_id"], args["env"])
        if name == "hub__approve_job":
            return await self.approve_job(args["job_id"], args["code"])
        if name == "hub__cancel_job":
            return await self.cancel_job(args["job_id"])
        return _UNKNOWN_TOOL

    async def submit_issue(
        self,
        *,
        project: str,
        question: str,
        env: str | None = None,
        issue_key: str | None = None,
        rcid: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, str]:
        self._require_encryption()
        project_config = self.projects.get(project)
        if project_config is None:
            raise ValueError("Project alias is not registered")
        if not isinstance(question, str) or not question.strip() or len(question) > 5000:
            raise ValueError("question must be non-empty and at most 5000 characters")
        candidate_env = env if env is not None else project_config.default_env
        selected_env = validate_project_env(project_config, candidate_env) if candidate_env is not None else None
        for label, value in (("issue_key", issue_key), ("rcid", rcid)):
            if value is not None and (not isinstance(value, str) or len(value) > 256):
                raise ValueError(f"{label} must be at most 256 characters")
        window = parse_window(since, until, self.limits.max_window_hours)
        since_iso = window[0].isoformat() if window else None
        until_iso = window[1].isoformat() if window else None
        state = "queued" if selected_env else "awaiting_input"
        async with self._submit_lock:
            if state == "queued":
                await self._ensure_queue_capacity()
            job_id = await _db(
                self.store.create_job,
                project=project, question=question, env=selected_env, issue_key=issue_key, rcid=rcid, state=state,
                since=since_iso, until=until_iso,
            )
        if state == "queued":
            self._schedule(job_id)
        return {"job_id": job_id, "status": state}

    async def _ensure_queue_capacity(self) -> None:
        queued = await _db(self.store.list_job_ids_by_state, "queued")
        if len(queued) >= self.limits.max_queued:
            raise ValueError(
                f"The investigation queue is full ({self.limits.max_queued} queued jobs); "
                "try again after a queued job starts or is cancelled"
            )

    async def provide_input(self, job_id: str, env: str) -> dict[str, str]:
        request = await _db(self.store.get_request, job_id)
        if (await _db(self.store.get_job, job_id))["state"] != "awaiting_input":
            raise ValueError("Job is not waiting for input")
        project = self.projects.get(request["project"])
        if project is None:
            raise ValueError("Project alias is no longer registered")
        selected_env = validate_project_env(project, env)
        async with self._submit_lock:
            await self._ensure_queue_capacity()
            await _db(self.store.update_request, job_id, env=selected_env)
            await _db(self.store.transition_job, job_id, "queued", event="job.input_received")
        self._schedule(job_id)
        return {"job_id": job_id, "status": "queued"}

    def get_job(self, job_id: str) -> dict[str, Any]:
        """Blocking: callers on the event loop run it via `asyncio.to_thread`."""
        job = self.store.get_job(job_id)
        job["events"] = self.store.list_events(job_id, limit=EVENT_PAGE_LIMIT)
        job["evidence_refs"] = self.store.list_evidence_refs(job_id)
        pending = self.store.get_pending_call(job_id)
        if pending:
            job["pending_approval"] = {"tool_name": pending["tool_name"], "expires_at": pending["expires_at"]}
        job["agents"] = self.agent_status()
        return job

    def pending_approval_preview(self, job_id: str) -> dict[str, Any] | None:
        """The full pending arguments as pretty JSON with only secret-like values masked. Blocking (decrypts)."""
        pending = self.store.get_pending_call(job_id, include_arguments=True)
        if not pending:
            return None
        text = json.dumps(
            _mask_secret_arguments(pending["arguments"], self._redact_preview_text),
            ensure_ascii=False, indent=2, default=str,
        )
        total = len(text)
        truncated = total > MAX_PREVIEW_CHARS
        if truncated:
            text = f"{text[:MAX_PREVIEW_CHARS]}\n... [truncated: first {MAX_PREVIEW_CHARS} of {total} characters shown]"
        return {
            "tool_name": pending["tool_name"],
            "agent": pending["agent"],
            "expires_at": pending["expires_at"],
            "arguments_preview": text,
            "truncated": truncated,
            "total_chars": total,
        }

    def _redact_preview_text(self, value: str) -> str:
        """Mask credential shapes (bearer, key=value, known token formats) and the configured secrets."""
        limit = len(value) * 32 + 64  # replacements can be longer than what they hide; never cut here
        text = redact(value, limit=limit)
        audit = getattr(self.runtime, "audit", None)
        return audit.redact_content(text, limit=limit) if audit is not None else text

    def list_jobs(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.store.list_jobs(limit=limit, offset=offset)

    def list_projects(self) -> list[dict[str, Any]]:
        return [
            {"alias": project.alias, "default_env": project.default_env, "allowed_envs": list(project.allowed_envs)}
            for project in sorted(self.projects.values(), key=lambda item: item.alias.casefold())
        ]

    def _require_encryption(self) -> None:
        """Refuse new jobs up front where the store cannot encrypt them (DPAPI exists only on Windows)."""
        if not self.store.encryption_available:
            raise ValueError(WINDOWS_ONLY_MESSAGE)

    def agent_status(self) -> dict[str, dict[str, str | bool | None]]:
        return {role: self._agent_status(role, config) for role, config in self.agents.items()}

    def _agent_status(self, role: str, config: AgentConfig) -> dict[str, str | bool | None]:
        """One role's readiness; `unavailable` names why a role cannot run (`reason` says it in English)."""
        status: dict[str, str | bool | None] = {
            "provider": config.provider,
            "model": config.model,
            "configured": bool(config.configured and role in self.providers),
            "unavailable": None,
            "reason": None,
        }
        if not self.store.encryption_available:
            status.update(configured=False, unavailable="windows-only", reason=WINDOWS_ONLY_MESSAGE)
        elif config.configured and config.provider in PROVIDER_PACKAGES and not provider_package_installed(config.provider):
            status.update(configured=False, unavailable="package-missing", reason=PACKAGE_MISSING_MESSAGE)
        return status

    async def approve_job(self, job_id: str, code: str) -> dict[str, Any]:
        approved = await self._approve_pending_call(job_id, code=code)
        return {"job_id": job_id, "approved": approved}

    async def approve_from_dashboard(self, job_id: str) -> bool:
        return await self._approve_pending_call(job_id, dashboard=True)

    async def _approve_pending_call(self, job_id: str, *, code: str | None = None, dashboard: bool = False) -> bool:
        live = job_id in self._approval_events
        pending = await _db(self._approve_in_store, job_id, code, dashboard, live)
        if pending is None:
            return False
        event = self._approval_events.get(job_id)
        task = self._tasks.get(job_id)
        if event is not None and task is not None and not task.done():
            event.set()
        elif task is None or task.done():
            # No investigation is waiting (for example after a restart): run only the stored operation.
            self._tasks[job_id] = asyncio.create_task(
                self._run_approved_write(job_id, pending), name=f"mcp-hub-approved-write-{job_id}",
            )
        return True

    def _approve_in_store(self, job_id: str, code: str | None, dashboard: bool, live: bool) -> dict[str, Any] | None:
        """Approve the pending call; return it, or None when it is missing, expired, or the code is wrong.

        A live wait is bounded by its running job; a wait restored after a restart expires
        once the job window since creation has passed.
        """
        pending = self.store.get_pending_call(job_id, include_arguments=True)
        if pending is None:
            return None
        if not live and self.store.expire_awaiting_approval(self._job_window, job_id=job_id):
            return None
        if not self.store.approve_pending_call(job_id, code, dashboard=dashboard):
            return None
        return pending

    async def _run_approved_write(self, job_id: str, expected: dict[str, Any]) -> None:
        slot = _JobSlot(self._slots)
        try:
            await slot.acquire()
            tool_name, arguments = await _db(self.store.consume_approved_call, job_id)
            if tool_name != expected["tool_name"] or arguments != expected["arguments"]:
                raise SpecialistError("Approved operation no longer matches its stored request")
            result = await self.runtime.call_child_tool(
                tool_name,
                arguments,
                request_id=f"job:{job_id}",
                client_name="investigation",
                agent_role=expected.get("agent"),
            )
            await _db(self._finish_approved_write, job_id, expected.get("agent"), tool_name, result)
        except asyncio.CancelledError:
            self._settle_interrupted(job_id)
            raise
        except Exception as exc:
            await _db(self._fail_approved_write, job_id, expected, exc)
        finally:
            slot.release()
            self._tasks.pop(job_id, None)
            self._approval_events.pop(job_id, None)
            self._approval_codes.pop(job_id, None)

    def _finish_approved_write(self, job_id: str, agent: str | None, tool_name: str, result: CallToolResult) -> None:
        source_id = "approved-write"
        self.store.append_event(
            job_id, "tool.call.failed" if result.is_error else "tool.call.succeeded",
            agent=agent, tool=tool_name, source_id=source_id,
        )
        self.store.save_encrypted_result(job_id, source_id, result.model_dump(mode="json"))
        outcome = "The approved write returned an error." if result.is_error else "The approved write completed."
        self.store.save_summary(job_id, f"{outcome} The investigation did not resume after the Hub restarted.")
        if self.store.get_job(job_id)["state"] == "running":
            self.store.transition_job(job_id, "partial", event="job.partial")

    def _fail_approved_write(self, job_id: str, expected: dict[str, Any], exc: Exception) -> None:
        self.store.append_event(job_id, "approved_write.failed", agent=expected.get("agent"), tool=expected.get("tool_name"))
        if self.store.get_job(job_id)["state"] == "running":
            self.store.save_summary(job_id, f"{type(exc).__name__}: approved write could not be completed.")
            self.store.transition_job(job_id, "partial", event="job.partial")

    def _settle_interrupted(self, job_id: str) -> None:
        """Mark a running job interrupted after its task was cancelled (shutdown or cancel)."""
        try:
            if self.store.get_job(job_id)["state"] == "running":
                self.store.transition_job(job_id, "interrupted", event="job.interrupted")
        except (KeyError, ValueError):
            pass

    def get_approval_code(self, job_id: str) -> str | None:
        pending = self.store.get_pending_call(job_id)
        if not pending or datetime.fromisoformat(pending["expires_at"]) <= self.store.clock():
            return None
        return self._approval_codes.get(job_id)

    async def cancel_job(self, job_id: str) -> dict[str, Any]:
        state = (await _db(self.store.get_job, job_id))["state"]
        if state in TERMINAL_STATES:
            return {"job_id": job_id, "cancelled": False, "status": state}
        await _db(self.store.transition_job, job_id, "cancelled", event="job.cancelled")
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        return {"job_id": job_id, "cancelled": True, "status": "cancelled"}

    def _schedule(self, job_id: str) -> None:
        task = asyncio.create_task(self._run_job(job_id), name=f"mcp-hub-job-{job_id}")
        self._tasks[job_id] = task

    def resume_queued_jobs(self) -> int:
        queued = self.store.list_job_ids_by_state("queued")
        for job_id in queued:
            if job_id not in self._tasks:
                self._schedule(job_id)
        return len(queued)

    def watch_pending_approvals(self) -> int:
        """At service start: settle approvals whose job window has passed and time out the younger ones.

        The younger ones stay approvable from the dashboard until their window ends; their MCP
        one-time codes were held in memory and do not survive the restart. Returns how many still wait.
        """
        self.store.expire_awaiting_approval(self._job_window)
        waiting = self.store.list_job_ids_by_state("awaiting_approval")
        now = self.store.clock()
        for job_id in waiting:
            created = datetime.fromisoformat(self.store.get_job(job_id)["created_at"])
            delay = max(0.0, (created + self._job_window - now).total_seconds()) + _EXPIRY_MARGIN_SECONDS
            timer = asyncio.create_task(self._expire_approval_later(job_id, delay), name=f"mcp-hub-approval-{job_id}")
            self._timers.add(timer)
            timer.add_done_callback(self._timers.discard)
        return len(waiting)

    async def _expire_approval_later(self, job_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        await _db(self.store.expire_awaiting_approval, self._job_window, job_id=job_id)

    async def close(self) -> None:
        tasks = [*self._tasks.values(), *self._timers]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def wait(self, job_id: str) -> None:
        task = self._tasks.get(job_id)
        if task:
            await task

    async def _run_job(self, job_id: str) -> None:
        # Everything after scheduling sits inside try/finally, so a job cancelled while it
        # still waits for a slot is forgotten too.
        slot = _JobSlot(self._slots)
        self._job_slots[job_id] = slot
        try:
            await slot.acquire()
            if await _db(self._start_if_queued, job_id):
                await self._execute_job(job_id)
        except asyncio.CancelledError:
            self._settle_interrupted(job_id)
            raise
        except Exception as exc:
            await _db(self._fail_job, job_id, exc)
        finally:
            slot.release()
            self._forget(job_id)

    def _start_if_queued(self, job_id: str) -> bool:
        if self.store.get_job(job_id)["state"] != "queued":
            return False
        self.store.transition_job(job_id, "running", event="job.started")
        return True

    def _fail_job(self, job_id: str, exc: Exception) -> None:
        self.store.append_event(job_id, "job.failed", agent=None, tool=type(exc).__name__)
        if self.store.get_job(job_id)["state"] == "running":
            self.store.transition_job(job_id, "failed", event="job.failed")

    async def _plan_job(self, job_id: str, request: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        if self.plan_assistant is None:
            await _db(self.store.append_event, job_id, "job.planned", agent=None, tool="off")
            return _roles_for(request), request

        hints = None
        source = "fallback"
        try:
            project = self.projects.get(request.get("project"))
            envs = tuple(project.allowed_envs) if project else ()
            routes = getattr(self.runtime, "routes", {})
            servers = tuple(sorted({getattr(r, "server_id", "") for r in routes.values()} - {""}))
            context = PlanContext(
                task=request.get("question", ""),
                playbooks={},
                servers=servers,
                envs=envs,
                now=self.store.clock(),
            )
            call_fn = getattr(self.plan_assistant, "hints", None) or getattr(self.plan_assistant, "assist_plan", None)
            hints = await asyncio.wait_for(
                call_fn(context),
                timeout=self.plan_timeout_seconds,
            )
            if hints and hints.source:
                source = hints.source
        except Exception:
            hints = None
            source = "fallback"

        await _db(self.store.append_event, job_id, "job.planned", agent=None, tool=source)
        if hints is None:
            return _roles_for(request), request

        hinted_roles = roles_for_servers(hints.servers, getattr(self.runtime, "routes", {}))
        allowed_roles = set(AGENT_ROLES) - {"coordinator"}
        roles: list[str] = []
        for role in [*_roles_for(request), *hinted_roles]:
            if role in allowed_roles and role not in roles:
                roles.append(role)

        req = dict(request)
        if req.get("since") is None and req.get("until") is None and hints.since is not None and hints.until is not None:
            req["since"] = hints.since.isoformat()
            req["until"] = hints.until.isoformat()
            await _db(self.store.update_request, job_id, since=req["since"], until=req["until"])
        return roles, req

    async def _execute_job(self, job_id: str) -> None:
        self._tool_counts[job_id] = 0
        request = await _db(self.store.get_request, job_id)
        roles, request = await self._plan_job(job_id, request)
        findings, incomplete, timed_out = await self._investigate(job_id, request, roles=roles)
        summary, synthesized = await self._summarize(request, findings, timed_out)
        final_state = "partial" if incomplete or timed_out or not synthesized else "completed"
        await _db(self._finish_job, job_id, redact(summary, limit=4000), final_state)

    def _finish_job(self, job_id: str, summary: str, final_state: str) -> None:
        self.store.save_summary(job_id, summary)
        state = self.store.get_job(job_id)["state"]
        if state == "awaiting_approval":
            final_state = "partial"  # the job window ended while a write still waited
        if state in {"running", "awaiting_approval"}:
            self.store.transition_job(job_id, final_state, event=f"job.{final_state}")

    def _forget(self, job_id: str) -> None:
        self._tool_counts.pop(job_id, None)
        self._job_results.pop(job_id, None)
        self._tasks.pop(job_id, None)
        self._job_slots.pop(job_id, None)
        self._approval_locks.pop(job_id, None)
        self._approval_events.pop(job_id, None)
        self._approval_codes.pop(job_id, None)

    async def _summarize(
        self, request: dict[str, Any], findings: list[SpecialistResult], timed_out: bool,
    ) -> tuple[str, bool]:
        """Return the coordinator summary, or a fallback and False when no coordinator could write one."""
        notes = [_TIME_LIMIT_NOTE.format(limit=_limit_text(self.limits.max_job_seconds))] if timed_out else []
        coordinator = self.providers.get("coordinator")
        if coordinator is None:
            if self.summarizer is not None:
                payload = _findings_payload(findings)
                draft = await self.summarizer.summarize(request["question"], payload)
                if draft is not None:
                    return "\n\n".join([draft, *notes]), False
            return _fallback_summary(findings, *notes, "Coordinator agent is not configured."), False
        audit = getattr(self.runtime, "audit", None)
        redactor = (lambda text: audit.redact_content(text, limit=12000)) if audit is not None else None
        try:
            async with asyncio.timeout(COORDINATOR_SECONDS):
                summary = await synthesize(
                    coordinator,
                    question=request["question"],
                    project=request["project"],
                    env=request["env"],
                    issue_key=request.get("issue_key"),
                    findings=findings,
                    redactor=redactor,
                )
        except Exception as exc:
            return _fallback_summary(findings, *notes, f"Coordinator failed ({type(exc).__name__})."), False
        return "\n\n".join([summary, *notes]), True

    async def _investigate(
        self, job_id: str, request: dict[str, Any], roles: list[str] | None = None,
    ) -> tuple[list[SpecialistResult], bool, bool]:
        """Run the specialists for at most the job window; return findings, incomplete, and timed out.

        A specialist still running at the limit is cancelled; it and any failed specialist keep
        the evidence they already stored.
        """
        specialist_roles = roles if roles is not None else _roles_for(request)
        tasks = {
            role: asyncio.create_task(self._run_specialist(job_id, role, request), name=f"mcp-hub-{role}-{job_id}")
            for role in specialist_roles
        }
        try:
            _done, pending = await asyncio.wait(tasks.values(), timeout=self.limits.max_job_seconds)
        except asyncio.CancelledError:
            await _cancel_all(tasks.values())
            raise
        await _cancel_all(pending)
        outcomes = [(role, _task_outcome(task, task in pending)) for role, task in tasks.items()]
        findings, incomplete = await _db(self._record_findings, job_id, outcomes)
        return findings, incomplete, bool(pending)

    async def _run_deterministic_specialist(
        self, job_id: str, role: str, request: dict[str, Any],
    ) -> SpecialistResult:
        candidates = _DETERMINISTIC_TOOLS.get(role, ())
        tool_name = next((name for name in candidates if name in self.runtime.public_tools), None)
        if tool_name is None:
            await _db(self.store.append_event, job_id, "agent.unavailable", agent=role)
            return SpecialistResult(role, "", (), "Agent is not configured.")

        tool = self.runtime.public_tools[tool_name]
        schema = (
            getattr(tool, "input_schema", None)
            or getattr(tool, "inputSchema", None)
            or (tool.get("input_schema") or tool.get("inputSchema") if isinstance(tool, dict) else {})
            or {}
        )
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        project = self.projects.get(request.get("project", ""))
        args = _build_deterministic_args(role, props, project, request)
        if args is None:
            await _db(self.store.append_event, job_id, "agent.unavailable", agent=role)
            return SpecialistResult(role, "", (), "No log targets configured for project.")

        await _db(self.store.append_event, job_id, "agent.started", agent=role)
        source_id = f"{role}-{uuid.uuid4().hex[:8]}"
        try:
            outcome = await self._call_child_tool(job_id, role, tool_name, args, tool, source_id)
            if isinstance(outcome, CallToolResult):
                outcome = ToolCallOutcome(outcome, source_id)
            if not outcome.reused:
                payload = outcome.result.model_dump(mode="json")
                await asyncio.to_thread(self.store.save_encrypted_result, job_id, outcome.source_id, payload)
            if not outcome.result.is_error and self.condenser is not None:
                raw_text = "\n".join(b.text for b in (outcome.result.content or []) if isinstance(b, TextContent))
                if len(raw_text) >= self.condense_min_chars:
                    try:
                        await asyncio.wait_for(
                            self.condenser.condense(raw_text, request.get("question")),
                            timeout=self.condense_timeout_seconds,
                        )
                    except Exception:
                        pass
            return SpecialistResult(role, "", (EvidenceRef(outcome.source_id, tool_name, role),), None)
        except Exception as exc:
            return SpecialistResult(role, "", (), f"Specialist failed ({type(exc).__name__}).")

    async def _run_specialist(self, job_id: str, role: str, request: dict[str, Any]) -> SpecialistResult:
        provider = self.providers.get(role)
        if provider is None:
            if self.summarizer is not None:
                return await self._run_deterministic_specialist(job_id, role, request)
            await _db(self.store.append_event, job_id, "agent.unavailable", agent=role)
            return SpecialistResult(role, "", (), "Agent is not configured.")
        executor = SpecialistExecutor(
            runtime=self.runtime,
            provider=provider,
            save_result=self.store.save_encrypted_result,
            call_tool=self._call_child_tool,
            tool_filter=self.policy.offers_tool,
            condenser=self.condenser,
            condense_min_chars=self.condense_min_chars,
            condense_timeout_seconds=self.condense_timeout_seconds,
        )
        await _db(self.store.append_event, job_id, "agent.started", agent=role)
        return await executor.run(job_id, role, _specialist_task(request))

    def _record_findings(
        self, job_id: str, outcomes: list[tuple[str, SpecialistResult | BaseException | None]],
    ) -> tuple[list[SpecialistResult], bool]:
        findings = [self._record_finding(job_id, role, outcome) for role, outcome in outcomes]
        return findings, any(finding.limitation for finding in findings)

    def _record_finding(self, job_id: str, role: str, outcome: SpecialistResult | BaseException | None) -> SpecialistResult:
        """Log the specialist's outcome; a failed or timed-out one keeps the evidence it stored."""
        if isinstance(outcome, SpecialistResult):
            self.store.append_event(job_id, "agent.completed", agent=role)
            return outcome
        refs = tuple(
            EvidenceRef(ref["source_id"], ref["tool"] or "", ref["agent"] or role)
            for ref in self.store.list_evidence_refs(job_id, agent=role)
        )
        if outcome is None:
            self.store.append_event(job_id, "agent.timed_out", agent=role)
            limitation = _TIMED_OUT_LIMITATION.format(limit=_limit_text(self.limits.max_job_seconds))
            return SpecialistResult(role, "", refs, limitation)
        self.store.append_event(job_id, "agent.failed", agent=role, tool=type(outcome).__name__)
        return SpecialistResult(role, "", refs, f"Specialist failed ({type(outcome).__name__}).")

    async def _call_child_tool(
        self,
        job_id: str,
        role: str,
        name: str,
        arguments: dict[str, Any],
        tool: Any,
        source_id: str,
    ) -> ToolCallOutcome:
        """Run one specialist tool call; an identical earlier call in the job is reused, not repeated."""
        key = cache_key(name, arguments)
        earlier = self._job_results.get(job_id, {}).get(key) if key is not None else None
        if earlier is not None:
            await _db(self.store.append_event, job_id, "tool.call.reused", agent=role, tool=name)
            return ToolCallOutcome(earlier[1].model_copy(deep=True), earlier[0], reused=True)
        result = await self._call_child_once(job_id, role, name, arguments, tool, source_id)
        if key is not None:
            self._job_results.setdefault(job_id, {})[key] = (source_id, result.model_copy(deep=True))
        return ToolCallOutcome(result, source_id)

    async def _call_child_once(
        self,
        job_id: str,
        role: str,
        name: str,
        arguments: dict[str, Any],
        tool: Any,
        source_id: str,
    ) -> CallToolResult:
        if not self.policy.offers_tool(tool):
            raise SpecialistError("This tool is not offered to investigation specialists")
        async with self._tool_count_lock:
            count = self._tool_counts.get(job_id, 0) + 1
            self._tool_counts[job_id] = count
        if count > self.limits.max_child_calls:
            raise SpecialistError(
                f"Job reached the {self.limits.max_child_calls} child-tool call limit; answer with the evidence you have"
            )
        if self.policy.requires_approval(name, read_only_hint=is_read_only_tool(tool), arguments=arguments):
            arguments = await self._await_approval(job_id, role, name, arguments)
        return await self._invoke_child(job_id, role, name, arguments, source_id)

    async def _invoke_child(
        self, job_id: str, role: str, name: str, arguments: dict[str, Any], source_id: str,
    ) -> CallToolResult:
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            result = await self.runtime.call_child_tool(
                name, arguments, request_id=f"job:{job_id}", client_name="investigation", agent_role=role,
            )
        except Exception:
            elapsed = round((loop.time() - started) * 1000)
            await _db(self.store.append_event, job_id, "tool.call.failed", agent=role, tool=name, duration_ms=elapsed)
            raise
        elapsed = round((loop.time() - started) * 1000)
        await _db(
            self.store.append_event,
            job_id, "tool.call.failed" if result.is_error else "tool.call.succeeded",
            agent=role, tool=name, source_id=source_id, duration_ms=elapsed,
        )
        return result

    async def _await_approval(self, job_id: str, role: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Hold a write until the dashboard (or its code) approves exactly this call; return the approved arguments.

        A job has one pending write at a time, and it gives up its active slot while it waits.
        """
        async with self._approval_locks.setdefault(job_id, asyncio.Lock()):
            # Registered before the pending call exists, so an approval can never miss this wait.
            event = asyncio.Event()
            self._approval_events[job_id] = event
            try:
                ttl = timedelta(minutes=self.limits.approval_code_minutes)
                code = await _db(self.store.create_pending_call, job_id, name, arguments, ttl=ttl, agent=role)
                self._approval_codes[job_id] = code
                await _db(self.store.append_event, job_id, "approval.code_issued", agent=role, tool=name)
                await self._wait_without_slot(job_id, event)
            finally:
                if self._approval_events.get(job_id) is event:
                    del self._approval_events[job_id]
            return await self._consume_approval(job_id, name, arguments)

    async def _wait_without_slot(self, job_id: str, event: asyncio.Event) -> None:
        slot = self._job_slots.get(job_id)
        if slot is not None:
            slot.release()
        await event.wait()
        if slot is not None:
            await slot.acquire()

    async def _consume_approval(self, job_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if (await _db(self.store.get_job, job_id))["state"] == "cancelled":
            raise asyncio.CancelledError
        try:
            approved_name, approved_args = await _db(self.store.consume_approved_call, job_id)
        except KeyError:
            raise SpecialistError("Pending write approval is no longer available") from None
        if approved_name != name or approved_args != arguments:
            raise SpecialistError("Approved operation does not match the pending tool call")
        return approved_args

    @staticmethod
    def _json(value: Any) -> CallToolResult:
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False, default=str))])


def _create_providers(
    agents: dict[str, AgentConfig], provider_factory: Callable[[AgentConfig], ModelProvider],
) -> dict[str, ModelProvider]:
    providers: dict[str, ModelProvider] = {}
    for role in AGENT_ROLES:
        config = agents.get(role)
        if config and config.configured:
            try:
                providers[role] = provider_factory(config)
            except Exception:
                # A broken optional role must not prevent direct tools or other roles from starting.
                continue
    return providers


def _roles_for(request: dict[str, Any]) -> list[str]:
    roles = ["logs", "source"]
    folded_question = fold(request["question"])
    if request.get("issue_key"):
        roles.append("jira")
    if request.get("issue_key") or "pull request" in folded_question or re.search(r"\bpr\b|pr_url", folded_question):
        roles.append("pr")
    if _SQL_RE.search(folded_question):
        roles.append("sql")
    return roles


def _specialist_task(request: dict[str, Any]) -> str:
    payload: dict[str, Any] = {
        "project": request["project"], "question": request["question"],
        "environment": request["env"], "issue_key": request.get("issue_key"),
        "rcid": request.get("rcid"),
    }
    if request.get("since") is not None and request.get("until") is not None:
        payload["window"] = {"since": request["since"], "until": request["until"]}
    return json.dumps(payload, ensure_ascii=False)


async def _cancel_all(tasks: Any) -> None:
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _task_outcome(task: asyncio.Task[SpecialistResult], timed_out: bool) -> SpecialistResult | BaseException | None:
    """The specialist's result, its exception, or None when it was stopped at the job time limit."""
    if timed_out or task.cancelled():
        return None
    return task.exception() or task.result()


def _fallback_summary(findings: list[SpecialistResult], *additional: str) -> str:
    sections = []
    for finding in findings:
        sources = " ".join(f"[{ref.source_id}]" for ref in finding.evidence_refs)
        if finding.summary:
            sections.append(f"{finding.role}: {finding.summary} {sources}".strip())
        elif sources:
            sections.append(f"{finding.role} evidence: {sources}")
        if finding.limitation:
            sections.append(f"{finding.role} limitation: {finding.limitation}")
    sections.extend(additional)
    return redact("\n".join(sections) or "No specialist evidence was collected.")


def _mask_secret_arguments(value: Any, redact_text: Callable[[str], str]) -> Any:
    """Mask values under secret-like keys and credentials inside strings; keep everything else readable."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_ARGUMENT_RE.search(str(key)) else _mask_secret_arguments(child, redact_text)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_mask_secret_arguments(item, redact_text) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
