"""The usage policy: `hub__plan`, its checks, and the Hub runtime's gate on direct client calls."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.types import CallToolRequestParams, CallToolResult, TextContent, Tool

from mcp_hub.client_profiles import load_client_profiles
from mcp_hub.planning import confirm as plan_confirm, policy
from mcp_hub.config import ConfigError, HubConfig, ServerConfig, ToolRoute
from mcp_hub.planning.playbooks import Playbook, load_playbooks, parse_playbook
from mcp_hub.planning.policy import CallFacts, PlanBook, PlanCatalog, PolicyRefused
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
        settings = Settings(policy=PolicySettings(mode=self.mode, health_check_tools=("logs__check",), confirm_playbook="off"))
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


class ConfirmGateTest(RuntimeGateTest):
    """With `always` (the default), a plan waits for the user's choice of playbook and a single-use token."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.runtime.settings = Settings(policy=PolicySettings(mode="enforce", confirm_playbook="always"))
        self.runtime.confirm_tokens = plan_confirm.ConfirmTokens(600)

    async def test_a_plan_without_a_token_is_offered_and_not_stored(self) -> None:
        result = await self.call("hub__plan", plan_args())
        self.assertFalse(result.is_error)
        self.assertIn("confirm_token:", result.content[0].text)
        self.assertNotIn("hub_plan_id", result.content[0].text)
        self.assertEqual(len(self.events("hub.plan.confirm_requested")), 1)
        self.assertEqual(self.events("hub.plan.created"), [])

    async def test_the_offered_token_creates_the_plan_once(self) -> None:
        offer = await self.call("hub__plan", plan_args())
        token = re.search(r"confirm_token: (\S+?)\.?$", offer.content[0].text, re.M)[1]
        result = await self.call("hub__plan", plan_args(confirm_token=token))
        self.assertFalse(result.is_error, result.content[0].text)
        self.assertRegex(result.content[0].text, r'hub_plan_id="')
        again = await self.call("hub__plan", plan_args(confirm_token=token))
        self.assertTrue(again.is_error)
        self.assertIn("Policy:", again.content[0].text)
        self.assertEqual(len(self.events("hub.plan.confirm_refused")), 1)

    async def test_a_token_is_refused_for_a_different_task(self) -> None:
        offer = await self.call("hub__plan", plan_args())
        token = re.search(r"confirm_token: (\S+?)\.?$", offer.content[0].text, re.M)[1]
        result = await self.call("hub__plan", plan_args(task="another task", confirm_token=token))
        self.assertTrue(result.is_error)

    async def test_the_planner_is_skipped_once_a_playbook_is_chosen(self) -> None:
        self.runtime.plan_assistant = MagicMock()
        self.runtime.plan_assistant.hints = AsyncMock(return_value=None)
        _args, assist = await self.runtime._assist_plan({"task": "Find the failed order", "playbook": "find-request"})
        self.assertEqual(assist, "off")
        self.runtime.plan_assistant.hints.assert_not_called()


