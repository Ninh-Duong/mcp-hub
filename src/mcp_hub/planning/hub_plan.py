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
from ..client_profiles import (
    ClientProfile, SessionExecution, default_session_execution, resolve_client_profile, validate_session_execution,
)
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

    async def _create_plan(
        self, arguments: dict[str, Any], request_id: str, client_name: str = "unknown",
    ) -> CallToolResult:
        """`hub__plan`: store a new plan and return its ID, its tools, and each planned child's contract."""
        fields = {"request_id": request_id, "feature": "hub", "server": "mcp-hub", "tool": PLAN_TOOL}
        try:
            offer, execution = self._confirm_and_execution(arguments, fields, client_name)
        except PolicyRefused as exc:
            self.audit.write("tool.call.denied", level=logging.WARNING, **fields, reason="policy:plan")
            return error_result(f"Policy: {exc}")
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
                execution=execution,
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
            assist=assist, client_profile=plan.execution.client if plan.execution else "default",
            execution_mode=plan.execution.mode if plan.execution else "single_agent",
        )
        text = policy.plan_summary(plan, self.tools_by_server(), contracts)
        if plan.workflow is not None:
            text += "\nWorkflow: " + json.dumps(self._workflow_status(plan))
        return CallToolResult(content=[TextContent(type="text", text=text)])

    def _confirm_and_execution(
        self, arguments: dict[str, Any], fields: dict[str, Any], client_name: str,
    ) -> tuple[CallToolResult | None, SessionExecution | None]:
        """Run playbook + session execution confirmation and return (offer_or_error, resolved_execution)."""
        profile = self._resolve_profile(arguments, client_name)
        previous = self.plans.get(arguments["continue_from"]) if arguments.get("continue_from") is not None else None
        continuing = previous is not None
        self._check_continuation_client(previous, arguments)
        task = arguments.get("task")
        valid_task = isinstance(task, str) and bool(task.strip())
        confirm_exec = not continuing and self.settings.policy.confirm_execution == "always"
        need_pb = valid_task and plan_confirm.needs_confirmation(
            self.settings.policy.confirm_playbook, task, self.playbooks,
        )
        need_exec = valid_task and confirm_exec and profile.has_choices
        execution: SessionExecution | None = None
        if need_pb or need_exec or (valid_task and arguments.get(plan_confirm.CONFIRM_ARG) is not None):
            offer, execution = self._confirm_gate(
                arguments, fields, profile, need_pb=need_pb, need_exec=need_exec, confirm_exec=confirm_exec,
            )
            if offer is not None:
                return offer, None
        if continuing and not _has_execution_args(arguments):
            return None, None
        return None, execution if execution is not None else self._resolve_execution(profile, arguments, require_explicit=need_exec)

    def _check_continuation_client(self, previous: Any, arguments: dict[str, Any]) -> None:
        explicit = arguments.get("client")
        if previous is None or previous.execution is None or not isinstance(explicit, str) or not explicit.strip():
            return
        try:
            matched = resolve_client_profile(self.client_profiles, "unknown", explicit, shared_service=self.shared_service)
        except ValueError as exc:
            raise PolicyRefused(str(exc)) from None
        if matched.key != previous.execution.client:
            raise PolicyRefused("Continuation must keep the confirmed session execution setup.")

    def _resolve_profile(self, arguments: dict[str, Any], client_name: str) -> ClientProfile:
        explicit = arguments.get("client")
        if explicit is not None and not isinstance(explicit, str):
            raise PolicyRefused("'client' must be a string.")
        try:
            return resolve_client_profile(
                self.client_profiles, client_name, explicit, shared_service=self.shared_service,
            )
        except ValueError as exc:
            raise PolicyRefused(str(exc)) from None

    def _resolve_execution(
        self, profile: ClientProfile, arguments: dict[str, Any], *, require_explicit: bool,
    ) -> SessionExecution:
        if not require_explicit and not _has_execution_args(arguments):
            return default_session_execution(profile)
        try:
            return validate_session_execution(
                profile,
                arguments.get("execution_mode"),
                arguments.get("orchestrate_model"),
                arguments.get("execute_model"),
            )
        except ValueError as exc:
            raise PolicyRefused(str(exc)) from None

    def _confirm_gate(
        self, arguments: dict[str, Any], fields: dict[str, Any], profile: ClientProfile,
        *, need_pb: bool, need_exec: bool, confirm_exec: bool,
    ) -> tuple[CallToolResult | None, SessionExecution | None]:
        """Offer playbooks and/or execution setup until confirmed with a single-use token."""
        task = arguments["task"]
        mode = self.settings.policy.confirm_playbook
        if arguments.get(plan_confirm.CONFIRM_ARG) is None:
            options = plan_confirm.choices(task, self.playbooks) if need_pb else None
            offered = [option for option, _ in options] if options is not None else None
            token = self.confirm_tokens.issue(task, offered, profile_key=profile.key if confirm_exec else None)
            self.audit.write("hub.plan.confirm_requested", **fields, mode=mode, options=len(options or ()))
            known = sorted(self.client_profiles.profiles) if (profile.key == "default" and confirm_exec) else ()
            text = plan_confirm.session_offer_text(
                token, options, profile if need_exec else None, known, shared_service=self.shared_service,
            )
            return CallToolResult(content=[TextContent(type="text", text=text)]), None
        choice = (arguments.get("playbook") or plan_confirm.NONE_CHOICE) if need_pb else None
        try:
            offer = self.confirm_tokens.redeem(arguments[plan_confirm.CONFIRM_ARG], task, choice)
            if offer.profile_key is not None and offer.profile_key != profile.key:
                raise PolicyRefused(
                    "Client profile changed after confirmation offer; call hub__plan without confirm_token to get a new offer."
                )
            execution = self._resolve_execution(profile, arguments, require_explicit=True) if need_exec else None
        except PolicyRefused as exc:
            self.audit.write("hub.plan.confirm_refused", level=logging.WARNING, **fields, mode=mode)
            return error_result(f"Policy: {exc}"), None
        return None, execution

    def _policy_denied(self, name: str, request_id: str, violation: Violation) -> CallToolResult:
        self._policy_audit("tool.call.denied", name, request_id, violation)
        return error_result(f"Policy: {violation.message}")

    def _policy_audit(self, event: str, name: str, request_id: str, violation: Violation) -> None:
        route = self.routes[name]
        self.audit.write(
            event, level=logging.WARNING, request_id=request_id, feature=route.feature, server=route.server_id,
            tool=route.tool_name, reason=f"policy:{violation.rule}",
        )


def _has_execution_args(arguments: dict[str, Any]) -> bool:
    return any(arguments.get(key) is not None for key in ("execution_mode", "orchestrate_model", "execute_model"))
