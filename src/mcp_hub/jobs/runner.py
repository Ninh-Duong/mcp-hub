from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from functools import partial
from typing import Any
from collections.abc import Callable

from mcp.types import CallToolResult, TextContent, Tool

from ..agents.config import AGENT_ROLES, AgentConfig
from ..agents.providers import (
    PACKAGE_MISSING_MESSAGE, PROVIDER_PACKAGES, ModelProvider, provider_package_installed,
)
from ..audit import redact
from ..extensions import DraftSummarizer, PlanAssistant, PlanContext, ResultCondenser
from ..extensions import ProcessingObserver, processing_step
from ..planning.merge import parse_window
from ..agents.specialists import roles_for_servers
from .approvals import ApprovalMixin
from .common import JobSlot, store_call
from .investigation import InvestigationMixin, roles_for
from .tools import JOB_TOOLS, create_provider, create_providers, object_schema
from ..security.dpapi import WINDOWS_ONLY_MESSAGE
from ..settings import JobSettings
from .policy import SideEffectPolicy
from ..projects import ProjectConfig, validate_project_env
from .store import JobStore, TERMINAL_STATES


# Job limits (active and queued jobs, the job time window, child calls per job, the approval-code lifetime, the
# coordinator's time, the specialists' rounds and result size, the prompt cache) come from settings.jobs, passed
# to JobRunner as `limits`.
EVENT_PAGE_LIMIT = 100
# A restored approval wait is settled this long after its window ends, so the store clock has passed it.
_UNKNOWN_TOOL = object()


# (name, description, properties, required, read-only hint) for each job tool the Hub exposes.


_DEFAULT_LIMITS = JobSettings()


