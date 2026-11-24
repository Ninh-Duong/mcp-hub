"""Bounded workflow execution facts, fill-once context and client-reported decisions."""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from time import monotonic
from typing import Any

from .workflow_definition import WorkflowDefinition, SOURCE_ROLES, REVIEW_SELECTION, SOURCE_EVIDENCE_TOOLS, node_tier
from .workflow_scope import WorkflowError, absolute, check_scope, fill_context, missing_inputs, source_projects, related_issues
from ..settings import JobSettings, PolicySettings


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    tool: str
    stage: str
    outcome: str
    duration_ms: int
    result_id: str | None = None
    complete: bool | None = None
    paths: tuple[str, ...] = ()
    revision: str | None = None
    error_kind: str | None = None
    file_id: str | None = None
    assets: tuple[str, ...] = ()
    project: str | None = None
    issue_key: str | None = None


@dataclass
class Workflow:
    definition: WorkflowDefinition
    context: dict[str, Any]
    max_receipts: int
    max_limit: int
    max_input_chars: int = PolicySettings().workflow_input_max_chars
    max_window_hours: int = JobSettings().max_window_hours
    stage: str = ""
    receipts: dict[str, Receipt] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    started: float = field(default_factory=monotonic)
    stopped: float | None = None
    outcome: str | None = None
    refusals: int = 0
    revision: str | None = None

    def __post_init__(self) -> None:
        self.stage = self.stage or self.entry()

    def entry(self) -> str:
        return next((stage for key, stage in (self.definition.entry_choices or {}).items() if self.context.get(key)), self.definition.entry)

    @property
    def window(self) -> tuple[datetime, datetime] | None:
        if "since" not in self.context:
            return None
        return absolute(self.context["since"]), absolute(self.context["until"])

    def node(self) -> dict[str, Any]:
        return self.definition.nodes.get(self.stage, {"tools": [], "reports": {}})

    def missing(self) -> list[str]:
        if self.stage in {"finished", "blocked", "superseded", "ready"}:
            return []
        return missing_inputs(self.context, [*self.definition.inputs, *self.node().get("requires", [])])

    def active_tier(self) -> str:
        return "orchestrate" if self.missing() else node_tier(self.stage, self.node())

    def status(self) -> dict[str, Any]:
        return {
            "stage": "awaiting_input" if self.missing() else self.stage, "active_stage": self.stage,
            "active_tier": self.active_tier(),
            "missing_inputs": self.missing(), "allowed_tools": self.allowed_tools(),
            "report_options": [*self.node()["reports"], "access_blocked"], "busy": self.lock.locked(), "outcome": self.outcome,
            "scope": {key: value for key, value in self.context.items() if key in {"project", "env", "rcid", "service", "issue_key", "pr_url", "query", "glob", "path", "since", "until"} | REVIEW_SELECTION},
            "revision": self.revision,
            "receipts": [asdict(receipt) for receipt in self.receipts.values()],
            "decisions": list(self.decisions), "calls": len(self.receipts), "refusals": self.refusals,
            "failed": sum(r.outcome != "succeeded" for r in self.receipts.values()),
            "tool_ms": sum(r.duration_ms for r in self.receipts.values()),
            "elapsed_ms": round(((self.stopped or monotonic()) - self.started) * 1000),
            "limitations": self.limitations(),
        }

    def limitations(self) -> list[str]:
        notes = []
        if any(r.tool == "source__search" for r in self.receipts.values()):
            notes.append("Source search examines at most 500 files; do not infer an exact repository-wide count.")
        if self.definition.multi_ticket:
            read = {r.issue_key for r in self.receipts.values() if r.tool == "jira__read_ticket" and r.outcome == "succeeded"}
            missing = related_issues(self.context) - read
            if missing:
                notes.append("Selected related ticket evidence is not available: " + ", ".join(sorted(missing)))
        return notes

    def allowed_tools(self) -> list[str]:
        return [] if self.missing() else list(self.node()["tools"])

    def provide_input(self, inputs: Any) -> None:
        self._idle()
        if self.stage in {"blocked", "ready"}:
            raise WorkflowError("This stage does not accept more context.")
        updated = self.input_context(inputs)
        self.context = updated
        if not self.receipts:
            self.stage = self.entry()

    def input_context(self, inputs: Any) -> dict[str, Any]:
        """Source roles can be adjusted before confirmation; confirmed scope can never grow."""
        self._idle()
        base = dict(self.context)
        if isinstance(inputs, dict) and set(inputs) & REVIEW_SELECTION:
            if set(inputs) & SOURCE_ROLES and not self.definition.multi_source:
                raise WorkflowError("This workflow does not support role-based source selection.")
            if "related_issue_keys" in inputs and not self.definition.multi_ticket:
                raise WorkflowError("This workflow does not support related-ticket selection.")
            if "changed_paths" in inputs and not any("changed_paths" in n.get("requires", ()) for n in self.definition.nodes.values()):
                raise WorkflowError("This workflow does not support changed-path selection.")
            confirmed = (
                any(d["decision"] in {"sources_confirmed", "impact_analyzed", "risks_confirmed"} for d in self.decisions)
                or any(r.tool.startswith("source__") for r in self.receipts.values())
            )
            tickets_locked = (
                confirmed or any(d["decision"] == "related_reviewed" for d in self.decisions)
                or (self.stage != "confirm_sources" and any(d["decision"] == "ticket_reviewed" for d in self.decisions))
            )
            editable = (set() if confirmed else (SOURCE_ROLES | {"changed_paths"})) | (set() if tickets_locked else {"related_issue_keys"})
            locked_keys = (set(inputs) & REVIEW_SELECTION) - editable
            if locked_keys - set(base):
                raise WorkflowError("Review scope is confirmed; start a new scoped plan to add selections.")
            base = {key: value for key, value in base.items() if key not in set(inputs) & editable}
            inputs = {key: value for key, value in inputs.items() if key not in editable or value != ""}
        return fill_context(base, inputs, self.max_input_chars, self.max_window_hours)

    def check(self, tool: str, arguments: dict[str, Any]) -> None:
        if tool not in self.allowed_tools():
            raise WorkflowError("Use hub__workflow status: provide missing context or use this stage's allowed tools.")
        if len(self.receipts) >= self.max_receipts:
            raise WorkflowError("Workflow call budget exhausted; finish with the available evidence.")
        paths = {path for ref in self.receipts.values() if ref.tool.startswith("source__") and ref.outcome == "succeeded"
                 and ref.project == arguments.get("project") for path in ref.paths}
        projects = source_projects(self.context) if self.definition.multi_source else None
        issues = related_issues(self.context) if self.definition.multi_ticket and self.node().get("ticket_scope") == "related" else None
        check_scope(tool, arguments, self.context, self.max_limit, paths, projects, issues)
        for key, value in self.node().get("arguments", {}).get(tool, {}).items():
            if arguments.get(key) != value:
                raise WorkflowError(f"Pass this stage's required {key}.")
        self._check_ticket(tool, arguments)
        predecessor = self.node().get("after", {}).get(tool)
        if predecessor and not any(r.tool == predecessor and r.outcome == "succeeded"
                                   and (tool != "pr__get_pr_file_context" or r.file_id == str(arguments.get("file_id")))
                                   for r in self.receipts.values()):
            raise WorkflowError("Read the prerequisite evidence first; dependent calls cannot share a batch.")

    def _check_ticket(self, tool: str, arguments: dict[str, Any]) -> None:
        issue = arguments.get("issue_key")
        reads = [r for r in self.receipts.values() if r.tool == "jira__read_ticket" and r.issue_key == issue]
        if tool == "jira__read_asset":
            successful = [r for r in reads if r.outcome == "succeeded"]
            assets = set(successful[-1].assets) if successful else set()
            if str(arguments.get("filename", "")).removeprefix("assets/") not in assets:
                raise WorkflowError("Read only an attachment listed by this ticket's successful evidence.")
        if tool == "jira__sync_ticket" and self.node().get("ticket_scope") == "related":
            if not any(r.outcome == "succeeded" or r.error_kind == "cache_missing" for r in reads):
                raise WorkflowError("Read this selected related ticket before requesting its cache refresh.")

    def record(self, tool: str, outcome: str, duration_ms: int, result_id: str | None = None,
               observations: dict[str, Any] | None = None) -> Receipt:
        data = observations or {}
        project = data.get("project") or (self.context.get("project") if tool.startswith("source__") and not self.definition.multi_source else None)
        issue = data.get("issue_key") or (self.context.get("issue_key") if tool.startswith("jira__") else None)
        receipt = Receipt(f"wf-{secrets.token_hex(12)}", tool, self.stage, outcome, duration_ms, result_id, project=project, issue_key=issue,
                          **{key: data[key] for key in ("complete", "paths", "revision", "error_kind", "file_id", "assets") if key in data})
        self.receipts[receipt.receipt_id] = receipt
        if receipt.error_kind == "access":
            self.stage = "blocked"
        if tool.startswith("pr__") and receipt.revision and outcome == "succeeded":
            if self.revision is not None and self.revision != receipt.revision:
                self.stage = "blocked"
            else:
                self.revision = receipt.revision
        return receipt

    def report(self, decision: Any, refs: Any, needs_source: Any = None) -> None:
        self._idle()
        if self.missing():
            raise WorkflowError("Provide missing context before reporting this stage.")
        if decision == "sources_confirmed":
            minimum = len(related_issues(self.context)) + 2 * len(source_projects(self.context))
            if minimum > self.max_receipts - len(self.receipts):
                raise WorkflowError("Selected review scopes exceed the remaining call budget; reduce the selection.")
        receipts = self._references(refs)
        if self.node().get("interaction"):
            req_all = self.node().get("evidence", [])
            req_any = self.node().get("evidence_any", [])
            ok_all = all(any(r.tool == tool and r.outcome == "succeeded" for r in self.receipts.values()) for tool in req_all)
            ok_any = not req_any or any(r.tool in req_any and r.outcome == "succeeded" for r in self.receipts.values())
            if not (ok_all and ok_any):
                raise WorkflowError("Read the required evidence before confirming this stage.")
        elif not receipts or any(r.stage != self.stage for r in receipts):
            raise WorkflowError("Report references must be from the current stage.")
        if self.node().get("source_coverage") and decision != "access_blocked":
            read = {r.project for r in self.receipts.values() if r.tool in SOURCE_EVIDENCE_TOOLS and r.outcome == "succeeded"}
            if source_projects(self.context) - read:
                raise WorkflowError("Read evidence from every confirmed source or finish insufficient with limitations.")
        if self.node().get("ticket_coverage") and decision != "access_blocked":
            attempted = {r.issue_key for r in self.receipts.values() if r.tool == "jira__read_ticket" and r.stage == self.stage}
            if related_issues(self.context) - attempted:
                raise WorkflowError("Attempt each confirmed related ticket or finish insufficient with limitations.")
        target = self._next_stage(decision, receipts, needs_source)
        self.decisions.append({"decision": decision, "refs": list(refs), "trust": "client-reported"})
        self.stage = target

    def _next_stage(self, decision: Any, receipts: list[Receipt], needs_source: Any) -> str:
        failed = [r for r in receipts if r.outcome != "succeeded"]
        if decision == "access_blocked" and failed:
            return "blocked"
        missing_allowed = decision in {"cache_missing", "cache_stale"} or self.node().get("allow_missing_cache")
        if failed and not (missing_allowed and all(r.error_kind == "cache_missing" for r in failed)):
            raise WorkflowError("Failed calls do not complete a step; retry or report access_blocked.")
        event = decision
        if decision == "findings":
            if not isinstance(needs_source, bool):
                raise WorkflowError("findings requires needs_source true or false.")
            event = "need_source" if needs_source else "sufficient"
        target = self.node()["reports"].get(event)
        if target is None:
            raise WorkflowError("Use a report_option for this stage.")
        return target if isinstance(target, str) else target["present" if self.context.get(target["input"]) else "missing"]

    def finish(self, outcome: Any, refs: Any) -> None:
        self._idle()
        receipts = self._references(refs)
        if outcome not in {"supported", "insufficient", "blocked"}:
            raise WorkflowError("Finish outcome must be supported, insufficient or blocked.")
        if self.stage == "blocked" and outcome != "blocked":
            raise WorkflowError("Blocked workflows must finish blocked.")
        evidence = any(r.tool not in {"logs__list_ai_context_snapshots", "pr__list_synced_prs"} for r in receipts)
        if outcome == "supported" and (self.stage != "ready" or not evidence or any(r.outcome != "succeeded" for r in receipts)):
            raise WorkflowError("Supported requires ready stage and successful evidence references.")
        if outcome == "supported" and self.definition.multi_source:
            if source_projects(self.context) - {r.project for r in receipts if r.tool in SOURCE_EVIDENCE_TOOLS}:
                raise WorkflowError("Supported source review must reference reads from every confirmed source.")
        if outcome == "supported" and self.definition.multi_ticket:
            read = {r.issue_key for r in receipts if r.tool == "jira__read_ticket" and r.outcome == "succeeded"}
            if related_issues(self.context) - read:
                raise WorkflowError("Supported review must reference successful reads of every confirmed related ticket.")
        self.outcome, self.stage, self.stopped = outcome, "finished", monotonic()

    def _idle(self) -> None:
        if self.lock.locked():
            raise WorkflowError("Calls are active; wait before changing workflow state.")
        if self.stage in {"finished", "superseded"}:
            raise WorkflowError("Workflow is finished or superseded; use its successor or create a new plan.")

    def _references(self, refs: Any) -> list[Receipt]:
        if not isinstance(refs, list) or len(refs) > self.max_receipts:
            raise WorkflowError("refs must be a bounded list of receipt IDs from this plan.")
        if any(not isinstance(ref, str) or ref not in self.receipts for ref in refs):
            raise WorkflowError("Unknown reference; use only receipt IDs from this plan.")
        return [self.receipts[ref] for ref in refs]