class ExecutionConfirmGateTest(RuntimeGateTest):
    """Per-client session execution mode/model confirmation and defaults."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.tmp_profiles = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_profiles.cleanup)
        self.runtime.client_profiles = load_client_profiles(private_root=self.tmp_profiles.name)
        self.runtime.shared_service = True
        self.runtime.settings = Settings(
            policy=PolicySettings(mode="enforce", confirm_playbook="always", confirm_execution="always"),
        )
        self.runtime.confirm_tokens = plan_confirm.ConfirmTokens(600)

    def _ctx(self, client_name: str) -> SimpleNamespace:
        return SimpleNamespace(
            request_id=1,
            session=SimpleNamespace(client_params=SimpleNamespace(client_info=SimpleNamespace(name=client_name))),
        )

    async def call_as(self, client_name: str, name: str, arguments: dict) -> CallToolResult:
        return await self.runtime.call_tool(self._ctx(client_name), CallToolRequestParams(name=name, arguments=arguments))

    @staticmethod
    def _token(result: CallToolResult) -> str:
        return re.search(r"confirm_token: (\S+?)\.?$", result.content[0].text, re.M)[1]

    async def test_offer_lists_antigravity_options_and_redeem_requires_valid_execution_choices(self) -> None:
        offer = await self.call_as("Antigravity", "hub__plan", plan_args())
        self.assertFalse(offer.is_error)
        text = offer.content[0].text
        self.assertIn("confirm_token:", text)
        self.assertIn("Playbook choices", text)
        for expected in ("multi_agent", "single_agent", "pro", "flash", "flash_lite"):
            self.assertIn(expected, text)

        refused_missing = await self.call_as("Antigravity", "hub__plan", plan_args(confirm_token=self._token(offer)))
        self.assertTrue(refused_missing.is_error)
        self.assertEqual(len(self.events("hub.plan.confirm_refused")), 1)

        bad_model_offer = await self.call_as("Antigravity", "hub__plan", plan_args())
        refused_model = await self.call_as("Antigravity", "hub__plan", plan_args(
            confirm_token=self._token(bad_model_offer),
            execution_mode="multi_agent", orchestrate_model="pro", execute_model="unknown-model",
        ))
        self.assertTrue(refused_model.is_error)

        mismatch_offer = await self.call_as("Antigravity", "hub__plan", plan_args())
        refused_client = await self.call_as("Claude Code", "hub__plan", plan_args(
            confirm_token=self._token(mismatch_offer),
            execution_mode="multi_agent", orchestrate_model="sonnet", execute_model="haiku",
        ))
        self.assertTrue(refused_client.is_error)

        good_offer = await self.call_as("Antigravity", "hub__plan", plan_args())
        created = await self.call_as("Antigravity", "hub__plan", plan_args(
            confirm_token=self._token(good_offer),
            execution_mode="multi_agent", orchestrate_model="pro", execute_model="flash",
        ))
        self.assertFalse(created.is_error, created.content[0].text)
        self.assertIn("Session execution (antigravity): mode=multi_agent, orchestrate=pro, execute=flash.",
                      created.content[0].text)
        event = self.events("hub.plan.created")[-1]
        self.assertEqual((event["client_profile"], event["execution_mode"]), ("antigravity", "multi_agent"))

    async def test_explicit_client_mismatch_on_unknown_client_is_refused(self) -> None:
        offer = await self.call_as("unknown", "hub__plan", plan_args(client="antigravity"))
        self.assertFalse(offer.is_error)
        refused = await self.call_as("unknown", "hub__plan", plan_args(
            client="claude-code",
            confirm_token=self._token(offer),
            execution_mode="multi_agent", orchestrate_model="sonnet", execute_model="haiku",
        ))
        self.assertTrue(refused.is_error)

    async def test_confirm_token_profile_binding_blocks_omitting_or_adding_client(self) -> None:
        self.runtime.settings = Settings(
            policy=PolicySettings(mode="enforce", confirm_playbook="off", confirm_execution="always"),
        )
        offer = await self.call_as("unknown", "hub__plan", plan_args(client="antigravity"))
        refused_omitted = await self.call_as("unknown", "hub__plan", plan_args(confirm_token=self._token(offer)))
        self.assertTrue(refused_omitted.is_error)

        self.runtime.settings = Settings(
            policy=PolicySettings(mode="enforce", confirm_playbook="always", confirm_execution="always"),
        )
        default_offer = await self.call_as("unknown", "hub__plan", plan_args())
        refused_switched = await self.call_as("unknown", "hub__plan", plan_args(
            client="antigravity", confirm_token=self._token(default_offer),
            execution_mode="multi_agent", orchestrate_model="pro", execute_model="flash",
        ))
        self.assertTrue(refused_switched.is_error)

        ws = await self.call_as("Antigravity", "hub__plan", plan_args(task="   "))
        self.assertTrue(ws.is_error)

    async def test_confirm_playbook_off_with_confirm_execution_always(self) -> None:
        self.runtime.settings = Settings(
            policy=PolicySettings(mode="enforce", confirm_playbook="off", confirm_execution="always"),
        )
        offer = await self.call_as("Antigravity", "hub__plan", plan_args())
        self.assertFalse(offer.is_error)
        self.assertIn("confirm_token:", offer.content[0].text)
        self.assertNotIn("Playbook choices", offer.content[0].text)

        created = await self.call_as("Antigravity", "hub__plan", plan_args(
            confirm_token=self._token(offer),
            execution_mode="single_agent", orchestrate_model="pro",
        ))
        self.assertFalse(created.is_error, created.content[0].text)
        self.assertIn("Session execution (antigravity): mode=single_agent, orchestrate=pro, execute=pro.",
                      created.content[0].text)

    async def test_confirm_execution_off_applies_profile_defaults(self) -> None:
        self.runtime.settings = Settings(
            policy=PolicySettings(mode="enforce", confirm_playbook="off", confirm_execution="off"),
        )
        created = await self.call_as("Antigravity", "hub__plan", plan_args())
        self.assertFalse(created.is_error, created.content[0].text)
        self.assertIn("Session execution (antigravity): mode=multi_agent, orchestrate=pro, execute=flash.",
                      created.content[0].text)
        event = self.events("hub.plan.created")[-1]
        self.assertEqual((event["client_profile"], event["execution_mode"]), ("antigravity", "multi_agent"))

    async def test_isolated_stdio_restricts_antigravity_to_single_agent(self) -> None:
        self.runtime.shared_service = False
        self.runtime.settings = Settings(
            policy=PolicySettings(mode="enforce", confirm_playbook="off", confirm_execution="always"),
        )
        offer = await self.call_as("Antigravity", "hub__plan", plan_args())
        self.assertFalse(offer.is_error)
        self.assertIn("only single_agent is available on isolated stdio", offer.content[0].text)
        refused = await self.call_as("Antigravity", "hub__plan", plan_args(
            confirm_token=self._token(offer),
            execution_mode="multi_agent", orchestrate_model="pro", execute_model="flash",
        ))
        self.assertTrue(refused.is_error)

        offer_two = await self.call_as("Antigravity", "hub__plan", plan_args())
        ok = await self.call_as("Antigravity", "hub__plan", plan_args(
            confirm_token=self._token(offer_two),
            execution_mode="single_agent", orchestrate_model="pro",
        ))
        self.assertFalse(ok.is_error, ok.content[0].text)
        self.assertIn("Session execution (antigravity): mode=single_agent, orchestrate=pro, execute=pro.",
                      ok.content[0].text)


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
        with patch("mcp_hub.instructions.child_contract", return_value="CONTRACT TEXT"):
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


class NeedsRegistrationTest(unittest.TestCase):
    def test_a_source_playbook_on_an_unregistered_project_names_the_alias(self) -> None:
        source = Playbook("source-search", "Search code.", ("logs-child",), ("query",), None, ("source",), (), "")
        catalog = PlanCatalog(FACTS, {"source-search": source}, {})
        with self.assertRaises(policy.NeedsRegistration) as raised:
            policy.parse_plan(plan_args(playbook="source-search", project="wec.be"), catalog, "p")
        self.assertEqual(raised.exception.alias, "wec.be")
        self.assertIsInstance(raised.exception, PolicyRefused)


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

    async def test_plan_allows_builtin_full_result_in_batch_and_includes_sql_hints(self) -> None:
        from mcp_hub.results.full_results import FullResultStore
        from mcp_hub.projects import SqlTarget

        store = FullResultStore(max_entries=10, ttl_seconds=60.0)
        rid = store.put("retrieved full payload")
        self.runtime.full_results = store
        self.runtime.projects = {
            "orders": ProjectConfig("orders", ("dev", "stg"), "stg", {"stg": TARGET}, {"stg": SqlTarget("DbMain", ("DbMain",))}),
        }
        plan_res = await self.call("hub__plan", playbook_args())
        self.assertIn("SQL hints for stg: default_database='DbMain'; databases=[DbMain].", plan_res.content[0].text)
        plan_id = re.search(r'hub_plan_id="([^"]+)"', plan_res.content[0].text)[1]
        batch_res = await self.call("hub__batch", {
            "hub_plan_id": plan_id,
            "calls": [
                {"tool": "logs__query", "arguments": {"env": "stg"}},
                {"tool": "hub__get_full_result", "arguments": {"result_id": rid}},
            ],
        })
        self.assertFalse(batch_res.is_error, batch_res.content[0].text)
        self.assertIn("retrieved full payload", "\n".join(b.text for b in batch_res.content))


class SummaryTest(unittest.TestCase):
    def test_the_instruction_summary_fits_codex_and_names_the_plan(self) -> None:
        from mcp_hub.instructions import INSTRUCTIONS_SUMMARY

        self.assertLessEqual(len(INSTRUCTIONS_SUMMARY), 512)
        self.assertIn("hub__plan", INSTRUCTIONS_SUMMARY)


class SchemaRewriteReexportTest(unittest.TestCase):
    def test_policy_reexports_the_schema_rewrite_names(self) -> None:
        from mcp_hub import schema_rewrite

        self.assertIs(policy.with_plan_id, schema_rewrite.with_plan_id)
        self.assertIs(policy.PLAN_ID_ARG, schema_rewrite.PLAN_ID_ARG)


class PlanIdLengthTest(unittest.TestCase):
    def test_plan_ids_carry_128_bits(self) -> None:
        plan = PlanBook(max_plans=2).create(plan_args(), CATALOG)
        self.assertRegex(plan.plan_id, r"^plan-[0-9a-f]{32}$")


class RepeatAfterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = [0.0]

    def plan(self, repeat_after_seconds: float) -> policy.Plan:
        book = PlanBook(max_plans=2, clock=lambda: self.now[0], repeat_after_seconds=repeat_after_seconds)
        plan = book.create(plan_args(env="stg"), CATALOG)
        policy.record(plan, "logs__query", {"env": "stg"}, ok=True)
        return plan

    def repeat_rule(self, plan: policy.Plan) -> str | None:
        violation = policy.check(plan, FACTS["logs__query"], {"env": "stg"}, HEALTH, plan.plan_id)
        return None if violation is None else violation.rule

    def test_a_repeat_is_refused_at_zero_seconds(self) -> None:
        self.assertEqual(self.repeat_rule(self.plan(60)), "repeat")

    def test_a_repeat_is_allowed_once_the_window_has_passed(self) -> None:
        plan = self.plan(60)
        self.now[0] = 59.0
        self.assertEqual(self.repeat_rule(plan), "repeat")
        self.now[0] = 60.0
        self.assertIsNone(self.repeat_rule(plan))

    def test_a_repeat_that_succeeds_starts_a_new_window(self) -> None:
        plan = self.plan(60)
        self.now[0] = 60.0
        policy.record(plan, "logs__query", {"env": "stg"}, ok=True)
        self.now[0] = 90.0
        self.assertEqual(self.repeat_rule(plan), "repeat")
        self.now[0] = 120.0
        self.assertIsNone(self.repeat_rule(plan))

    def test_a_zero_window_keeps_every_repeat_refused(self) -> None:
        plan = self.plan(0)
        self.now[0] = 10_000.0
        self.assertEqual(self.repeat_rule(plan), "repeat")

    def test_a_repeat_that_fails_counts_and_the_retry_limit_applies(self) -> None:
        plan = self.plan(60)
        self.now[0] = 60.0
        policy.record(plan, "logs__query", {"env": "stg"}, ok=False)
        self.assertIsNone(self.repeat_rule(plan))
        policy.record(plan, "logs__query", {"env": "stg"}, ok=False)
        self.assertEqual(self.repeat_rule(plan), "retry")

    def test_a_failure_while_the_repeat_is_still_refused_keeps_the_success(self) -> None:
        plan = self.plan(60)
        self.now[0] = 30.0
        policy.record(plan, "logs__query", {"env": "stg"}, ok=False)
        self.assertEqual(self.repeat_rule(plan), "repeat")

    def test_the_defaults_come_from_the_settings_and_the_monotonic_clock(self) -> None:
        import time

        book = PlanBook(max_plans=2)
        self.assertEqual(book.repeat_after_seconds, PolicySettings().repeat_after_seconds)
        self.assertEqual(PolicySettings().repeat_after_seconds, 0.0)
        self.assertIs(book.clock, time.monotonic)


class PlanPersistenceTest(unittest.TestCase):
    def test_plan_snapshot_round_trip_ttl_expiry_and_corrupt_recovery(self) -> None:
        from mcp_hub.planning import plan_store

        now = [10_000.0]
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "plans.json"
            book = PlanBook(max_plans=4, persist_path=snap, ttl_seconds=120.0, wall_clock=lambda: now[0])
            created = book.create(plan_args(env="stg"), CATALOG)
            self.assertEqual(book.active_servers(), created.servers)
            book.save_snapshot()
            raw = json.loads(snap.read_text(encoding="utf-8"))
            self.assertEqual(raw["version"], plan_store.SNAPSHOT_VERSION)
            self.assertEqual(len(raw["plans"]), 1)

            # Survives restart within TTL
            now[0] = 10_060.0
            restored = PlanBook(max_plans=4, persist_path=snap, ttl_seconds=120.0, wall_clock=lambda: now[0])
            self.assertEqual(restored.load(CATALOG), 1)
            loaded = restored.get(created.plan_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.servers, created.servers)
            self.assertEqual(loaded.env, "stg")

            # Expired plans are dropped on load
            now[0] = 10_500.0
            expired = PlanBook(max_plans=4, persist_path=snap, ttl_seconds=120.0, wall_clock=lambda: now[0])
            self.assertEqual(expired.load(CATALOG), 0)
            self.assertIsNone(expired.get(created.plan_id))

            # Corrupt snapshot logs plans.snapshot.invalid and starts empty
            snap.write_text("{corrupt", encoding="utf-8")
            audit = MagicMock()
            corrupt = PlanBook(max_plans=4, persist_path=snap, ttl_seconds=120.0, wall_clock=lambda: now[0])
            self.assertEqual(corrupt.load(CATALOG, audit=audit), 0)
            audit.write.assert_called_once()
            self.assertEqual(audit.write.call_args.args[0], "plans.snapshot.invalid")

    def test_workflow_execution_and_window_metadata_round_trip(self) -> None:
        from mcp_hub.client_profiles import SessionExecution
        from mcp_hub.planning.playbooks import load_playbooks

        playbooks = load_playbooks()
        facts = {
            **FACTS,
            "logs__find_openobserve_logs_by_rcid": CallFacts("logs__find_openobserve_logs_by_rcid", "mcp-server-logs", True, True),
            "logs__list_ai_context_snapshots": CallFacts("logs__list_ai_context_snapshots", "mcp-server-logs", True, False),
            "logs__read_ai_context": CallFacts("logs__read_ai_context", "mcp-server-logs", True, False),
            "source__search": CallFacts("source__search", "mcp-read-source", True, False),
        }
        projects = {"orders": ProjectConfig("orders", ("stg",), "stg", {"stg": TARGET})}
        catalog = PlanCatalog(facts, playbooks=playbooks, projects=projects)
        now = [20_000.0]
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "plans.json"
            book = PlanBook(max_plans=4, persist_path=snap, ttl_seconds=600.0, wall_clock=lambda: now[0])
            exec_cfg = SessionExecution("antigravity", "multi_agent", "subagent", "pro", "flash")
            plan = book.create(
                {
                    "task": "Trace rcid", "playbook": "rcid-bug", "project": "orders", "env": "stg",
                    "rcid": "0HN-1", "since": "2026-11-25T00:00:00Z", "until": "2026-11-25T01:00:00Z",
                },
                catalog,
                execution=exec_cfg,
            )
            plan.workflow.record("logs__find_openobserve_logs_by_rcid", "succeeded", 12)
            book.save_snapshot()
            raw = snap.read_text(encoding="utf-8")
            self.assertNotIn(TARGET.organization, raw)  # resolved log targets stay out of snapshot

            reloaded = PlanBook(max_plans=4, persist_path=snap, ttl_seconds=600.0, wall_clock=lambda: now[0])
            self.assertEqual(reloaded.load(catalog), 1)
            loaded = reloaded.get(plan.plan_id)
            self.assertEqual(loaded.execution, exec_cfg)
            self.assertEqual(loaded.target, TARGET)
            self.assertIsNotNone(loaded.window)
            self.assertEqual(len(loaded.workflow.receipts), 1)


if __name__ == "__main__":
    unittest.main()
