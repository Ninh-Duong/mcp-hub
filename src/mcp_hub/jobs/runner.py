from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Any, Callable, TypeVar

from mcp.types import CallToolResult, TextContent, Tool

from ..agents.config import AGENT_ROLES, AgentConfig
from ..agents.coordinator import synthesize
from ..agents.providers import (
    PACKAGE_MISSING_MESSAGE, PROVIDER_PACKAGES, AnthropicProvider, GeminiProvider, ModelProvider, OpenAIProvider,
    provider_package_installed,
)
from ..agents.specialists import (
    EvidenceRef, SpecialistExecutor, SpecialistResult, SpecialistError, ToolCallOutcome, is_read_only_tool,
)
from ..audit import redact
from ..result_cache import cache_key
from ..security.dpapi import WINDOWS_ONLY_MESSAGE
from .policy import SideEffectPolicy
from .projects import ProjectConfig, validate_project_env
from .store import JobStore, TERMINAL_STATES


MAX_ACTIVE_JOBS = 2
MAX_QUEUED_JOBS = 20
MAX_JOB_SECONDS = 15 * 60
# The coordinator's summary runs after the specialists finish or reach MAX_JOB_SECONDS, bounded by this.
COORDINATOR_SECONDS = 90
MAX_CHILD_CALLS = 30
# The approval preview shows the full arguments up to this many characters, then marks the cut.
MAX_PREVIEW_CHARS = 64 * 1024
EVENT_PAGE_LIMIT = 100
# A restored approval wait is settled this long after its window ends, so the store clock has passed it.
_EXPIRY_MARGIN_SECONDS = 0.25
_JOB_WINDOW = timedelta(seconds=MAX_JOB_SECONDS)
_TIME_LIMIT_NOTE = "Investigation reached the 15-minute time limit; findings are partial."
_TIMED_OUT_LIMITATION = "Specialist stopped at the 15-minute job time limit."
# Argument names whose values the approval preview masks; every other argument is shown unchanged.
_SECRET_ARGUMENT_RE = re.compile(
    r"(?i)(?:token|secret|password|passwd|pwd|passphrase|authorization|bearer|api[_-]?key|access[_-]?key"
    r"|private[_-]?key|credential|cookie|session[_-]?id)"
)
_UNKNOWN_TOOL = object()
_T = TypeVar("_T")


