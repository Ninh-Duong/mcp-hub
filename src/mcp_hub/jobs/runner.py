from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from mcp.types import CallToolResult, TextContent, Tool

from ..agents.config import AGENT_ROLES, AgentConfig
from ..agents.coordinator import synthesize
from ..agents.providers import AnthropicProvider, GeminiProvider, ModelProvider, OpenAIProvider
from ..agents.specialists import (
    SpecialistExecutor, SpecialistResult, SpecialistError, ToolCallOutcome, minimize_model_content,
)
from ..audit import SENSITIVE_FIELD_RE, redact
from ..result_cache import cache_key
from .policy import SideEffectPolicy
from .projects import ProjectConfig, validate_project_env
from .store import JobStore, TERMINAL_STATES


MAX_ACTIVE_JOBS = 2
MAX_JOB_SECONDS = 15 * 60
MAX_CHILD_CALLS = 30


_ENV = {"type": "string", "enum": ["dev", "stg", "prod"]}
_JOB_ID = {"job_id": {"type": "string"}}
# (name, description, properties, required, read-only hint) for each job tool the Hub exposes.
_JOB_TOOLS: tuple[tuple[str, str, dict[str, Any], tuple[str, ...], bool], ...] = (
    ("hub__list_projects", "List registered project aliases and their allowed environments without local roots.",
     {}, (), True),
    ("hub__submit_issue", "Start an asynchronous issue investigation using configured logs, source, Jira, and PR agents.",
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
    return provider_type(config.model, config.api_key)


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
            add_secrets(
                config.api_key for config in agents.values() if config and config.api_key
            )
        self.providers: dict[str, ModelProvider] = {}
        for role in AGENT_ROLES:
            config = agents.get(role)
            if config and config.configured:
                try:
                    self.providers[role] = provider_factory(config)
                except Exception:
                    # A broken optional role must not prevent direct tools or other roles from starting.
                    continue
        self.policy = policy or SideEffectPolicy()
        self._slots = asyncio.Semaphore(MAX_ACTIVE_JOBS)
        self._tasks: dict[str, asyncio.Task[None]] = {}
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
        args = arguments or {}
        try:
            if name == "hub__list_projects":
                return self._json(self.list_projects())
            if name == "hub__submit_issue":
                return self._json(await self.submit_issue(**args))
            if name == "hub__get_job":
                return self._json(self.get_job(args["job_id"]))
            if name == "hub__get_job_detail":
                return self._json(self.store.get_detail_page(
                    args["job_id"], args["source_id"], offset=args.get("offset", 0), limit=args.get("limit", 2000),
                ))
            if name == "hub__provide_job_input":
                return self._json(await self.provide_input(args["job_id"], args["env"]))
            if name == "hub__approve_job":
                return self._json(self.approve_job(args["job_id"], args["code"]))
            if name == "hub__cancel_job":
                return self._json(self.cancel_job(args["job_id"]))
        except (KeyError, ValueError) as exc:
            return CallToolResult(content=[TextContent(type="text", text=str(exc))], is_error=True)
        except Exception as exc:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Hub job request failed ({type(exc).__name__})")],
                is_error=True,
            )
        return CallToolResult(content=[TextContent(type="text", text="Unknown Hub job tool")], is_error=True)

    async def submit_issue(
        self,
        *,
        project: str,
        question: str,
        env: str | None = None,
        issue_key: str | None = None,
        rcid: str | None = None,
    ) -> dict[str, str]:
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
        job_id = self.store.create_job(
            project=project, question=question, env=selected_env, issue_key=issue_key, rcid=rcid, state=state,
        )
        if state == "queued":
            self._schedule(job_id)
        return {"job_id": job_id, "status": state}

    async def provide_input(self, job_id: str, env: str) -> dict[str, str]:
        request = self.store.get_request(job_id)
        if self.store.get_job(job_id)["state"] != "awaiting_input":
            raise ValueError("Job is not waiting for input")
        project = self.projects.get(request["project"])
        if project is None:
            raise ValueError("Project alias is no longer registered")
        selected_env = validate_project_env(project, env)
        self.store.update_request(job_id, env=selected_env)
        self.store.transition_job(job_id, "queued", event="job.input_received")
        self._schedule(job_id)
        return {"job_id": job_id, "status": "queued"}

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        events = self.store.list_events(job_id)
        job["events"] = events
        job["evidence_refs"] = [
            {"source_id": event["source_id"], "tool": event["tool"], "agent": event["agent"]}
            for event in events if event.get("source_id")
        ]
        pending = self.store.get_pending_call(job_id)
        if pending:
            job["pending_approval"] = {"tool_name": pending["tool_name"], "expires_at": pending["expires_at"]}
        job["agents"] = self.agent_status()
        return job

    def pending_approval_preview(self, job_id: str) -> dict[str, Any] | None:
        pending = self.store.get_pending_call(job_id, include_arguments=True)
        if not pending:
            return None
        safe_arguments = json.dumps(
            _redact_approval_arguments(
                pending["arguments"],
                lambda text: minimize_model_content(
                    text,
                    limit=2000,
                    redactor=lambda value: self.runtime.audit.redact_content(value, limit=2000),
                ),
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        )[:2000]
        return {
            "tool_name": pending["tool_name"],
            "expires_at": pending["expires_at"],
            "arguments_preview": safe_arguments,
        }

    def list_jobs(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.store.list_jobs(limit=limit, offset=offset)

    def list_projects(self) -> list[dict[str, Any]]:
        return [
            {"alias": project.alias, "default_env": project.default_env, "allowed_envs": list(project.allowed_envs)}
            for project in sorted(self.projects.values(), key=lambda item: item.alias.casefold())
        ]

    def agent_status(self) -> dict[str, dict[str, str | bool | None]]:
        return {
            role: {
                "provider": config.provider,
                "model": config.model,
                "configured": bool(config.configured and role in self.providers),
            }
            for role, config in self.agents.items()
        }

    def approve_job(self, job_id: str, code: str) -> dict[str, Any]:
        approved = self._approve_pending_call(job_id, code=code)
        return {"job_id": job_id, "approved": approved}

    def approve_from_dashboard(self, job_id: str) -> bool:
        return self._approve_pending_call(job_id, dashboard=True)

    def _approve_pending_call(self, job_id: str, *, code: str | None = None, dashboard: bool = False) -> bool:
        pending = self.store.get_pending_call(job_id, include_arguments=True)
        if pending is None:
            return False
        job = self.store.get_job(job_id)
        created_at = datetime.fromisoformat(job["created_at"])
        if (datetime.now(timezone.utc) - created_at).total_seconds() > MAX_JOB_SECONDS:
            if job["state"] == "awaiting_approval":
                self.store.transition_job(job_id, "partial", event="approval.expired")
            return False
        approved = self.store.approve_pending_call(job_id, code, dashboard=dashboard)
        if not approved:
            return False
        event = self._approval_events.get(job_id)
        task = self._tasks.get(job_id)
        if event is not None and task is not None and not task.done():
            event.set()
        else:
            task = asyncio.create_task(
                self._run_approved_write(job_id, pending), name=f"mcp-hub-approved-write-{job_id}",
            )
            self._tasks[job_id] = task
        return True

    async def _run_approved_write(self, job_id: str, expected: dict[str, Any]) -> None:
        source_id = "approved-write"
        async with self._slots:
            try:
                tool_name, arguments = self.store.consume_approved_call(job_id)
                if tool_name != expected["tool_name"] or arguments != expected["arguments"]:
                    raise SpecialistError("Approved operation no longer matches its stored request")
                result = await self.runtime.call_child_tool(
                    tool_name,
                    arguments,
                    request_id=f"job:{job_id}",
                    client_name="investigation",
                    agent_role=expected.get("agent"),
                )
                self.store.append_event(
                    job_id,
                    "tool.call.failed" if result.is_error else "tool.call.succeeded",
                    agent=expected.get("agent"), tool=tool_name, source_id=source_id,
                )
                self.store.save_encrypted_result(job_id, source_id, result.model_dump(mode="json"))
                outcome = "The approved write returned an error." if result.is_error else "The approved write completed."
                self.store.save_summary(
                    job_id, f"{outcome} The investigation did not resume after the Hub restarted."
                )
                if self.store.get_job(job_id)["state"] == "running":
                    self.store.transition_job(job_id, "partial", event="job.partial")
            except asyncio.CancelledError:
                if self.store.get_job(job_id)["state"] == "running":
                    self.store.transition_job(job_id, "interrupted", event="job.interrupted")
                raise
            except Exception as exc:
                self.store.append_event(job_id, "approved_write.failed", agent=expected.get("agent"), tool=expected.get("tool_name"))
                if self.store.get_job(job_id)["state"] == "running":
                    self.store.save_summary(job_id, f"{type(exc).__name__}: approved write could not be completed.")
                    self.store.transition_job(job_id, "partial", event="job.partial")
            finally:
                self._tasks.pop(job_id, None)
                self._approval_events.pop(job_id, None)
                self._approval_codes.pop(job_id, None)

    def get_approval_code(self, job_id: str) -> str | None:
        pending = self.store.get_pending_call(job_id)
        if not pending or datetime.fromisoformat(pending["expires_at"]) <= datetime.now(timezone.utc):
            return None
        return self._approval_codes.get(job_id)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        state = self.store.get_job(job_id)["state"]
        if state in TERMINAL_STATES:
            return {"job_id": job_id, "cancelled": False, "status": state}
        self.store.transition_job(job_id, "cancelled", event="job.cancelled")
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
        tasks = list(self._tasks.values())
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
        async with self._slots:
            if self.store.get_job(job_id)["state"] != "queued":
                return
            self.store.transition_job(job_id, "running", event="job.started")
            self._tool_counts[job_id] = 0
            try:
                async with asyncio.timeout(MAX_JOB_SECONDS):
                    findings, incomplete = await self._investigate(job_id)
                summary, synthesized = await self._summarize(job_id, findings)
                self.store.save_summary(job_id, redact(summary, limit=4000))
                final_state = "partial" if incomplete or not synthesized else "completed"
                self.store.transition_job(job_id, final_state, event=f"job.{final_state}")
            except asyncio.CancelledError:
                if self.store.get_job(job_id)["state"] == "running":
                    self.store.transition_job(job_id, "interrupted", event="job.interrupted")
                raise
            except TimeoutError:
                self.store.save_summary(job_id, _fallback_summary([], "Investigation reached the 15-minute time limit."))
                if self.store.get_job(job_id)["state"] in {"running", "awaiting_approval"}:
                    self.store.transition_job(job_id, "partial", event="job.partial")
            except Exception as exc:
                self.store.append_event(job_id, "job.failed", agent=None, tool=type(exc).__name__)
                if self.store.get_job(job_id)["state"] == "running":
                    self.store.transition_job(job_id, "failed", event="job.failed")
            finally:
                self._forget(job_id)

    def _forget(self, job_id: str) -> None:
        self._tool_counts.pop(job_id, None)
        self._job_results.pop(job_id, None)
        self._tasks.pop(job_id, None)
        self._approval_events.pop(job_id, None)
        self._approval_codes.pop(job_id, None)

    async def _summarize(self, job_id: str, findings: list[SpecialistResult]) -> tuple[str, bool]:
        """Return the coordinator summary, or a fallback and False when no coordinator could write one."""
        request = self.store.get_request(job_id)
        coordinator = self.providers.get("coordinator")
        if coordinator is None:
            return _fallback_summary(findings, "Coordinator agent is not configured."), False
        audit = getattr(self.runtime, "audit", None)
        redactor = (lambda text: audit.redact_content(text, limit=12000)) if audit is not None else None
        try:
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
            return _fallback_summary(findings, f"Coordinator failed ({type(exc).__name__})."), False
        return summary, True

    async def _investigate(self, job_id: str) -> tuple[list[SpecialistResult], bool]:
        request = self.store.get_request(job_id)
        roles = ["logs", "source"]
        if request.get("issue_key"):
            roles.append("jira")
        if request.get("issue_key") or "pull request" in request["question"].casefold() or re.search(
            r"\bpr\b|pr_url", request["question"].casefold(),
        ):
            roles.append("pr")
        tasks = []
        for role in roles:
            provider = self.providers.get(role)
            executor = SpecialistExecutor(
                runtime=self.runtime,
                provider=provider,
                save_result=self.store.save_encrypted_result,
                call_tool=self._call_child_tool,
            ) if provider else None
            if executor is None:
                self.store.append_event(job_id, "agent.unavailable", agent=role)
                tasks.append(asyncio.sleep(0, result=SpecialistResult(role, "", (), "Agent is not configured.")))
                continue
            task = json.dumps({
                "project": request["project"], "question": request["question"],
                "environment": request["env"], "issue_key": request.get("issue_key"),
                "rcid": request.get("rcid"),
            }, ensure_ascii=False)
            self.store.append_event(job_id, "agent.started", agent=role)
            tasks.append(executor.run(job_id, role, task))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        findings: list[SpecialistResult] = []
        incomplete = False
        for role, result in zip(roles, results):
            if isinstance(result, BaseException):
                incomplete = True
                self.store.append_event(job_id, "agent.failed", agent=role, tool=type(result).__name__)
                findings.append(SpecialistResult(role, "", (), f"Specialist failed ({type(result).__name__})."))
            else:
                findings.append(result)
                if result.limitation:
                    incomplete = True
                self.store.append_event(job_id, "agent.completed", agent=role)
        return findings, incomplete

    async def _call_child_tool(
        self,
        job_id: str,
        role: str,
        name: str,
        arguments: dict[str, Any],
        tool: Tool,
        source_id: str,
    ) -> ToolCallOutcome:
        """Run one specialist tool call; an identical earlier call in the job is reused, not repeated."""
        key = cache_key(name, arguments)
        earlier = self._job_results.get(job_id, {}).get(key) if key is not None else None
        if earlier is not None:
            self.store.append_event(job_id, "tool.call.reused", agent=role, tool=name)
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
        tool: Tool,
        source_id: str,
    ) -> CallToolResult:
        async with self._tool_count_lock:
            count = self._tool_counts.get(job_id, 0) + 1
            self._tool_counts[job_id] = count
        if count > MAX_CHILD_CALLS:
            raise SpecialistError("Job reached the 30 child-tool call limit")
        read_only = getattr(getattr(tool, "annotations", None), "read_only_hint", None)
        if self.policy.requires_approval(name, read_only_hint=read_only, arguments=arguments):
            arguments = await self._await_approval(job_id, role, name, arguments)
        started = asyncio.get_running_loop().time()
        result = await self.runtime.call_child_tool(
            name,
            arguments,
            request_id=f"job:{job_id}",
            client_name="investigation",
            agent_role=role,
        )
        elapsed = round((asyncio.get_running_loop().time() - started) * 1000)
        self.store.append_event(
            job_id,
            "tool.call.failed" if result.is_error else "tool.call.succeeded",
            agent=role,
            tool=name,
            source_id=source_id,
            duration_ms=elapsed,
        )
        return result

    async def _await_approval(self, job_id: str, role: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Hold a write until the dashboard code approves exactly this call; return the approved arguments."""
        code = self.store.create_pending_call(job_id, name, arguments, agent=role)
        self._approval_codes[job_id] = code
        event = self._approval_events.setdefault(job_id, asyncio.Event())
        self.store.append_event(job_id, "approval.code_issued", agent=role, tool=name)
        await event.wait()
        if self.store.get_job(job_id)["state"] == "cancelled":
            raise asyncio.CancelledError
        try:
            approved_name, approved_args = self.store.consume_approved_call(job_id)
        except KeyError:
            raise SpecialistError("Pending write approval is no longer available") from None
        if approved_name != name or approved_args != arguments:
            raise SpecialistError("Approved operation does not match the pending tool call")
        return approved_args

    @staticmethod
    def _json(value: Any) -> CallToolResult:
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False, default=str))])


def _fallback_summary(findings: list[SpecialistResult], additional: str | None = None) -> str:
    sections = []
    for finding in findings:
        if finding.summary:
            sources = " ".join(f"[{ref.source_id}]" for ref in finding.evidence_refs)
            sections.append(f"{finding.role}: {finding.summary} {sources}".strip())
        if finding.limitation:
            sections.append(f"{finding.role} limitation: {finding.limitation}")
    if additional:
        sections.append(additional)
    return redact("\n".join(sections) or "No specialist evidence was collected.")


def _redact_approval_arguments(value: Any, redact_text: Callable[[str], str]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SENSITIVE_FIELD_RE.search(str(key)) else _redact_approval_arguments(child, redact_text)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_approval_arguments(item, redact_text) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
