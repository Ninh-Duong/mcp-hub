"""Bounded workflow execution facts, fill-once context and client-reported decisions."""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from time import monotonic
from typing import Any

from .workflow_definition import WorkflowDefinition
from .workflow_scope import WorkflowError, absolute, check_scope, fill_context, missing_inputs
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

    def status(self) -> dict[str, Any]:
        return {
            "stage": "awaiting_input" if self.missing() else self.stage, "active_stage": self.stage,
            "missing_inputs": self.missing(), "allowed_tools": self.allowed_tools(),
            "report_options": [*self.node()["reports"], "access_blocked"], "busy": self.lock.locked(), "outcome": self.outcome,
            "scope": {key: value for key, value in self.context.items() if key in {"project", "env", "rcid", "service", "issue_key", "pr_url", "query", "glob", "path", "since", "until"}},
            "revision": self.revision,
            "receipts": [asdict(receipt) for receipt in self.receipts.values()],
            "decisions": list(self.decisions), "calls": len(self.receipts), "refusals": self.refusals,
            "failed": sum(r.outcome != "succeeded" for r in self.receipts.values()),
            "tool_ms": sum(r.duration_ms for r in self.receipts.values()),
            "elapsed_ms": round(((self.stopped or monotonic()) - self.started) * 1000),
            "limitations": ["Source search examines at most 500 files; do not infer an exact repository-wide count."]
            if any(r.tool == "source__search" for r in self.receipts.values()) else [],
        }

    def allowed_tools(self) -> list[str]:
        return [] if self.missing() else list(self.node()["tools"])

    def provide_input(self, inputs: Any) -> None:
        self._idle()
        if self.stage in {"blocked", "ready"}:
            raise WorkflowError("This stage does not accept more context.")
        updated = fill_context(self.context, inputs, self.max_input_chars, self.max_window_hours)
        self.context = updated
        if not self.receipts:
            self.stage = self.entry()

    def check(self, tool: str, arguments: dict[str, Any]) -> None:
        if tool not in self.allowed_tools():
            raise WorkflowError("Use hub__workflow status: provide missing context or use this stage's allowed tools.")
        if len(self.receipts) >= self.max_receipts:
            raise WorkflowError("Workflow call budget exhausted; finish with the available evidence.")
        paths = {path for ref in self.receipts.values() if ref.tool == "source__search" and ref.outcome == "succeeded" for path in ref.paths}
        check_scope(tool, arguments, self.context, self.max_limit, paths)
        for key, value in self.node().get("arguments", {}).get(tool, {}).items():
            if arguments.get(key) != value:
                raise WorkflowError(f"Pass this stage's required {key}.")
        if tool == "jira__read_asset":
            assets = {name for ref in self.receipts.values() if ref.tool == "jira__read_ticket" and ref.outcome == "succeeded" for name in ref.assets}
            if str(arguments.get("filename", "")).removeprefix("assets/") not in assets:
                raise WorkflowError("Read only an attachment listed by successful ticket evidence.")
        predecessor = self.node().get("after", {}).get(tool)
        if predecessor and not any(r.tool == predecessor and r.outcome == "succeeded"
                                   and (tool != "pr__get_pr_file_context" or r.file_id == str(arguments.get("file_id")))
                                   for r in self.receipts.values()):
            raise WorkflowError("Read the prerequisite evidence first; dependent calls cannot share a batch.")

    def record(self, tool: str, outcome: str, duration_ms: int, result_id: str | None = None,
               observations: dict[str, Any] | None = None) -> Receipt:
        data = observations or {}
        receipt = Receipt(f"wf-{secrets.token_hex(12)}", tool, self.stage, outcome, duration_ms, result_id,
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
        receipts = self._references(refs)
        if not receipts or any(r.stage != self.stage for r in receipts):
            raise WorkflowError("Report references must be from the current stage.")
        target = self._next_stage(decision, receipts, needs_source)
        self.decisions.append({"decision": decision, "refs": list(refs), "trust": "client-reported"})
        self.stage = target

    def _next_stage(self, decision: Any, receipts: list[Receipt], needs_source: Any) -> str:
        failed = [r for r in receipts if r.outcome != "succeeded"]
        if decision == "access_blocked" and failed:
            return "blocked"
        if failed and not (decision in {"cache_missing", "cache_stale"} and all(r.error_kind == "cache_missing" for r in failed)):
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
