"""Investigation steps of a job: the specialists and their tool calls, the findings, and the coordinator summary."""
from __future__ import annotations

from .common import cancel_all, store_call
from .tools import build_deterministic_args, limit_text

import asyncio
import json
import re
import uuid
from typing import Any

from mcp.types import CallToolResult, TextContent

from ..agents.coordinator import findings_payload, synthesize
from ..agents.specialists import (
    EvidenceRef, SpecialistExecutor, SpecialistResult, SpecialistError, ToolCallOutcome,
)
from ..audit import redact
from ..extensions import ResultCondenser
from ..extensions import ObservedCondenser, processing_step
from ..textnorm import fold
from ..results.cache import cache_key
from ..tool_meta import is_read_only_tool


_TIME_LIMIT_NOTE = "Investigation reached the {limit} time limit; findings are partial."
_TIMED_OUT_LIMITATION = "Specialist stopped at the {limit} job time limit."
_SQL_RE = re.compile(r"\b(sql|database|db|table|query|stored procedure|bang|du lieu|co so du lieu)\b")


class InvestigationMixin:
    """Investigation and specialist steps of JobRunner (moved out of runner.py)."""

    async def _summarize(
        self, request: dict[str, Any], findings: list[SpecialistResult], timed_out: bool,
    ) -> tuple[str, bool]:
        """Return the coordinator summary, or a fallback and False when no coordinator could write one."""
        notes = [_TIME_LIMIT_NOTE.format(limit=limit_text(self.limits.max_job_seconds))] if timed_out else []
        coordinator = self.providers.get("coordinator")
        if coordinator is None:
            if self.summarizer is not None:
                payload = findings_payload(findings)
                draft = await processing_step(self.processing_observer, "local_summary",
                                              self.summarizer.summarize(request["question"], payload), source="bitnet")
                if draft is not None:
                    return "\n\n".join([draft, *notes]), False
            return _fallback_summary(findings, *notes, "Coordinator agent is not configured."), False
        audit = getattr(self.runtime, "audit", None)
        redactor = (
            (lambda text: audit.redact_content(text, limit=self.limits.coordinator_input_chars))
            if audit is not None else None
        )
        try:
            async with asyncio.timeout(self.limits.coordinator_seconds):
                summary = await synthesize(
                    coordinator,
                    question=request["question"],
                    project=request["project"],
                    env=request["env"],
                    issue_key=request.get("issue_key"),
                    findings=findings,
                    redactor=redactor,
                    input_chars=self.limits.coordinator_input_chars,
                    output_tokens=self.limits.coordinator_output_tokens,
                    summary_chars=self.limits.coordinator_summary_chars,
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
        specialist_roles = roles if roles is not None else roles_for(request)
        tasks = {
            role: asyncio.create_task(self._run_specialist(job_id, role, request), name=f"mcp-hub-{role}-{job_id}")
            for role in specialist_roles
        }
        try:
            _done, pending = await asyncio.wait(tasks.values(), timeout=self.limits.max_job_seconds)
        except asyncio.CancelledError:
            await cancel_all(tasks.values())
            raise
        await cancel_all(pending)
        outcomes = [(role, _task_outcome(task, task in pending)) for role, task in tasks.items()]
        findings, incomplete = await store_call(self._record_findings, job_id, outcomes)
        return findings, incomplete, bool(pending)

    async def _run_deterministic_specialist(
        self, job_id: str, role: str, request: dict[str, Any],
    ) -> SpecialistResult:
        candidates = _DETERMINISTIC_TOOLS.get(role, ())
        tool_name = next((name for name in candidates if name in self.runtime.public_tools), None)
        if tool_name is None:
            await store_call(self.store.append_event, job_id, "agent.unavailable", agent=role)
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
        args = build_deterministic_args(role, props, project, request)
        if args is None:
            await store_call(self.store.append_event, job_id, "agent.unavailable", agent=role)
            return SpecialistResult(role, "", (), "No log targets configured for project.")

        await store_call(self.store.append_event, job_id, "agent.started", agent=role)
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
                            self._observed_condenser().condense(raw_text, request.get("question")),
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
            await store_call(self.store.append_event, job_id, "agent.unavailable", agent=role)
            return SpecialistResult(role, "", (), "Agent is not configured.")
        executor = SpecialistExecutor(
            runtime=self.runtime,
            provider=provider,
            save_result=self.store.save_encrypted_result,
            call_tool=self._call_child_tool,
            tool_filter=self.policy.offers_tool,
            condenser=self._observed_condenser(),
            condense_min_chars=self.condense_min_chars,
            condense_timeout_seconds=self.condense_timeout_seconds,
            max_rounds=self.limits.specialist_max_rounds,
            context_chars=self.limits.context_chars,
            tool_result_chars=self.limits.tool_result_chars,
            provider_output_tokens=self.limits.provider_output_tokens,
        )
        await store_call(self.store.append_event, job_id, "agent.started", agent=role)
        return await executor.run(job_id, role, _specialist_task(request))

    def _observed_condenser(self) -> ResultCondenser | None:
        if self.condenser is None or self.processing_observer is None:
            return self.condenser
        return ObservedCondenser(self.condenser, self.processing_observer)

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
            limitation = _TIMED_OUT_LIMITATION.format(limit=limit_text(self.limits.max_job_seconds))
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
            await store_call(self.store.append_event, job_id, "tool.call.reused", agent=role, tool=name)
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
            await store_call(self.store.append_event, job_id, "tool.call.failed", agent=role, tool=name, duration_ms=elapsed)
            raise
        elapsed = round((loop.time() - started) * 1000)
        await store_call(
            self.store.append_event,
            job_id, "tool.call.failed" if result.is_error else "tool.call.succeeded",
            agent=role, tool=name, source_id=source_id, duration_ms=elapsed,
        )
        return result


def roles_for(request: dict[str, Any]) -> list[str]:
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


_DETERMINISTIC_TOOLS: dict[str, tuple[str, ...]] = {
    "logs": ("logs__get_openobserve_service_logs", "logs__search_openobserve_logs"),
    "sql": ("sql__search_context",),
}
