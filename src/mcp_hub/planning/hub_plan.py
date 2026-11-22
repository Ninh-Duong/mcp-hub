"""hub__plan on HubRuntime: planning assistance, the plan gate, and policy refusals with their audit events."""
from __future__ import annotations

import asyncio
import logging
import json
from dataclasses import replace
from typing import Any

from mcp.types import (
    CallToolResult,
    TextContent,
)

from datetime import UTC, datetime
from ..extensions import PlanContext, PlanHints
from ..extensions import processing_step
from ..planning.merge import merge_plan_arguments
from ..planning import confirm as plan_confirm, policy
from ..planning.policy import PLAN_TOOL, PlanCatalog, PolicyRefused, Violation
from ..instructions import runtime_contract
from ..results.runtime import error_result


class PlanningMixin:
    """Methods of HubRuntime, moved out of server.py."""

    @property
    def policy_mode(self) -> str:
        return self.settings.policy.mode

    async def _assist_plan(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
        task = arguments.get("task")
        # A playbook the user chose needs no guessing: the planner runs only without one.
        if self.plan_assistant is None or arguments.get("playbook") is not None or not isinstance(task, str) or not task.strip():
            return arguments, "off"
        proj = self.projects.get(arguments["project"]) if isinstance(arguments.get("project"), str) else None
        envs = tuple(proj.allowed_envs) if proj is not None else ()
        context = PlanContext(
            task=task,
            playbooks={pb_id: pb.when for pb_id, pb in self.playbooks.items()},
            servers=tuple(self.clients.keys()),
            envs=envs,
            now=datetime.now(UTC),
        )
        try:
            hints = await processing_step(self.processing_observer, "plan", asyncio.wait_for(
                self.plan_assistant.hints(context),
                self.settings.bitnet.plan_timeout_seconds,
            ))
        except Exception:
            hints = None
        if hints is None:
            return arguments, "fallback"
        cleaned_hints = self._clean_hints(hints, context)
        merged, used = merge_plan_arguments(arguments, cleaned_hints)
        assist = cleaned_hints.source if (used or hints is not None) else "fallback"
        return merged, assist

    def _clean_hints(self, hints: PlanHints, context: PlanContext) -> PlanHints:
        pb = hints.playbook if hints.playbook in context.playbooks else None
        servers = tuple(s for s in hints.servers if s in context.servers)
        env = hints.env if (not context.envs or hints.env in context.envs) else None
        return replace(hints, playbook=pb, servers=servers, env=env)

    async def _create_plan(self, arguments: dict[str, Any], request_id: str) -> CallToolResult:
        """`hub__plan`: store a new plan and return its ID, its tools, and each planned child's contract."""
        fields = {"request_id": request_id, "feature": "hub", "server": "mcp-hub", "tool": PLAN_TOOL}
        offer = self._confirm_gate(arguments, fields)
        if offer is not None:
            return offer
        arguments, assist = await self._assist_plan(arguments)
        try:
            plan = self.plans.create(
                arguments,
                PlanCatalog(
                    self._call_facts, self.playbooks, self.projects,
                    max_window_hours=self.settings.jobs.max_window_hours,
                    policy_settings=self.settings.policy,
                ),
            )
        except policy.NeedsRegistration as exc:
            self.audit.write("hub.plan.needs_registration", level=logging.WARNING, **fields,
                             playbook=arguments.get("playbook"))
            return error_result(f"Policy: {exc}")
        except PolicyRefused as exc:
            self.audit.write("tool.call.denied", level=logging.WARNING, **fields, reason="policy:plan")
            return error_result(f"Policy: {exc}")
        contracts = await asyncio.to_thread(lambda: {sid: runtime_contract(sid) for sid in plan.servers})
        self.audit.write(
            "hub.plan.created", **fields, plan_id=plan.plan_id, servers=sorted(plan.servers),
            env_set=plan.env is not None, writes=sorted(plan.writes), steps=len(plan.steps),
            connection_checks=plan.connection_checks, mode=self.policy_mode,
            playbook=plan.playbook.id if plan.playbook else None, project_set=plan.project is not None,
            assist=assist,
        )
        text = policy.plan_summary(plan, self.tools_by_server(), contracts)
        if plan.workflow is not None:
            text += "\nWorkflow: " + json.dumps(self._workflow_status(plan))
        return CallToolResult(content=[TextContent(type="text", text=text)])

    def _confirm_gate(self, arguments: dict[str, Any], fields: dict[str, Any]) -> CallToolResult | None:
        """Offer the playbooks until the user has chosen one; a refused token is an error. None lets the plan go on."""
        task = arguments.get("task")
        mode = self.settings.policy.confirm_playbook
        if not isinstance(task, str) or not plan_confirm.needs_confirmation(mode, task, self.playbooks):
            return None
        choice = arguments.get("playbook") or plan_confirm.NONE_CHOICE
        if arguments.get(plan_confirm.CONFIRM_ARG) is None:
            options = plan_confirm.choices(task, self.playbooks)
            token = self.confirm_tokens.issue(task, [option for option, _ in options])
            self.audit.write("hub.plan.confirm_requested", **fields, mode=mode, options=len(options))
            return CallToolResult(content=[TextContent(type="text", text=plan_confirm.offer_text(token, options))])
        try:
            self.confirm_tokens.redeem(arguments[plan_confirm.CONFIRM_ARG], task, choice)
        except PolicyRefused as exc:
            self.audit.write("hub.plan.confirm_refused", level=logging.WARNING, **fields, mode=mode)
            return error_result(f"Policy: {exc}")
        return None

    def _policy_denied(self, name: str, request_id: str, violation: Violation) -> CallToolResult:
        self._policy_audit("tool.call.denied", name, request_id, violation)
        return error_result(f"Policy: {violation.message}")

    def _policy_audit(self, event: str, name: str, request_id: str, violation: Violation) -> None:
        route = self.routes[name]
        self.audit.write(
            event, level=logging.WARNING, request_id=request_id, feature=route.feature, server=route.server_id,
            tool=route.tool_name, reason=f"policy:{violation.rule}",
        )
