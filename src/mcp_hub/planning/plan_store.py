"""Metadata-only persistence for PlanBook (config/private/plans.json).

Stores only bounded scope/workflow metadata and wall-clock timestamps for TTL expiration.
Raw tool results, secrets, and resolved log/SQL targets are never written to disk; targets
and playbooks are re-resolved from the active catalog on load.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..client_profiles import SessionExecution
from ..jsonio import read_json, write_json_atomic
from .workflow import Receipt, Workflow

SNAPSHOT_VERSION = 1
DEFAULT_PLAN_TTL_SECONDS = 7200.0
_EXCLUDED_CONTEXT_KEYS = frozenset({"organization", "stream", "field"})


def serialize_plan(plan: Any, created_at: float) -> dict[str, Any]:
    """Convert one Plan into a bounded metadata-only JSON dict."""
    data: dict[str, Any] = {
        "plan_id": plan.plan_id,
        "created_at": created_at,
        "servers": sorted(plan.servers),
        "env": plan.env,
        "writes": sorted(plan.writes),
        "connection_checks": bool(plan.connection_checks),
        "steps": list(plan.steps),
        "playbook_id": plan.playbook.id if plan.playbook is not None else None,
        "project": plan.project,
        "task": plan.task,
        "window": [plan.window[0].isoformat(), plan.window[1].isoformat()] if plan.window is not None else None,
        "had_failure": bool(plan.had_failure),
        "execution": _serialize_execution(plan.execution),
        "workflow": _serialize_workflow(plan.workflow),
    }
    return data


def _serialize_execution(execution: SessionExecution | None) -> dict[str, Any] | None:
    if execution is None:
        return None
    return {
        "client": execution.client,
        "mode": execution.mode,
        "mechanism": execution.mechanism,
        "orchestrate_model": execution.orchestrate_model,
        "execute_model": execution.execute_model,
    }


def _serialize_workflow(workflow: Workflow | None) -> dict[str, Any] | None:
    if workflow is None:
        return None
    context = {k: v for k, v in workflow.context.items() if k not in _EXCLUDED_CONTEXT_KEYS}
    receipts = [
        {
            "receipt_id": r.receipt_id, "tool": r.tool, "stage": r.stage, "outcome": r.outcome,
            "duration_ms": r.duration_ms, "complete": r.complete, "paths": list(r.paths),
            "revision": r.revision, "error_kind": r.error_kind, "file_id": r.file_id,
            "assets": list(r.assets), "project": r.project, "issue_key": r.issue_key,
        }
        for r in workflow.receipts.values()
    ]
    return {
        "stage": workflow.stage,
        "outcome": workflow.outcome,
        "refusals": workflow.refusals,
        "revision": workflow.revision,
        "context": context,
        "decisions": list(workflow.decisions),
        "receipts": receipts,
    }


def read_snapshot_entries(path: Path, ttl_seconds: float, *, audit: Any = None, now: float | None = None) -> list[dict[str, Any]]:
    """Read and TTL-filter plan entries from disk; log and return [] if missing or corrupt."""
    if not path.is_file():
        return []
    try:
        raw = read_json(path)
        if not isinstance(raw, dict) or raw.get("version") != SNAPSHOT_VERSION or not isinstance(raw.get("plans"), list):
            raise ValueError("Invalid plan snapshot format")
    except (OSError, ValueError) as exc:
        if audit is not None:
            audit.write("plans.snapshot.invalid", level=logging.WARNING, error_type=type(exc).__name__)
        return []
    cutoff = (time.time() if now is None else now) - ttl_seconds
    return [
        entry for entry in raw["plans"]
        if isinstance(entry, dict)
        and isinstance(entry.get("created_at"), (int, float))
        and float(entry["created_at"]) >= cutoff
    ]


def write_snapshot(path: Path, entries: list[dict[str, Any]]) -> None:
    """Atomically write plan snapshot metadata with user-only permissions."""
    write_json_atomic(path, {"version": SNAPSHOT_VERSION, "plans": entries}, mode=0o600)


def restore_execution(raw: Any) -> SessionExecution | None:
    if not isinstance(raw, dict):
        return None
    fields = ("client", "mode", "mechanism", "orchestrate_model", "execute_model")
    if not all(isinstance(raw.get(k), str) for k in fields):
        return None
    return SessionExecution(*(raw[k] for k in fields))


def restore_window(raw: Any) -> tuple[datetime, datetime] | None:
    if not isinstance(raw, list) or len(raw) != 2 or not all(isinstance(item, str) for item in raw):
        return None
    return datetime.fromisoformat(raw[0]), datetime.fromisoformat(raw[1])


def restore_workflow(raw: Any, playbook: Any, catalog: Any, target: Any) -> Workflow | None:
    if not isinstance(raw, dict) or playbook is None or not playbook.workflow:
        return None
    context = dict(raw.get("context") or {})
    if target is not None:
        context.update(organization=target.organization, stream=target.stream, field=target.rcid_field)
    settings = catalog.policy_settings
    workflow = Workflow(
        playbook.workflow, context, settings.workflow_max_receipts, settings.workflow_max_limit,
        settings.workflow_input_max_chars, catalog.max_window_hours,
        stage=str(raw.get("stage") or ""),
        outcome=raw.get("outcome") if isinstance(raw.get("outcome"), str) else None,
        refusals=int(raw.get("refusals") or 0),
        revision=raw.get("revision") if isinstance(raw.get("revision"), str) else None,
    )
    if isinstance(raw.get("decisions"), list):
        workflow.decisions = [d for d in raw["decisions"] if isinstance(d, dict)]
    for item in raw.get("receipts") or ():
        if isinstance(item, dict) and isinstance(item.get("receipt_id"), str):
            workflow.receipts[item["receipt_id"]] = Receipt(
                receipt_id=item["receipt_id"], tool=str(item.get("tool", "")),
                stage=str(item.get("stage", "")), outcome=str(item.get("outcome", "")),
                duration_ms=int(item.get("duration_ms") or 0),
                complete=item.get("complete") if isinstance(item.get("complete"), bool) else None,
                paths=tuple(item.get("paths") or ()), revision=item.get("revision"),
                error_kind=item.get("error_kind"), file_id=item.get("file_id"),
                assets=tuple(item.get("assets") or ()), project=item.get("project"),
                issue_key=item.get("issue_key"),
            )
    return workflow
