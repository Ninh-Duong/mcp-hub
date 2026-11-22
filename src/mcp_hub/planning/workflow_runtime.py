"""MCP workflow operations and serialized entry points for the direct-call pilot."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from time import perf_counter
from typing import Any

from jsonschema import Draft202012Validator
from mcp.types import CallToolResult, TextContent, Tool

from . import policy
from .workflow import WorkflowError
from .workflow_scope import result_observations, error_kind
from ..batch import parse_calls, BatchRefused
from ..results.runtime import FULL_RESULT_TOOL, error_result
from ..results.shape import result_succeeded

WORKFLOW_TOOL = "hub__workflow"
WORKFLOW_META = "mcp-hub/workflow"
_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "hub_plan_id": {"type": "string"},
        "action": {"enum": ["status", "report", "finish", "provide_input"]},
        "decision": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,63}$"},
        "inputs": {"type": "object", "additionalProperties": {"type": "string"}},
        "refs": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "needs_source": {"type": "boolean"},
        "outcome": {"enum": ["supported", "insufficient", "blocked"]},
    },
    "required": ["hub_plan_id", "action"],
}


def workflow_tool() -> Tool:
    return Tool(
        name=WORKFLOW_TOOL, description=(
            "Inspect a playbook workflow, provide missing user context, report findings with receipt refs, or finish. "
            "Follow status missing_inputs, required_writes, allowed_tools and report_options. Ask the user before "
            "log time selection or write-capable cache reads/refresh. Fill locked context once; use continue_from "
            "in hub__plan after permission for additional refresh writes. Findings are client-reported. "
            "Finish supported only at ready, otherwise insufficient/blocked. Never invent refs."
        ), input_schema=_SCHEMA, annotations={"readOnlyHint": False, "openWorldHint": False},
    )


def attach_workflow(result: CallToolResult, data: dict[str, Any]) -> CallToolResult:
    return result.model_copy(update={"meta": {**(result.meta or {}), WORKFLOW_META: data}})


class WorkflowRuntimeMixin:
    def _workflow_operation(self, arguments: dict[str, Any], request_id: str) -> CallToolResult:
        if not Draft202012Validator(_SCHEMA).is_valid(arguments):
            return error_result("Policy: invalid hub__workflow arguments.")
        plan = self.plans.get(arguments["hub_plan_id"])
        if plan is None or plan.workflow is None:
            return error_result("Policy: workflow unknown; create a new playbook plan.")
        workflow = plan.workflow
        try:
            action = arguments["action"]
            if action == "report":
                workflow.report(arguments.get("decision"), arguments.get("refs", []), arguments.get("needs_source"))
            elif action == "finish":
                workflow.finish(arguments.get("outcome"), arguments.get("refs", []))
            elif action == "provide_input":
                policy.provide_workflow_input(plan, arguments.get("inputs", {}), self._workflow_catalog())
        except (WorkflowError, policy.PolicyRefused) as exc:
            return self._workflow_refusal(workflow, str(exc), request_id)
        if action != "status":
            metrics = workflow.status()
            self.audit.write("hub.workflow.updated", request_id=request_id, action=action, stage=workflow.stage,
                             outcome=workflow.outcome, calls=len(workflow.receipts), refusals=workflow.refusals,
                             tool_ms=metrics["tool_ms"], elapsed_ms=metrics["elapsed_ms"])
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(self._workflow_status(plan)))])

    def _workflow_catalog(self) -> policy.PlanCatalog:
        return policy.PlanCatalog(self._call_facts, self.playbooks, self.projects,
                                  max_window_hours=self.settings.jobs.max_window_hours,
                                  policy_settings=self.settings.policy)

    def _workflow_status(self, plan: Any) -> dict[str, Any]:
        status = plan.workflow.status()
        status["required_writes"] = [name for name in status["allowed_tools"] if name in self._call_facts
                                     and not self._call_facts[name].read_only and name not in plan.writes]
        return status

    async def _call_planned(self, name: str, arguments: dict[str, Any], request_id: str,
                            client_name: str, options: Any = None) -> CallToolResult:
        plan = self.plans.get(arguments.get(policy.PLAN_ID_ARG))
        if plan is None or plan.workflow is None or self.policy_mode != "enforce":
            return await self._call_planned_core(name, arguments, request_id, client_name, options)
        workflow = plan.workflow
        if workflow.lock.locked():
            return self._workflow_refusal(workflow, "Calls are active; wait before another request.", request_id)
        async with workflow.lock:
            try:
                clean = policy.split_plan_id(arguments)[1]
                self._check_workflow_call(plan, name, clean)
            except WorkflowError as exc:
                return self._workflow_refusal(workflow, str(exc), request_id)
            result = await self._call_planned_core(name, arguments, request_id, client_name, options)
            return self._workflow_result(plan, result)

    def _workflow_refusal(self, workflow: Any, message: str, request_id: str) -> CallToolResult:
        workflow.refusals += 1
        self.audit.write("hub.workflow.refused", request_id=request_id, stage=workflow.stage)
        return error_result(f"Policy: {message}")

    def _check_workflow_call(self, plan: Any, name: str, arguments: dict[str, Any]) -> None:
        if name == FULL_RESULT_TOOL:
            return
        facts = self._call_facts.get(name)
        if facts is None:
            raise WorkflowError("This tool is not enabled in the Hub configuration.")
        violation = policy.check(plan, facts, arguments, self._health_tools, plan.plan_id)
        if violation is not None:
            raise WorkflowError(violation.message)
        plan.workflow.check(name, arguments)

    async def _call_batch(self, arguments: dict[str, Any], request_id: str, client_name: str) -> CallToolResult:
        plan = self.plans.get(arguments.get(policy.PLAN_ID_ARG))
        if plan is None or plan.workflow is None or self.policy_mode != "enforce":
            return await self._call_batch_core(arguments, request_id, client_name)
        workflow = plan.workflow
        if workflow.lock.locked():
            return self._workflow_refusal(workflow, "Calls are active; wait before another request.", request_id)
        async with workflow.lock:
            try:
                servers = {name: route.server_id for name, route in self.routes.items()}
                builtin = frozenset({FULL_RESULT_TOOL}) if self.full_results is not None else frozenset()
                calls = parse_calls(arguments, servers, frozenset(self._read_only),
                                    self.settings.batch.max_calls, builtin_tools=builtin)
                if len(workflow.receipts) + sum(c.tool != FULL_RESULT_TOOL for c in calls) > workflow.max_receipts:
                    raise WorkflowError("Workflow call budget exhausted.")
                for call in calls:
                    self._check_workflow_call(plan, call.tool, policy.split_plan_id(call.arguments)[1])
            except (WorkflowError, BatchRefused) as exc:
                return self._workflow_refusal(workflow, str(exc), request_id)
            return await self._call_batch_core(arguments, request_id, client_name)

    async def _workflow_child_call(self, plan: Any, name: str, arguments: dict[str, Any],
                                   request_id: str, client_name: str, options: Any = None) -> CallToolResult:
        workflow = plan.workflow if plan is not None and self.policy_mode == "enforce" else None
        started = perf_counter()
        outcome = "failed"
        result = None
        try:
            result = await self.call_child_tool(name, arguments, request_id=request_id,
                                                client_name=client_name, options=options)
            outcome = "succeeded" if result_succeeded(result) else "failed"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            if workflow is not None:
                observed = result_observations(result, workflow.max_receipts, workflow.max_input_chars) if result else {}
                if result is not None and outcome == "failed":
                    observed["error_kind"] = error_kind(result)
                if name.startswith("pr__") and "file_id" in arguments:
                    observed["file_id"] = str(arguments["file_id"])[:workflow.max_input_chars]
                receipt = workflow.record(name, outcome, round((perf_counter() - started) * 1000), observations=observed)
                self.audit.write("hub.workflow.call", request_id=request_id, stage=receipt.stage,
                                 outcome=outcome, duration_ms=receipt.duration_ms, tool=name)
        if workflow is None:
            return result
        if name == "jira__sync_ticket" and outcome == "succeeded":
            self._allow_refreshed_ticket(plan)
        return attach_workflow(result, {"hub_plan_id": plan.plan_id, "receipt_id": receipt.receipt_id,
                                        "stage": workflow.stage, "allowed_tools": workflow.allowed_tools()})

    def _allow_refreshed_ticket(self, plan: Any) -> None:
        """An explicitly permitted refresh allows one intentional read of the new ticket revision."""
        from ..results.cache import cache_key

        key = cache_key("jira__read_ticket", {"issue_key": plan.workflow.context["issue_key"]})
        plan.seen.pop(key, None)
        plan.succeeded_at.pop(key, None)

    def _workflow_result(self, plan: Any, result: CallToolResult) -> CallToolResult:
        """Link the retained receipt to a full-result reference after condensation/packing."""
        data = (result.meta or {}).get(WORKFLOW_META)
        if plan is None or plan.workflow is None or not data:
            return result
        condensed = (result.meta or {}).get("mcp-hub/condensed", {})
        rid = condensed.get("result_id")
        receipt = plan.workflow.receipts[data["receipt_id"]]
        if rid:
            plan.workflow.receipts[receipt.receipt_id] = replace(receipt, result_id=rid)
        return result
