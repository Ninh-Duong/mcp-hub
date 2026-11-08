"""The usage policy: `hub__plan`, its checks, and the Hub runtime's gate on direct client calls."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mcp.types import CallToolRequestParams, CallToolResult, TextContent, Tool

from mcp_hub import policy
from mcp_hub.config import ConfigError, HubConfig, ServerConfig, ToolRoute
from mcp_hub.playbooks import load_playbooks, parse_playbook
from mcp_hub.policy import CallFacts, PlanBook, PlanCatalog, PolicyRefused
from mcp_hub.projects import LogTarget, ProjectConfig
from mcp_hub.server import HubRuntime
from mcp_hub.settings import PolicySettings, Settings, parse_settings

HEALTH = frozenset({"logs__check"})
FACTS = {
    "logs__query": CallFacts("logs__query", "logs-child", True, True),
    "logs__export": CallFacts("logs__export", "logs-child", False, True),
    "logs__check": CallFacts("logs__check", "logs-child", True, False),
    "jira__read": CallFacts("jira__read", "jira-child", True, False),
}
CATALOG = PlanCatalog(FACTS)


def plan_args(**overrides) -> dict:
    return {"task": "Find the failed order", "servers": ["logs-child"], "steps": ["logs__query"], **overrides}


class ParsePlanTest(unittest.TestCase):
    def test_a_valid_plan_keeps_its_scope(self) -> None:
        plan = policy.parse_plan(plan_args(env="stg", writes=["logs__export"], connection_checks=True), CATALOG, "p")
        self.assertEqual((plan.servers, plan.env, plan.writes), (frozenset({"logs-child"}), "stg", frozenset({"logs__export"})))
        self.assertTrue(plan.connection_checks)

    def test_window_parsing_in_plan_and_summary(self) -> None:
        args = plan_args(since="2026-11-08T00:00:00Z", until="2026-11-08T12:00:00Z")
        plan = policy.parse_plan(args, CATALOG, "p-window")
        self.assertIsNotNone(plan.window)
        summary = policy.plan_summary(plan, {"logs-child": ["logs__query"]}, {})
        self.assertIn("Time window: 2026-11-08T00:00:00+00:00 to 2026-11-08T12:00:00+00:00 (UTC).", summary)

    def test_over_limit_window_in_plan_is_refused(self) -> None:
        args = plan_args(since="2026-11-01T00:00:00Z", until="2026-11-08T12:00:00Z")
        cat = PlanCatalog(FACTS, max_window_hours=24)
        with self.assertRaises(PolicyRefused):
            policy.parse_plan(args, cat, "p-over")

    def test_invalid_plans_are_refused_with_the_fix(self) -> None:
        cases = {
            "no task": plan_args(task=" "),
            "unknown server": plan_args(servers=["other"]),
            "no servers": plan_args(servers=[]),
            "no steps": plan_args(steps=[]),
            "steps not a list": plan_args(steps="read"),
            "read-only write": plan_args(writes=["logs__query"]),
            "write of another child": plan_args(servers=["jira-child"], writes=["logs__export"]),
            "env not text": plan_args(env=3),
            "checks not a flag": plan_args(connection_checks="yes"),
            "too many steps": plan_args(steps=["x"] * (policy.MAX_STEPS + 1)),
        }
        for label, arguments in cases.items():
            with self.subTest(label), self.assertRaises(PolicyRefused):
                policy.parse_plan(arguments, CATALOG, "p")


class CheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = policy.parse_plan(plan_args(env="stg"), CATALOG, "p")

    def rule(self, name: str, arguments: dict, plan=..., plan_id=None) -> str | None:
        violation = policy.check(self.plan if plan is ... else plan, FACTS[name], arguments, HEALTH, plan_id)
        return None if violation is None else violation.rule

    def test_a_call_inside_the_plan_passes(self) -> None:
        self.assertIsNone(self.rule("logs__query", {"env": "stg"}))

    def test_each_rule_refuses_its_case(self) -> None:
        self.assertEqual(self.rule("logs__query", {}, plan=None), "no_plan")
        self.assertEqual(self.rule("logs__query", {}, plan=None, plan_id="plan-x"), "unknown_plan")
        self.assertEqual(self.rule("jira__read", {}), "server")
        self.assertEqual(self.rule("logs__query", {"env": "prod"}), "env")
        self.assertEqual(self.rule("logs__query", {}), "env")
        self.assertEqual(self.rule("logs__export", {"env": "stg"}), "write")
        self.assertEqual(self.rule("logs__check", {}), "health_check")

    def test_a_plan_without_env_leaves_env_to_the_child(self) -> None:
        plan = policy.parse_plan(plan_args(), CATALOG, "p")
        self.assertIsNone(self.rule("logs__query", {}, plan=plan))
        self.assertIsNone(self.rule("logs__query", {"env": "dev"}, plan=plan))

    def test_declared_writes_and_checks_pass(self) -> None:
        plan = policy.parse_plan(plan_args(writes=["logs__export"], connection_checks=True), CATALOG, "p")
        self.assertIsNone(self.rule("logs__export", {}, plan=plan))
        self.assertIsNone(self.rule("logs__check", {}, plan=plan))

    def test_a_failure_allows_a_connection_check_and_one_retry(self) -> None:
        policy.record(self.plan, "logs__query", {"env": "stg"}, ok=False)
        self.assertIsNone(self.rule("logs__check", {}))
        self.assertIsNone(self.rule("logs__query", {"env": "stg"}))
        policy.record(self.plan, "logs__query", {"env": "stg"}, ok=False)
        self.assertEqual(self.rule("logs__query", {"env": "stg"}), "retry")

    def test_a_succeeded_call_is_not_repeated(self) -> None:
        policy.record(self.plan, "logs__query", {"env": "stg", "q": "a"}, ok=True)
        self.assertEqual(self.rule("logs__query", {"q": "a", "env": "stg"}), "repeat")
        self.assertIsNone(self.rule("logs__query", {"env": "stg", "q": "b"}))
        policy.record(self.plan, "logs__query", {"env": "stg", "q": "a"}, ok=False)  # a success stays a success
        self.assertEqual(self.rule("logs__query", {"q": "a", "env": "stg"}), "repeat")

    def test_record_without_a_plan_is_a_no_op(self) -> None:
        policy.record(None, "logs__query", {}, ok=True)

    def test_a_batch_reports_the_first_breaking_call_and_inner_repeats(self) -> None:
        good = (FACTS["logs__query"], {"env": "stg"})
        self.assertIsNone(policy.check_batch(self.plan, [good], HEALTH))
        self.assertEqual(policy.check_batch(self.plan, [good, good], HEALTH)[0], 2)
        index, violation = policy.check_batch(self.plan, [good, (FACTS["jira__read"], {})], HEALTH)
        self.assertEqual((index, violation.rule), (2, "server"))


class PlanBookTest(unittest.TestCase):
    def test_plans_are_found_by_id_and_the_oldest_is_dropped(self) -> None:
        book = PlanBook(max_plans=2)
        first = book.create(plan_args(), CATALOG)
        second = book.create(plan_args(), CATALOG)
        self.assertIs(book.get(first.plan_id), first)  # now the most recently used
        book.create(plan_args(), CATALOG)
        self.assertIsNone(book.get(second.plan_id))
        self.assertIs(book.get(first.plan_id), first)
        self.assertIsNone(book.get(None))
        self.assertIsNone(book.get(123))

    def test_schema_and_argument_helpers(self) -> None:
        schema = policy.with_plan_id({"type": "object", "properties": {"env": {"type": "string"}}})
        self.assertEqual(set(schema["properties"]), {"env", policy.PLAN_ID_ARG})
        self.assertIn(policy.PLAN_ID_ARG, policy.with_plan_id(None)["properties"])
        self.assertEqual(policy.split_plan_id({"a": 1, "hub_plan_id": "p"}), ("p", {"a": 1}))
        self.assertTrue(policy.tool_accepts_env(Tool(name="t", input_schema=schema)))
        self.assertFalse(policy.tool_accepts_env(Tool(name="t", input_schema={"type": "object"})))


class PolicySettingsTest(unittest.TestCase):
    def test_defaults_and_overrides(self) -> None:
        self.assertEqual(parse_settings({}).policy.mode, "enforce")
        parsed = parse_settings({"policy": {"mode": "warn", "maxPlans": 10, "healthCheckTools": ["a__b"]}}).policy
        self.assertEqual((parsed.mode, parsed.max_plans, parsed.health_check_tools), ("warn", 10, ("a__b",)))

    def test_bad_values_are_refused(self) -> None:
        for raw in ({"mode": "strict"}, {"healthCheckTools": "a__b"}, {"healthCheckTools": [""]}, {"maxPlans": 0}):
            with self.subTest(raw=raw), self.assertRaises(ConfigError):
                parse_settings({"policy": raw})


ROUTES = (
    ToolRoute("logs", "logs-child", "query", "logs__query"),
    ToolRoute("logs", "logs-child", "export", "logs__export"),
    ToolRoute("logs", "logs-child", "check", "logs__check"),
    ToolRoute("jira", "jira-child", "read", "jira__read"),
)
ENV_SCHEMA = {"type": "object", "properties": {"env": {"type": "string"}}, "additionalProperties": False}


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail: set[str] = set()

    async def call_tool(self, name: str, arguments: dict) -> CallToolResult:
        self.calls.append((name, arguments))
        return CallToolResult(content=[TextContent(type="text", text=f"{name} ok")], is_error=name in self.fail)


class RuntimeGateTest(unittest.IsolatedAsyncioTestCase):
    mode = "enforce"

    async def asyncSetUp(self) -> None:
        root = Path(tempfile.mkdtemp())
        servers = {sid: ServerConfig(sid, "python", (), root, {}, ()) for sid in ("logs-child", "jira-child")}
        settings = Settings(policy=PolicySettings(mode=self.mode, health_check_tools=("logs__check",)))
        self.audit = MagicMock()
        self.runtime = HubRuntime(HubConfig(root / "hub.json", servers, ROUTES), self.audit, settings)
        self.logs, self.jira = RecordingClient(), RecordingClient()
        self.runtime.child_connected("logs-child", self.logs, {
            "query": Tool(name="query", input_schema=ENV_SCHEMA, annotations={"readOnlyHint": True}),
            "export": Tool(name="export", input_schema={"type": "object"}),
            "check": Tool(name="check", input_schema={"type": "object"}, annotations={"readOnlyHint": True}),
        })
        self.runtime.child_connected("jira-child", self.jira, {
            "read": Tool(name="read", input_schema={"type": "object"}, annotations={"readOnlyHint": True}),
        })

    async def call(self, name: str, arguments: dict) -> CallToolResult:
        return await self.runtime.call_tool(SimpleNamespace(request_id=1), CallToolRequestParams(name=name, arguments=arguments))

    async def plan(self, **overrides) -> str:
        result = await self.call("hub__plan", plan_args(**overrides))
        self.assertFalse(result.is_error, result.content[0].text)
        return re.search(r'hub_plan_id="([^"]+)"', result.content[0].text)[1]

    def events(self, name: str) -> list[dict]:
        return [c.kwargs for c in self.audit.write.call_args_list if c.args[0] == name]


class EnforceGateTest(RuntimeGateTest):
    async def test_published_tools_take_a_plan_id(self) -> None:
        listed = {tool.name: tool for tool in self.runtime.public_tools}
        self.assertIn("hub__plan", listed)
        for name in ("logs__query", "hub__batch"):
            self.assertIn(policy.PLAN_ID_ARG, listed[name].input_schema["properties"])

    async def test_a_call_without_a_plan_never_reaches_the_child(self) -> None:
        result = await self.call("logs__query", {"env": "stg"})
        self.assertTrue(result.is_error)
        self.assertIn("Call hub__plan first", result.content[0].text)
        self.assertEqual(self.logs.calls, [])
        self.assertEqual(self.events("tool.call.denied")[0]["reason"], "policy:no_plan")

    async def test_a_planned_call_reaches_the_child_without_the_plan_id(self) -> None:
        plan_id = await self.plan(env="stg")
        result = await self.call("logs__query", {"env": "stg", "hub_plan_id": plan_id})
        self.assertFalse(result.is_error)
        self.assertEqual(self.logs.calls, [("query", {"env": "stg"})])
        repeat = await self.call("logs__query", {"env": "stg", "hub_plan_id": plan_id})
        self.assertIn("already ran", repeat.content[0].text)
        created = self.events("hub.plan.created")[0]
        self.assertEqual((created["servers"], created["env_set"], created["steps"]), (["logs-child"], True, 1))
        self.assertNotIn("task", created)

    async def test_the_plan_returns_each_childs_contract(self) -> None:
        with patch("mcp_hub.server.child_contract", return_value="CONTRACT TEXT"):
            result = await self.call("hub__plan", plan_args())
        self.assertIn("CONTRACT TEXT", result.content[0].text)
        self.assertIn("logs__query", result.content[0].text)

    async def test_an_invalid_plan_is_refused(self) -> None:
        result = await self.call("hub__plan", plan_args(servers=["nope"]))
        self.assertTrue(result.is_error)
        self.assertEqual(self.events("tool.call.denied")[0]["reason"], "policy:plan")

    async def test_an_undeclared_write_and_an_early_check_are_refused(self) -> None:
        plan_id = await self.plan()
        self.assertTrue((await self.call("logs__export", {"hub_plan_id": plan_id})).is_error)
        self.assertTrue((await self.call("logs__check", {"hub_plan_id": plan_id})).is_error)
        self.assertEqual(self.logs.calls, [])
        self.logs.fail.add("query")
        await self.call("logs__query", {"hub_plan_id": plan_id})
        self.assertFalse((await self.call("logs__check", {"hub_plan_id": plan_id})).is_error)

    async def test_a_batch_with_one_breaking_call_is_refused_whole(self) -> None:
        plan_id = await self.plan(env="stg")
        calls = [{"tool": "logs__query", "arguments": {"env": "stg"}}, {"tool": "jira__read", "arguments": {}}]
        result = await self.call("hub__batch", {"hub_plan_id": plan_id, "calls": calls})
        self.assertTrue(result.is_error)
        self.assertIn("call 2", result.content[0].text)
        self.assertEqual((self.logs.calls, self.jira.calls), ([], []))

    async def test_a_planned_batch_runs_and_records_its_calls(self) -> None:
        plan_id = await self.plan(servers=["logs-child", "jira-child"], env="stg")
        calls = [{"tool": "logs__query", "arguments": {"env": "stg", "hub_plan_id": plan_id}},
                 {"tool": "jira__read", "arguments": {}}]
        result = await self.call("hub__batch", {"hub_plan_id": plan_id, "calls": calls})
        self.assertFalse(result.is_error)
        self.assertEqual(self.logs.calls, [("query", {"env": "stg"})])
        again = await self.call("jira__read", {"hub_plan_id": plan_id})
        self.assertIn("already ran", again.content[0].text)

    async def test_the_job_runner_path_is_not_gated(self) -> None:
        result = await self.runtime.call_child_tool("logs__query", {"env": "dev", "hub_plan_id": "x"})
        self.assertFalse(result.is_error)
        self.assertEqual(self.logs.calls, [("query", {"env": "dev"})])


class WarnGateTest(RuntimeGateTest):
    mode = "warn"

    async def test_a_breaking_call_runs_with_a_warning(self) -> None:
        result = await self.call("logs__query", {"env": "stg"})
        self.assertFalse(result.is_error)
        self.assertTrue(result.content[0].text.startswith("Policy warning:"))
        self.assertEqual(result.content[1].text, "query ok")
        self.assertEqual(self.events("tool.call.policy_warned")[0]["reason"], "policy:no_plan")

    async def test_a_batch_is_not_refused(self) -> None:
        calls = [{"tool": "logs__query", "arguments": {}}, {"tool": "jira__read", "arguments": {}}]
        self.assertFalse((await self.call("hub__batch", {"calls": calls})).is_error)


class OffGateTest(RuntimeGateTest):
    mode = "off"

    async def test_no_plan_is_needed_and_the_plan_id_is_still_removed(self) -> None:
        result = await self.call("logs__export", {"hub_plan_id": "anything"})
        self.assertEqual(result.content[0].text, "export ok")
        self.assertEqual(self.logs.calls, [("export", {})])
        self.assertEqual(self.events("tool.call.policy_warned"), [])


PLAYBOOK = parse_playbook("""---
id: find-request
when: The user gives an rcid.
servers: [logs-child]
tools: [logs__query, logs__export]
max_limit: 100
needs: [logs]
---
## Steps
1. Query by rcid.
""", "find-request")
TARGET = LogTarget("org-1", "stream-1", "rcid")
PROJECTS = {"orders": ProjectConfig("orders", ("dev", "stg"), "stg", {"stg": TARGET})}
PLAYBOOK_CATALOG = PlanCatalog(FACTS, {"find-request": PLAYBOOK}, PROJECTS)


def playbook_args(**overrides) -> dict:
    return {"task": "Why did rcid 1 fail?", "playbook": "find-request", "project": "orders", **overrides}


class PlaybookPlanTest(unittest.TestCase):
    def test_a_playbook_plan_takes_servers_steps_env_and_targets(self) -> None:
        plan = policy.parse_plan(playbook_args(), PLAYBOOK_CATALOG, "p")
        self.assertEqual((plan.servers, plan.env, plan.target, plan.project), (frozenset({"logs-child"}), "stg", TARGET, "orders"))
        self.assertEqual(plan.steps, ("Follow playbook find-request.",))

    def test_playbook_plans_are_refused_without_their_facts(self) -> None:
        cases = {
            "unknown playbook": playbook_args(playbook="other"),
            "no project": {"task": "x", "playbook": "find-request"},
            "unknown project": playbook_args(project="billing"),
            "env not allowed": playbook_args(env="prod"),
            "no log targets for env": playbook_args(env="dev"),
            "server outside playbook": playbook_args(servers=["jira-child"]),
            "write outside playbook": playbook_args(writes=["jira__read"]),
        }
        for label, arguments in cases.items():
            with self.subTest(label), self.assertRaises(PolicyRefused):
                policy.parse_plan(arguments, PLAYBOOK_CATALOG, "p")
        with self.assertRaisesRegex(PolicyRefused, "Ask the user to add logs.dev"):
            policy.parse_plan(playbook_args(env="dev"), PLAYBOOK_CATALOG, "p")

    def test_a_project_without_a_playbook_scopes_source_calls(self) -> None:
        plan = policy.parse_plan(plan_args(project="orders", env="dev"), PLAYBOOK_CATALOG, "p")
        self.assertEqual((plan.project, plan.env, plan.target), ("orders", "dev", None))

    def test_playbook_rules(self) -> None:
        plan = policy.parse_plan(playbook_args(), PLAYBOOK_CATALOG, "p")

        def rule(name: str, arguments: dict) -> str | None:
            violation = policy.check(plan, FACTS[name], {"env": "stg", **arguments}, HEALTH)
            return None if violation is None else violation.rule

        self.assertIsNone(rule("logs__query", {"organization": "org-1", "stream": "stream-1", "limit": 100}))
        self.assertEqual(rule("logs__check", {}), "playbook_tool")
        self.assertEqual(rule("logs__query", {"limit": 500}), "limit")
        self.assertEqual(rule("logs__query", {"organization": "org-2"}), "target")
        self.assertEqual(rule("logs__query", {"stream": "other"}), "target")
        self.assertEqual(rule("logs__query", {"field": "trace_id"}), "target")
        self.assertEqual(rule("logs__query", {"project": "billing"}), "target")

    def test_the_answer_carries_the_steps_and_targets(self) -> None:
        plan = policy.parse_plan(playbook_args(), PLAYBOOK_CATALOG, "p")
        text = policy.plan_summary(plan, {"logs-child": ["logs__query"]}, {})
        for expected in ("organization='org-1'", "stream='stream-1'", "field='rcid'", "project='orders'",
                         "limit at most 100", "Query by rcid."):
            self.assertIn(expected, text)

    def test_the_plan_tool_lists_the_playbooks(self) -> None:
        tool = policy.plan_tool({"find-request": PLAYBOOK})
        self.assertIn("- find-request: The user gives an rcid.", tool.description)
        self.assertEqual(tool.input_schema["properties"]["playbook"]["enum"], ["find-request"])
        self.assertNotIn("enum", policy.plan_tool().input_schema["properties"]["playbook"])


class PlaybookRuntimeTest(RuntimeGateTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.runtime.playbooks = {"find-request": PLAYBOOK}
        self.runtime.projects = PROJECTS

    async def test_a_playbook_plan_is_audited_without_its_targets(self) -> None:
        result = await self.call("hub__plan", playbook_args())
        self.assertFalse(result.is_error, result.content[0].text)
        plan_id = re.search(r'hub_plan_id="([^"]+)"', result.content[0].text)[1]
        created = self.events("hub.plan.created")[0]
        self.assertEqual((created["playbook"], created["project_set"]), ("find-request", True))
        self.assertNotIn("org-1", str(self.audit.write.call_args_list))
        refused = await self.call("logs__query", {"env": "stg", "limit": 500, "hub_plan_id": plan_id})
        self.assertIn("limit 500", refused.content[0].text)
        self.assertEqual(self.events("tool.call.denied")[-1]["reason"], "policy:limit")

    async def test_the_runtime_loads_the_repository_playbooks_by_default(self) -> None:
        self.assertEqual(set(HubRuntime(self.runtime.config, self.audit).playbooks), set(load_playbooks()))
        self.assertIn("rcid-bug", policy.plan_tool(load_playbooks()).description)


class SummaryTest(unittest.TestCase):
    def test_the_instruction_summary_fits_codex_and_names_the_plan(self) -> None:
        from mcp_hub.server import INSTRUCTIONS_SUMMARY

        self.assertLessEqual(len(INSTRUCTIONS_SUMMARY), 512)
        self.assertIn("hub__plan", INSTRUCTIONS_SUMMARY)


if __name__ == "__main__":
    unittest.main()