class JobRunner(ApprovalMixin, InvestigationMixin):
    def __init__(
        self,
        *,
        store: JobStore,
        runtime: Any,
        projects: dict[str, ProjectConfig],
        agents: dict[str, AgentConfig],
        provider_factory: Callable[[AgentConfig], ModelProvider] | None = None,
        policy: SideEffectPolicy | None = None,
        limits: JobSettings = _DEFAULT_LIMITS,
        plan_assistant: PlanAssistant | None = None,
        condenser: ResultCondenser | None = None,
        summarizer: DraftSummarizer | None = None,
        processing_observer: ProcessingObserver | None = None,
        plan_timeout_seconds: float = 3.0,
        condense_min_chars: int = 9000,
        condense_timeout_seconds: float = 8.0,
    ) -> None:
        self.store = store
        self.limits = limits
        self.plan_assistant = plan_assistant
        self.condenser = condenser
        self.summarizer = summarizer
        self.processing_observer = processing_observer
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
        factory = provider_factory or partial(create_provider, prompt_cache=limits.prompt_cache)
        self.providers = create_providers(agents, factory)
        self.policy = policy or SideEffectPolicy()
        self._slots = asyncio.Semaphore(limits.max_active)
        self._submit_lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._timers: set[asyncio.Task[None]] = set()
        self._job_slots: dict[str, JobSlot] = {}
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
                inputSchema=object_schema(properties, required),
                annotations={"readOnlyHint": read_only},
            )
            for name, description, properties, required, read_only in JOB_TOOLS
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
            return await store_call(self.get_job, args["job_id"])
        if name == "hub__get_job_detail":
            return await store_call(
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
            job_id = await store_call(
                self.store.create_job,
                project=project, question=question, env=selected_env, issue_key=issue_key, rcid=rcid, state=state,
                since=since_iso, until=until_iso,
            )
        if state == "queued":
            self._schedule(job_id)
        return {"job_id": job_id, "status": state}

    async def _ensure_queue_capacity(self) -> None:
        queued = await store_call(self.store.list_job_ids_by_state, "queued")
        if len(queued) >= self.limits.max_queued:
            raise ValueError(
                f"The investigation queue is full ({self.limits.max_queued} queued jobs); "
                "try again after a queued job starts or is cancelled"
            )

    async def provide_input(self, job_id: str, env: str) -> dict[str, str]:
        request = await store_call(self.store.get_request, job_id)
        if (await store_call(self.store.get_job, job_id))["state"] != "awaiting_input":
            raise ValueError("Job is not waiting for input")
        project = self.projects.get(request["project"])
        if project is None:
            raise ValueError("Project alias is no longer registered")
        selected_env = validate_project_env(project, env)
        async with self._submit_lock:
            await self._ensure_queue_capacity()
            await store_call(self.store.update_request, job_id, env=selected_env)
            await store_call(self.store.transition_job, job_id, "queued", event="job.input_received")
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

    def list_jobs(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.store.list_jobs(limit=limit, offset=offset)

    def list_projects(self) -> list[dict[str, Any]]:
        return [self._project_entry(project) for project in sorted(self.projects.values(), key=lambda item: item.alias.casefold())]

    def _project_entry(self, project: ProjectConfig) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "alias": project.alias,
            "default_env": project.default_env,
            "allowed_envs": list(project.allowed_envs),
        }
        if project.sql:
            entry["sql"] = {
                env: {"default_database": target.default_database, "databases": list(target.databases)}
                for env, target in project.sql.items()
            }
        return entry

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


    def _settle_interrupted(self, job_id: str) -> None:
        """Mark a running job interrupted after its task was cancelled (shutdown or cancel)."""
        try:
            if self.store.get_job(job_id)["state"] == "running":
                self.store.transition_job(job_id, "interrupted", event="job.interrupted")
        except (KeyError, ValueError):
            pass

    async def cancel_job(self, job_id: str) -> dict[str, Any]:
        state = (await store_call(self.store.get_job, job_id))["state"]
        if state in TERMINAL_STATES:
            return {"job_id": job_id, "cancelled": False, "status": state}
        await store_call(self.store.transition_job, job_id, "cancelled", event="job.cancelled")
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
        slot = JobSlot(self._slots)
        self._job_slots[job_id] = slot
        try:
            await slot.acquire()
            if await store_call(self._start_if_queued, job_id):
                await self._execute_job(job_id)
        except asyncio.CancelledError:
            # The store write runs on a worker thread; shield keeps it going if the task is cancelled again.
            await asyncio.shield(store_call(self._settle_interrupted, job_id))
            raise
        except Exception as exc:
            await store_call(self._fail_job, job_id, exc)
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
            await store_call(self.store.append_event, job_id, "job.planned", agent=None, tool="off")
            return roles_for(request), request

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
            hints = await processing_step(self.processing_observer, "job_plan", asyncio.wait_for(
                call_fn(context),
                timeout=self.plan_timeout_seconds,
            ))
            if hints and hints.source:
                source = hints.source
        except Exception:
            hints = None
            source = "fallback"

        await store_call(self.store.append_event, job_id, "job.planned", agent=None, tool=source)
        if hints is None:
            return roles_for(request), request

        hinted_roles = roles_for_servers(hints.servers, getattr(self.runtime, "routes", {}))
        allowed_roles = set(AGENT_ROLES) - {"coordinator"}
        roles: list[str] = []
        for role in [*roles_for(request), *hinted_roles]:
            if role in allowed_roles and role not in roles:
                roles.append(role)

        req = dict(request)
        if req.get("since") is None and req.get("until") is None and hints.since is not None and hints.until is not None:
            req["since"] = hints.since.isoformat()
            req["until"] = hints.until.isoformat()
            await store_call(self.store.update_request, job_id, since=req["since"], until=req["until"])
        return roles, req

    async def _execute_job(self, job_id: str) -> None:
        self._tool_counts[job_id] = 0
        request = await store_call(self.store.get_request, job_id)
        roles, request = await self._plan_job(job_id, request)
        findings, incomplete, timed_out = await self._investigate(job_id, request, roles=roles)
        summary, synthesized = await self._summarize(request, findings, timed_out)
        final_state = "partial" if incomplete or timed_out or not synthesized else "completed"
        await store_call(self._finish_job, job_id, redact(summary, limit=4000), final_state)

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


    @staticmethod
    def _json(value: Any) -> CallToolResult:
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False, default=str))])

