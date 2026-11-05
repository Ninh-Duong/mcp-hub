"""Usage policy for direct client calls: a client declares a plan with `hub__plan`, then the Hub refuses
calls that break it.

The rules in `docs/client-rules.md` used to be advisory text that a client could ignore. This module makes
the checkable ones binding for every client:

- no child tool runs without the `hub_plan_id` that `hub__plan` returned;
- only the children the plan names;
- the plan's environment, passed explicitly to tools that take `env`;
- a tool that may write only when the plan lists it (the user asked for it);
- a connection check only after a failed call, or when the plan says the user asked for checks;
- no identical repeat of a call that already succeeded, and one retry of a failed one;
- with a playbook (`docs/playbooks/<id>.md`): only its tools, `limit` at most its `max_limit`, and the
  project's log targets (organization, stream, request-ID field) and source project, nothing else.

The plan travels as a `hub_plan_id` argument, not as MCP session state: on current protocol versions a
request carries no lasting session identity. The Hub adds the argument to every tool schema it publishes and
removes it before a call reaches the child. Pure logic: the Hub runtime does the routing and auditing.
"""
from __future__ import annotations

import secrets
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from mcp.types import Tool

from .playbooks import Playbook
from .projects import LogTarget, ProjectConfig
from .result_cache import cache_key

PLAN_TOOL = "hub__plan"
PLAN_ID_ARG = "hub_plan_id"
MAX_TEXT = 2000
MAX_STEPS = 50
# A failed call may be retried once (two attempts in all); a succeeded call is never repeated.
MAX_FAILED_ATTEMPTS = 2
_SUCCEEDED = -1

_PLAN_REQUIRED = (
    f"Call {PLAN_TOOL} first (task, child server IDs, environment, planned steps), then pass the "
    f"{PLAN_ID_ARG} it returns with every call. It also returns the rules for those children."
)
_PLAN_ID_SCHEMA = {"type": "string", "description": f"The plan ID that {PLAN_TOOL} returned; required."}


class PolicyRefused(ValueError):
    """An invalid plan; the message is safe to show to the client."""


class Violation(NamedTuple):
    rule: str
    message: str


class CallFacts(NamedTuple):
    """What the policy needs to know about one tool, from the Hub's routes."""

    name: str
    server_id: str
    read_only: bool
    accepts_env: bool


@dataclass(frozen=True)
class PlanCatalog:
    """What a plan is checked against: the routed tools, the playbooks, and the registered projects."""

    tools: Mapping[str, CallFacts]
    playbooks: Mapping[str, Playbook] = field(default_factory=dict)
    projects: Mapping[str, ProjectConfig] = field(default_factory=dict)


@dataclass
class Plan:
    plan_id: str
    servers: frozenset[str]
    env: str | None
    writes: frozenset[str]
    connection_checks: bool
    steps: tuple[str, ...]
    playbook: Playbook | None = None
    project: str | None = None
    target: LogTarget | None = None
    # Call key -> number of failed attempts, or _SUCCEEDED once it succeeded.
    seen: dict[str, int] = field(default_factory=dict)
    had_failure: bool = False


_STRING_LIST = {"type": "array", "items": {"type": "string"}}
_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {"type": "string", "description": "The user's request, in one or two sentences."},
        "servers": {**_STRING_LIST, "minItems": 1, "description": "Child server IDs this task needs (see hub__status)."},
        "env": {"type": "string", "description": "The environment the user named (dev, stg, prod); keep it for the task."},
        "steps": {**_STRING_LIST, "minItems": 1, "maxItems": MAX_STEPS,
                  "description": "The planned calls in order; group independent reads into one hub__batch step."},
        "writes": {**_STRING_LIST, "description": "Tools that may write, only those the user explicitly asked for."},
        "connection_checks": {"type": "boolean",
                              "description": "True only when the user asked to check connections."},
        "playbook": {"type": "string", "description": "The playbook that matches the user's input (listed above)."},
        "project": {"type": "string", "description": "The project alias (see hub__list_projects); log playbooks need it."},
    },
    "required": ["task"],
    "additionalProperties": False,
}
_PLAN_DESCRIPTION = (
    f"Required before any child tool. Pick the playbook that matches the user's input and pass it with the "
    f"project and env; or declare the child server IDs and planned steps yourself. Returns a {PLAN_ID_ARG} to "
    "pass with every later call, the steps to follow, the log targets, and each child's rules. The Hub then "
    "refuses calls outside the plan: other children or tools, another environment, organization, or stream, "
    "a larger limit, undeclared writes, connection checks before a failure, and identical repeats."
)