_ENV = {"type": "string", "enum": ["dev", "stg", "prod"]}
_JOB_ID = {"job_id": {"type": "string"}}
# (name, description, properties, required, read-only hint) for each job tool the Hub exposes.
_JOB_TOOLS: tuple[tuple[str, str, dict[str, Any], tuple[str, ...], bool], ...] = (
    ("hub__list_projects", "List registered project aliases and their allowed environments without local roots.",
     {}, (), True),
    ("hub__submit_issue", "Start an asynchronous issue investigation using configured logs, source, Jira, and PR "
     f"agents. At most {MAX_QUEUED_JOBS} jobs can wait in the queue.",
     {"project": {"type": "string"}, "question": {"type": "string"}, "env": _ENV,
      "issue_key": {"type": "string"}, "rcid": {"type": "string"}},
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
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.projects = projects
        self.agents = agents
        audit = getattr(runtime, "audit", None)
        add_secrets = getattr(audit, "add_secrets", None)
        if callable(add_secrets):
            add_secrets(config.api_key for config in agents.values() if config and config.api_key)
        self.providers = _create_providers(agents, provider_factory)
        self.policy = policy or SideEffectPolicy()
        self._slots = asyncio.Semaphore(MAX_ACTIVE_JOBS)
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

    @staticmethod
    def tool_definitions() -> list[Tool]:
        return [
            Tool(
                name=name,
                description=description,
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
        state = "queued" if selected_env else "awaiting_input"
        async with self._submit_lock:
            if state == "queued":
                await self._ensure_queue_capacity()
            job_id = await _db(
                self.store.create_job,
                project=project, question=question, env=selected_env, issue_key=issue_key, rcid=rcid, state=state,
            )
        if state == "queued":
            self._schedule(job_id)
        return {"job_id": job_id, "status": state}

    async def _ensure_queue_capacity(self) -> None:
        queued = await _db(self.store.list_job_ids_by_state, "queued")
        if len(queued) >= MAX_QUEUED_JOBS:
            raise ValueError(
                f"The investigation queue is full ({MAX_QUEUED_JOBS} queued jobs); "
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
        if not live and self.store.expire_awaiting_approval(_JOB_WINDOW, job_id=job_id):
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
        self.store.expire_awaiting_approval(_JOB_WINDOW)
        waiting = self.store.list_job_ids_by_state("awaiting_approval")
        now = self.store.clock()
        for job_id in waiting:
            created = datetime.fromisoformat(self.store.get_job(job_id)["created_at"])
            delay = max(0.0, (created + _JOB_WINDOW - now).total_seconds()) + _EXPIRY_MARGIN_SECONDS
            timer = asyncio.create_task(self._expire_approval_later(job_id, delay), name=f"mcp-hub-approval-{job_id}")
            self._timers.add(timer)
            timer.add_done_callback(self._timers.discard)
        return len(waiting)

    async def _expire_approval_later(self, job_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        await _db(self.store.expire_awaiting_approval, _JOB_WINDOW, job_id=job_id)

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

    async def _execute_job(self, job_id: str) -> None:
        self._tool_counts[job_id] = 0
        request = await _db(self.store.get_request, job_id)
        findings, incomplete, timed_out = await self._investigate(job_id, request)
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
        notes = [_TIME_LIMIT_NOTE] if timed_out else []
        coordinator = self.providers.get("coordinator")
        if coordinator is None:
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

    async def _investigate(self, job_id: str, request: dict[str, Any]) -> tuple[list[SpecialistResult], bool, bool]:
        """Run the specialists for at most MAX_JOB_SECONDS; return findings, incomplete, and timed out.

        A specialist still running at the limit is cancelled; it and any failed specialist keep
        the evidence they already stored.
        """
        tasks = {
            role: asyncio.create_task(self._run_specialist(job_id, role, request), name=f"mcp-hub-{role}-{job_id}")
            for role in _roles_for(request)
        }
        try:
            _done, pending = await asyncio.wait(tasks.values(), timeout=MAX_JOB_SECONDS)
        except asyncio.CancelledError:
            await _cancel_all(tasks.values())
            raise
        await _cancel_all(pending)
        outcomes = [(role, _task_outcome(task, task in pending)) for role, task in tasks.items()]
        findings, incomplete = await _db(self._record_findings, job_id, outcomes)
        return findings, incomplete, bool(pending)

    async def _run_specialist(self, job_id: str, role: str, request: dict[str, Any]) -> SpecialistResult:
        provider = self.providers.get(role)
        if provider is None:
            await _db(self.store.append_event, job_id, "agent.unavailable", agent=role)
            return SpecialistResult(role, "", (), "Agent is not configured.")
        executor = SpecialistExecutor(
            runtime=self.runtime,
            provider=provider,
            save_result=self.store.save_encrypted_result,
            call_tool=self._call_child_tool,
            tool_filter=self.policy.offers_tool,
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
            return SpecialistResult(role, "", refs, _TIMED_OUT_LIMITATION)
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
        if count > MAX_CHILD_CALLS:
            raise SpecialistError(f"Job reached the {MAX_CHILD_CALLS} child-tool call limit; answer with the evidence you have")
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
                code = await _db(self.store.create_pending_call, job_id, name, arguments, agent=role)
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
    question = request["question"].casefold()
    if request.get("issue_key"):
        roles.append("jira")
    if request.get("issue_key") or "pull request" in question or re.search(r"\bpr\b|pr_url", question):
        roles.append("pr")
    return roles


def _specialist_task(request: dict[str, Any]) -> str:
    return json.dumps({
        "project": request["project"], "question": request["question"],
        "environment": request["env"], "issue_key": request.get("issue_key"),
        "rcid": request.get("rcid"),
    }, ensure_ascii=False)


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
