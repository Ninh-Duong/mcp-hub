"""Bounded, process-local execution facts and client-reported workflow decisions."""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any


class WorkflowError(ValueError):
    """A safe workflow refusal; contains no child results."""


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    tool: str
    stage: str
    outcome: str
    duration_ms: int
    result_id: str | None = None


def pilot_window(arguments: dict[str, Any], seconds: float) -> tuple[datetime, datetime]:
    """Validate pilot inputs and fix a default absolute window once, at plan creation."""
    for key in ("rcid", "project", "env"):
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise WorkflowError(f"rcid-bug requires a non-empty {key}.")
    if arguments.get("since") is None and arguments.get("until") is None:
        end = datetime.now(UTC)
        return end - timedelta(seconds=seconds), end
    return _absolute(arguments.get("since")), _absolute(arguments.get("until"))


def _absolute(value: Any) -> datetime:
    if not isinstance(value, str):
        raise WorkflowError("Pass both absolute since/until or from/to timestamps with timezones.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise WorkflowError("Use absolute timestamps with timezones; relative windows are refused.") from None
    if parsed.tzinfo is None:
        raise WorkflowError("Timestamps must include a timezone.")
    return parsed.astimezone(UTC)


@dataclass
class Workflow:
    stages: dict[str, tuple[str, ...]]
    rcid: str
    env: str
    targets: dict[str, str]
    window: tuple[datetime, datetime]
    max_receipts: int
    max_limit: int
    stage: str = "cache_list"
    receipts: dict[str, Receipt] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    started: float = field(default_factory=monotonic)
    stopped: float | None = None
    outcome: str | None = None
    refusals: int = 0

    def status(self) -> dict[str, Any]:
        return {
            "stage": self.stage, "allowed_tools": self.allowed_tools(),
            "busy": self.lock.locked(), "outcome": self.outcome,
            "receipts": [asdict(receipt) for receipt in self.receipts.values()],
            "decisions": list(self.decisions), "calls": len(self.receipts), "refusals": self.refusals,
            "failed": sum(r.outcome != "succeeded" for r in self.receipts.values()),
            "tool_ms": sum(r.duration_ms for r in self.receipts.values()),
            "elapsed_ms": round(((self.stopped or monotonic()) - self.started) * 1000),
        }

    def allowed_tools(self) -> list[str]:
        tools = list(self.stages.get(self.stage, ()))
        if self.stage == "logs" and not any(r.tool == "logs__find_openobserve_logs_by_rcid"
                                             and r.outcome == "succeeded" for r in self.receipts.values()):
            tools = [name for name in tools if name != "logs__get_openobserve_service_logs"]
        return tools

    def check(self, tool: str, arguments: dict[str, Any]) -> None:
        """Validate without reserving or changing state, so whole batches can be checked first."""
        if self.stage == "finished" or tool not in self.allowed_tools():
            raise WorkflowError(f"Stage {self.stage}: use hub__workflow status and its allowed_tools.")
        if len(self.receipts) >= self.max_receipts:
            raise WorkflowError("Workflow call budget exhausted; finish with the available evidence.")
        if self.stage.startswith("source_"):
            if arguments.get("project") != self.targets["project"]:
                raise WorkflowError("Pass exactly the plan's source project.")
        if self.stage in {"cache_list", "logs"} and arguments.get("env") != self.env:
            raise WorkflowError("Pass exactly the plan's environment.")
        if self.stage == "logs":
            self._check_logs(tool, arguments)
        if self.stage == "cache_read":
            if not isinstance(arguments.get("snapshot"), str) or not isinstance(arguments.get("file"), str):
                raise WorkflowError("Cache reads require snapshot and file from the listed snapshots.")

    def _check_logs(self, tool: str, arguments: dict[str, Any]) -> None:
        for key in ("organization", "stream"):
            if arguments.get(key) != self.targets[key]:
                raise WorkflowError(f"Pass the plan's {key}.")
        if tool == "logs__find_openobserve_logs_by_rcid":
            if arguments.get("rcid") != self.rcid or arguments.get("field") != self.targets["field"]:
                raise WorkflowError("Pass the plan's rcid and request-ID field.")
        elif not arguments.get("service") or arguments.get("level") != "Warning":
            raise WorkflowError("Related service logs require a service from the request and level Warning.")
        if (_absolute(arguments.get("from")), _absolute(arguments.get("to"))) != self.window:
            raise WorkflowError("Use exactly the plan's absolute from/to window; re-plan to change scope.")
        limit = arguments.get("limit")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.max_limit:
            raise WorkflowError(f"Pass an explicit limit between 1 and {self.max_limit}.")

    def record(self, tool: str, outcome: str, duration_ms: int, result_id: str | None = None) -> Receipt:
        receipt = Receipt(f"wf-{secrets.token_hex(12)}", tool, self.stage, outcome, duration_ms, result_id)
        self.receipts[receipt.receipt_id] = receipt
        return receipt

    def report(self, decision: Any, refs: Any, needs_source: Any = None) -> None:
        self._idle()
        receipts = self._references(refs)
        if not receipts or any(r.stage != self.stage for r in receipts):
            raise WorkflowError("Report references must be from the current stage.")
        next_stage = self._next_stage(decision, receipts, needs_source)
        self.decisions.append({"decision": decision, "refs": list(refs), "trust": "client-reported"})
        self.stage = next_stage

    def _next_stage(self, decision: Any, receipts: list[Receipt], needs_source: Any) -> str:
        if decision == "access_blocked" and any(r.outcome != "succeeded" for r in receipts):
            return "blocked"
        if any(r.outcome != "succeeded" for r in receipts):
            raise WorkflowError("Failed calls do not complete a step; retry or report access_blocked.")
        transitions = {
            ("cache_list", "cache_hit"): "cache_read", ("cache_list", "cache_miss"): "logs",
            ("cache_read", "cache_insufficient"): "logs",
            ("source_search", "source_found"): "source_read",
            ("source_read", "source_read"): "ready",
        }
        if (self.stage, decision) in transitions:
            return transitions[self.stage, decision]
        if self.stage in {"cache_read", "logs"} and decision == "findings" and isinstance(needs_source, bool):
            return "source_search" if needs_source else "ready"
        raise WorkflowError("Decision is invalid for this stage; findings requires needs_source true or false.")

    def finish(self, outcome: Any, refs: Any) -> None:
        self._idle()
        receipts = self._references(refs)
        if outcome not in {"supported", "insufficient", "blocked"}:
            raise WorkflowError("Finish outcome must be supported, insufficient or blocked.")
        if self.stage == "blocked" and outcome != "blocked":
            raise WorkflowError("Access-blocked workflows must finish blocked.")
        evidence = any(r.stage in {"cache_read", "logs", "source_read"} for r in receipts)
        if outcome == "supported" and (self.stage != "ready" or not evidence or any(r.outcome != "succeeded" for r in receipts)):
            raise WorkflowError("Supported requires ready stage and successful evidence references.")
        self.outcome, self.stage, self.stopped = outcome, "finished", monotonic()

    def _idle(self) -> None:
        if self.lock.locked():
            raise WorkflowError("Calls are active; wait before changing workflow state.")
        if self.stage == "finished":
            raise WorkflowError("Workflow is finished; create a new plan.")

    def _references(self, refs: Any) -> list[Receipt]:
        if not isinstance(refs, list) or len(refs) > self.max_receipts:
            raise WorkflowError("refs must be a bounded list of receipt IDs from this plan.")
        if any(not isinstance(ref, str) or ref not in self.receipts for ref in refs):
            raise WorkflowError("Unknown reference; use only receipt IDs from this plan.")
        return [self.receipts[ref] for ref in refs]