def plan_tool(playbooks: Mapping[str, Playbook] | None = None) -> Tool:
    """`hub__plan`, its description listing each playbook and when to use it."""
    schema = _PLAN_SCHEMA
    description = _PLAN_DESCRIPTION
    if playbooks:
        listed = "\n".join(f"- {playbook.id}: {playbook.when}" for playbook in playbooks.values())
        description = f"{description}\nPlaybooks:\n{listed}"
        properties = {**schema["properties"], "playbook": {**schema["properties"]["playbook"], "enum": list(playbooks)}}
        schema = {**schema, "properties": properties}
    return Tool(
        name=PLAN_TOOL, description=description, input_schema=schema,
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )


def tool_accepts_env(tool: Tool) -> bool:
    properties = (tool.input_schema or {}).get("properties")
    return isinstance(properties, Mapping) and "env" in properties


def with_plan_id(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """A copy of a tool's input schema that also accepts `hub_plan_id`."""
    copied = dict(schema or {"type": "object"})
    copied["properties"] = {**(copied.get("properties") or {}), PLAN_ID_ARG: _PLAN_ID_SCHEMA}
    return copied


def split_plan_id(arguments: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    """(the call's `hub_plan_id`, the arguments the child receives without it)."""
    rest = {key: value for key, value in arguments.items() if key != PLAN_ID_ARG}
    return arguments.get(PLAN_ID_ARG), rest


class PlanBook:
    """Recent plans by ID; the oldest is dropped past `max_plans`."""

    def __init__(self, max_plans: int) -> None:
        self.max_plans = max_plans
        self._plans: OrderedDict[str, Plan] = OrderedDict()

    def get(self, plan_id: Any) -> Plan | None:
        plan = self._plans.get(plan_id) if isinstance(plan_id, str) else None
        if plan is not None:
            self._plans.move_to_end(plan_id)
        return plan

    def create(self, arguments: Mapping[str, Any], catalog: PlanCatalog) -> Plan:
        plan = parse_plan(arguments, catalog, f"plan-{secrets.token_hex(6)}")
        self._plans[plan.plan_id] = plan
        while len(self._plans) > self.max_plans:
            self._plans.popitem(last=False)
        return plan


def parse_plan(arguments: Mapping[str, Any], catalog: PlanCatalog, plan_id: str) -> Plan:
    """Validate `hub__plan` arguments against the Hub's tools, playbooks, and projects; PolicyRefused says the fix."""
    _text(arguments.get("task"), "task")
    playbook = _playbook(arguments, catalog)
    servers = _servers(arguments, catalog, playbook)
    steps = _steps(arguments, playbook)
    project, env, target = _scope(arguments, catalog, playbook)
    writes = _strings(arguments.get("writes", []), "writes")
    for name in writes:
        facts = catalog.tools.get(name)
        if facts is None or facts.read_only or facts.server_id not in servers:
            raise PolicyRefused(f"'writes' may list only tools that may write, from the planned servers: {name}.")
        if playbook is not None and name not in playbook.tools:
            raise PolicyRefused(f"'writes' may list only tools of playbook {playbook.id}: {name}.")
    checks = arguments.get("connection_checks", False)
    if not isinstance(checks, bool):
        raise PolicyRefused("'connection_checks' must be true or false.")
    return Plan(plan_id, frozenset(servers), env, frozenset(writes), checks, steps, playbook, project, target)


def _playbook(arguments: Mapping[str, Any], catalog: PlanCatalog) -> Playbook | None:
    name = arguments.get("playbook")
    if name is None:
        return None
    if name not in catalog.playbooks:
        raise PolicyRefused(f"'playbook' must be one of: {', '.join(catalog.playbooks) or 'none'}.")
    return catalog.playbooks[name]


def _servers(arguments: Mapping[str, Any], catalog: PlanCatalog, playbook: Playbook | None) -> list[str]:
    """The planned children: those given, else the playbook's; each known, and within the playbook."""
    known = sorted({facts.server_id for facts in catalog.tools.values()})
    given = arguments.get("servers")
    servers = _strings(given, "servers") if given is not None or playbook is None else list(playbook.servers)
    allowed = known if playbook is None else [server for server in playbook.servers if server in known]
    unknown = [server for server in servers if server not in allowed]
    if not servers or unknown:
        raise PolicyRefused(f"'servers' must name connected child server IDs from: {', '.join(allowed) or 'none'}.")
    return servers


def _steps(arguments: Mapping[str, Any], playbook: Playbook | None) -> tuple[str, ...]:
    if playbook is not None and arguments.get("steps") is None:
        return (f"Follow playbook {playbook.id}.",)
    steps = _strings(arguments.get("steps"), "steps")
    if not steps or len(steps) > MAX_STEPS:
        raise PolicyRefused(f"'steps' must list 1 to {MAX_STEPS} planned calls.")
    return tuple(steps)


def _scope(
    arguments: Mapping[str, Any], catalog: PlanCatalog, playbook: Playbook | None,
) -> tuple[str | None, str | None, LogTarget | None]:
    """(project alias, environment, log target), checked against the project and the playbook's needs."""
    env = arguments.get("env")
    env = _text(env, "env") if env is not None else None
    alias = arguments.get("project")
    needs_logs = playbook is not None and "logs" in playbook.needs
    if alias is None:
        if needs_logs:
            raise PolicyRefused(f"Playbook {playbook.id} needs 'project': one of {', '.join(catalog.projects) or 'none'}.")
        return None, env, None
    project = catalog.projects.get(_text(alias, "project"))
    if project is None:
        raise PolicyRefused(f"'project' must be one of: {', '.join(catalog.projects) or 'none (none registered)'}.")
    env = env or project.default_env
    if env is None or env not in project.allowed_envs:
        raise PolicyRefused(f"'env' must be one of {', '.join(project.allowed_envs)} for project {project.alias}.")
    target = project.logs.get(env)
    if needs_logs and target is None:
        raise PolicyRefused(
            f"Project {project.alias} has no log targets for {env}. Ask the user to add logs.{env} (organization, "
            "stream, rcid_field) for it in config/private/projects.json; do not discover them yourself."
        )
    return project.alias, env, target


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise PolicyRefused(f"'{name}' must be a non-empty string of at most {MAX_TEXT} characters.")
    return value.strip()


def _strings(value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise PolicyRefused(f"'{name}' must be a list of strings.")
    return [_text(item, name) for item in value]


def missing_plan(plan_id: Any) -> Violation:
    if plan_id is None:
        return Violation("no_plan", _PLAN_REQUIRED)
    return Violation("unknown_plan", f"Unknown or expired {PLAN_ID_ARG}. {_PLAN_REQUIRED}")


def check(
    plan: Plan | None, facts: CallFacts, arguments: Mapping[str, Any], health_tools: frozenset[str],
    plan_id: Any = None,
) -> Violation | None:
    """The first rule this call breaks, or None. `arguments` excludes `hub_plan_id`."""
    if plan is None:
        return missing_plan(plan_id)
    if facts.server_id not in plan.servers:
        return Violation("server", f"{facts.name} belongs to {facts.server_id}, which the plan does not name. "
                                   f"Call {PLAN_TOOL} again if the task needs it.")
    for rule_check in (_check_playbook, _check_env, _check_target):
        violation = rule_check(plan, facts, arguments)
        if violation is not None:
            return violation
    if not facts.read_only and facts.name not in plan.writes:
        return Violation("write", f"{facts.name} may write and the plan does not list it in 'writes'. "
                                  "Write only what the user asked for.")
    if facts.name in health_tools and not (plan.had_failure or plan.connection_checks):
        return Violation("health_check", f"{facts.name} is a connection check: use it only to explain a failed "
                                         "call, or when the user asked to check connections.")
    key = cache_key(facts.name, dict(arguments))
    outcome = plan.seen.get(key) if key is not None else None
    if outcome == _SUCCEEDED:
        return Violation("repeat", f"{facts.name} already ran with these arguments in this plan. "
                                   "Reuse that result.")
    if outcome is not None and outcome >= MAX_FAILED_ATTEMPTS:
        return Violation("retry", f"{facts.name} already failed {outcome} times with these arguments. "
                                  "Report the failure instead of retrying.")
    return None


def _check_env(plan: Plan, facts: CallFacts, arguments: Mapping[str, Any]) -> Violation | None:
    if plan.env is None:
        return None
    if "env" in arguments and arguments["env"] != plan.env:
        return Violation("env", f"The plan's environment is '{plan.env}'. Do not switch environments on your "
                                f"own; call {PLAN_TOOL} again if the user asked for another.")
    if facts.accepts_env and "env" not in arguments:
        return Violation("env", f"Pass env='{plan.env}' to {facts.name}, the environment of the plan.")
    return None


def _check_playbook(plan: Plan, facts: CallFacts, arguments: Mapping[str, Any]) -> Violation | None:
    playbook = plan.playbook
    if playbook is None:
        return None
    if facts.name not in playbook.tools:
        return Violation("playbook_tool", f"{facts.name} is not a tool of playbook {playbook.id}: use "
                                          f"{', '.join(playbook.tools)}.")
    limit = arguments.get("limit")
    if playbook.max_limit is not None and isinstance(limit, (int, float)) and limit > playbook.max_limit:
        return Violation("limit", f"limit {limit} is above {playbook.max_limit}, the most playbook {playbook.id} "
                                  "allows. Start narrow (50) and widen the time window instead.")
    return None


_TARGET_ARGS = (("organization", "organization"), ("stream", "stream"), ("field", "rcid_field"))


def _check_target(plan: Plan, facts: CallFacts, arguments: Mapping[str, Any]) -> Violation | None:
    """The plan's log targets and source project, when it has them, are the only ones a call may name."""
    expected = {}
    if plan.target is not None:
        expected = {arg: getattr(plan.target, attr) for arg, attr in _TARGET_ARGS}
    if plan.project is not None:
        expected["project"] = plan.project
    for arg, value in expected.items():
        if arg in arguments and arguments[arg] != value:
            return Violation("target", f"Pass {arg}='{value}' to {facts.name}, as the plan's targets say.")
    return None


def check_batch(
    plan: Plan | None, calls: Iterable[tuple[CallFacts, Mapping[str, Any]]], health_tools: frozenset[str],
    plan_id: Any = None,
) -> tuple[int, Violation] | None:
    """The first (1-based index, violation) in a batch, counting a repeat inside the batch too."""
    keys: set[str] = set()
    for index, (facts, arguments) in enumerate(calls, 1):
        violation = check(plan, facts, arguments, health_tools, plan_id)
        key = cache_key(facts.name, dict(arguments))
        if violation is None and key is not None and key in keys:
            violation = Violation("repeat", f"{facts.name} appears twice with the same arguments.")
        if violation is not None:
            return index, violation
        if key is not None:
            keys.add(key)
    return None


def record(plan: Plan | None, name: str, arguments: Mapping[str, Any], ok: bool) -> None:
    """Remember a call's outcome: a success blocks its repeat; a failure allows connection checks."""
    if plan is None:
        return
    key = cache_key(name, dict(arguments))
    if key is not None and plan.seen.get(key) != _SUCCEEDED:
        plan.seen[key] = _SUCCEEDED if ok else plan.seen.get(key, 0) + 1
    if not ok:
        plan.had_failure = True


def _scope_lines(plan: Plan) -> list[str]:
    """The playbook's limits and steps, and the targets to pass, for the `hub__plan` answer."""
    lines = []
    if plan.project is not None:
        lines.append(f"Project: {plan.project} (pass project='{plan.project}' to source tools).")
    if plan.target is not None:
        lines.append(
            f"Log targets for {plan.env}: organization='{plan.target.organization}', stream='{plan.target.stream}', "
            f"field='{plan.target.rcid_field}'. Use exactly these; do not list organizations or streams."
        )
    playbook = plan.playbook
    if playbook is not None:
        limit = f"; limit at most {playbook.max_limit}" if playbook.max_limit is not None else ""
        lines.append(f"Playbook {playbook.id}: only these tools: {', '.join(playbook.tools)}{limit}.")
        lines.append(f"\n# Playbook {playbook.id}\n\n{playbook.body}")
    return lines


def plan_summary(plan: Plan, tools_by_server: Mapping[str, list[str]], contracts: Mapping[str, str]) -> str:
    """The text `hub__plan` returns: the allowed tools, the enforced rules, and each child's contract."""
    lines = [
        f"Plan accepted. Pass {PLAN_ID_ARG}=\"{plan.plan_id}\" with every call of this task, "
        "including hub__batch.",
        f"Environment: {plan.env or 'not set (child defaults)'}.",
        f"Writes allowed: {', '.join(sorted(plan.writes)) or 'none'}.",
        "Enforced: only these children; the plan's environment; no undeclared writes; connection checks only "
        "after a failed call; no identical repeats. Batch independent reads with hub__batch.",
    ]
    lines.extend(_scope_lines(plan))
    for server_id in sorted(plan.servers):
        lines.append(f"\n## {server_id}\nTools: {', '.join(tools_by_server.get(server_id, []))}")
        if contracts.get(server_id):
            lines.append(contracts[server_id])
    return "\n".join(lines)
