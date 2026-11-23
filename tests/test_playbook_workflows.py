"""All five user journeys and their permission/scope boundaries, without upstream services."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from mcp.types import CallToolResult, TextContent

from tests.helpers.workflow import call, create_plan, runtime_fixture
from mcp_hub.planning.workflow_runtime import WORKFLOW_META
from mcp_hub.planning.workflow_definition import parse_definition, compile_definition, WorkflowDefinition
from mcp_hub.config import ConfigError, DOCS_ROOT


class PlaybookJourneysTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime, self.client = runtime_fixture(Path(self.temp.name))

    async def operation(self, pid, action="status", **values):
        return await call(self.runtime, "hub__workflow", {"hub_plan_id": pid, "action": action, **values})

    async def tool(self, pid, name, **arguments):
        return await call(self.runtime, name, {"hub_plan_id": pid, **arguments})

    def receipt(self, result):
        self.assertFalse(result.is_error, result.content)
        return result.meta[WORKFLOW_META]["receipt_id"]

    async def report(self, pid, decision, ref, **values):
        result = await self.operation(pid, "report", decision=decision, refs=[ref], **values)
        self.assertFalse(result.is_error, result.content)
        return json.loads(result.content[0].text)

    async def continuation(self, pid, writes, **extra):
        old = self.runtime.plans.get(pid)
        scope = (await self.operation(pid)).content[0].text
        args = {"task": old.task, "playbook": old.playbook.id, **json.loads(scope)["scope"],
                "writes": writes, "continue_from": pid, **extra}
        result = await call(self.runtime, "hub__plan", args)
        return result

    def plan_id(self, result):
        self.assertFalse(result.is_error, result.content)
        return re.search(r'hub_plan_id="([^"]+)"', result.content[0].text)[1]

    def data(self, **values):
        self.client.result = CallToolResult(content=[TextContent(type="text", text=json.dumps(values))])

    async def test_rcid_missing_time_then_related_service(self):
        pid = await create_plan(self.runtime, since=None, until=None)
        status = json.loads((await self.operation(pid)).content[0].text)
        self.assertEqual(status["stage"], "awaiting_input")
        self.assertIn("since", status["missing_inputs"])
        self.assertTrue((await self.tool(pid, "logs__list_ai_context_snapshots", env="dev")).is_error)
        supplied = await self.operation(pid, "provide_input", inputs={"since": "2026-11-22T00:00:00Z", "until": "2026-11-22T01:00:00Z"})
        self.assertFalse(supplied.is_error, supplied.content)
        ref = self.receipt(await self.tool(pid, "logs__list_ai_context_snapshots", env="dev"))
        await self.report(pid, "cache_miss", ref)
        args = self.logs(pid)
        self.assertTrue((await self.tool(pid, "logs__get_openobserve_service_logs", **args, service="orders-api")).is_error)
        ref = self.receipt(await self.tool(pid, "logs__find_openobserve_logs_by_rcid", **args))
        await self.report(pid, "request_without_error", ref)
        self.assertFalse((await self.operation(pid, "provide_input", inputs={"service": "orders-api"})).is_error)
        ref = self.receipt(await self.tool(pid, "logs__get_openobserve_service_logs", **args, service="orders-api", level="Warning"))
        await self.report(pid, "findings", ref, needs_source=False)
        self.assertFalse((await self.operation(pid, "finish", outcome="supported", refs=[ref])).is_error)

    def logs(self, pid):
        context = self.runtime.plans.get(pid).workflow.context
        return {"env": context["env"], "organization": context["organization"], "stream": context["stream"],
                "field": context["field"], "rcid": context.get("rcid", "request-1"),
                "from": context["since"], "to": context["until"], "limit": 50}

    async def test_service_select_group_request_and_source(self):
        pid = await create_plan(self.runtime, playbook="service-errors", rcid=None)
        ref = self.receipt(await self.tool(pid, "logs__list_ai_context_snapshots", env="dev"))
        await self.report(pid, "cache_miss", ref)
        ref = self.receipt(await self.tool(pid, "logs__list_openobserve_services", env="dev", organization="org", stream="stream"))
        await self.report(pid, "service_selected", ref)
        self.assertFalse((await self.operation(pid, "provide_input", inputs={"service": "orders-api"})).is_error)
        args = self.logs(pid)
        args.pop("rcid")
        self.assertTrue((await self.tool(pid, "logs__get_openobserve_service_logs", **args, service="orders-api", level="Warning")).is_error)
        ref = self.receipt(await self.tool(pid, "logs__get_openobserve_service_logs", **args, service="orders-api", level="Error"))
        await self.report(pid, "need_request", ref)
        self.assertFalse((await self.operation(pid, "provide_input", inputs={"rcid": "request-1"})).is_error)
        ref = self.receipt(await self.tool(pid, "logs__find_openobserve_logs_by_rcid", **self.logs(pid)))
        await self.report(pid, "findings", ref, needs_source=True)
        self.data(matches=[{"path": "app.py", "line": 1}], total=1)
        ref = self.receipt(await self.tool(pid, "source__search", project="orders", query="error"))
        await self.report(pid, "source_found", ref)
        self.assertTrue((await self.tool(pid, "source__read", project="orders", path="other.py")).is_error)
        ref = self.receipt(await self.tool(pid, "source__read", project="orders", path="app.py"))
        await self.report(pid, "source_read", ref)

    async def test_jira_refresh_permissions_and_no_replay(self):
        pid = await create_plan(self.runtime, playbook="jira-ticket-bug", issue_key="EX-1", rcid=None, since=None, until=None)
        self.client.result = CallToolResult(content=[TextContent(type="text", text="Ticket not cached")], is_error=True)
        result = await self.tool(pid, "jira__read_ticket", issue_key="EX-1")
        self.assertTrue(result.is_error)
        ref = result.meta[WORKFLOW_META]["receipt_id"]
        await self.report(pid, "cache_missing", ref)
        before = len(self.client.calls)
        self.assertTrue((await self.tool(pid, "jira__sync_ticket", issue_key="EX-1")).is_error)
        self.assertEqual(len(self.client.calls), before)
        next_pid = self.plan_id(await self.continuation(pid, ["jira__sync_ticket"]))
        self.assertEqual(len(self.client.calls), before)
        self.assertEqual(self.runtime.plans.get(pid).workflow.stage, "superseded")
        self.assertTrue((await self.tool(pid, "jira__read_ticket", issue_key="EX-1")).is_error)
        self.data(ticket="facts")
        self.assertTrue((await self.tool(next_pid, "jira__sync_ticket", issue_key="EX-1", force=True)).is_error)
        ref = self.receipt(await self.tool(next_pid, "jira__sync_ticket", issue_key="EX-1", force=False))
        await self.report(next_pid, "refreshed", ref)
        ref = self.receipt(await self.tool(next_pid, "jira__read_ticket", issue_key="EX-1"))
        await self.report(next_pid, "has_rcid", ref)
        state = json.loads((await self.operation(next_pid)).content[0].text)
        self.assertEqual(state["active_stage"], "rcid-bug.cache_list")
        self.assertIn("since", state["missing_inputs"])
        self.assertIn("rcid", state["missing_inputs"])

    async def test_jira_ticket_read_does_not_require_log_targets(self):
        self.runtime.projects["orders"] = self.runtime.projects["orders"].__class__("orders", ("dev",), "dev")
        pid = await create_plan(self.runtime, playbook="jira-ticket-bug", issue_key="EX-1", rcid=None)
        ref = self.receipt(await self.tool(pid, "jira__read_ticket", issue_key="EX-1"))
        await self.report(pid, "has_service", ref)
        self.assertIn("log_target", json.loads((await self.operation(pid)).content[0].text)["missing_inputs"])

    async def test_access_error_cannot_be_treated_as_cache_miss(self):
        pid = await create_plan(self.runtime, playbook="jira-ticket-bug", issue_key="EX-1")
        self.client.result = CallToolResult(content=[TextContent(type="text", text="No access: not cached")], is_error=True)
        result = await self.tool(pid, "jira__read_ticket", issue_key="EX-1")
        ref = result.meta[WORKFLOW_META]["receipt_id"]
        self.assertTrue((await self.operation(pid, "report", decision="cache_missing", refs=[ref])).is_error)
        self.assertEqual(self.runtime.plans.get(pid).workflow.stage, "blocked")
        self.assertFalse((await self.operation(pid, "finish", outcome="blocked", refs=[ref])).is_error)
        self.assertTrue((await self.continuation(pid, ["jira__sync_ticket"])).is_error)

    async def pr_review(self, revision="one"):
        pid = await create_plan(self.runtime, playbook="pr-review", pr_url="https://example.com/pr/1",
                                project=None, env=None, rcid=None, since=None, until=None)
        self.assertIn("pr__get_pr_context_pack", json.loads((await self.operation(pid)).content[0].text)["required_writes"])
        denied = await self.tool(pid, "pr__get_pr_context_pack", pr_url="https://example.com/pr/1")
        self.assertTrue(denied.is_error)
        self.assertEqual(self.client.calls, [])
        writes = ["pr__get_pr_context_pack", "pr__search_pr_files", "pr__get_pr_file_changes", "pr__get_pr_file_context",
                  "pr__get_pr_comments", "pr__get_pr_comment"]
        pid = self.plan_id(await self.continuation(pid, writes))
        self.data(source_commit=revision)
        ref = self.receipt(await self.tool(pid, "pr__get_pr_context_pack", pr_url="https://example.com/pr/1"))
        await self.report(pid, "context_read", ref)
        ref = self.receipt(await self.tool(pid, "pr__search_pr_files", pr_url="https://example.com/pr/1"))
        await self.report(pid, "files_selected", ref)
        return pid

    async def test_pr_diff_context_order_and_revision_change(self):
        pid = await self.pr_review()
        args = {"pr_url": "https://example.com/pr/1", "file_id": "1"}
        self.assertTrue((await self.tool(pid, "pr__get_pr_file_context", **args)).is_error)
        cross = await self.tool(pid, "hub__batch", calls=[{"tool": "pr__get_pr_file_changes", "arguments": args},
                                                         {"tool": "pr__get_pr_file_context", "arguments": args}])
        self.assertTrue(cross.is_error)
        self.receipt(await self.tool(pid, "pr__get_pr_file_changes", **args))
        self.assertTrue((await self.tool(pid, "pr__get_pr_file_context", **{**args, "file_id": "2"})).is_error)
        ref = self.receipt(await self.tool(pid, "pr__get_pr_file_context", **args))
        self.data(source_commit="two")
        self.receipt(await self.tool(pid, "pr__get_pr_file_changes", **{**args, "file_id": "3"}))
        self.assertEqual(self.runtime.plans.get(pid).workflow.stage, "blocked")
        self.assertTrue((await self.operation(pid, "finish", outcome="supported", refs=[ref])).is_error)

    async def test_pr_review_complete_and_threads(self):
        pid = await self.pr_review()
        args = {"pr_url": "https://example.com/pr/1"}
        ref = self.receipt(await self.tool(pid, "pr__get_pr_file_changes", **args, file_id="1"))
        self.receipt(await self.tool(pid, "pr__get_pr_comments", **args))
        self.receipt(await self.tool(pid, "pr__get_pr_comment", **args, comment_id=1))
        await self.report(pid, "review_complete", ref)
        self.assertFalse((await self.operation(pid, "finish", outcome="supported", refs=[ref])).is_error)

    async def test_pr_without_url_and_source_direct_read(self):
        pid = await create_plan(self.runtime, playbook="pr-review", pr_url=None, since=None, until=None)
        ref = self.receipt(await self.tool(pid, "pr__list_synced_prs"))
        self.assertFalse((await self.operation(pid, "provide_input", inputs={"pr_url": "https://example.com/pr/1"})).is_error)
        await self.report(pid, "pr_selected", ref)
        pid = await create_plan(self.runtime, playbook="source-search", query=None, path="app.py")
        ref = self.receipt(await self.tool(pid, "source__read", project="orders", path="app.py"))
        await self.report(pid, "source_read", ref)

    async def test_source_count_is_bounded_and_scope_fixed(self):
        pid = await create_plan(self.runtime, playbook="source-search", query="retry", glob="*.py")
        self.data(matches=[{"path": "app.py"}], total=200, truncated=True)
        self.assertTrue((await self.tool(pid, "source__search", project="orders", query="other", glob="*.py")).is_error)
        self.assertTrue((await self.tool(pid, "source__search", project="orders", query="retry", glob="*.cs")).is_error)
        ref = self.receipt(await self.tool(pid, "source__search", project="orders", query="retry", glob="*.py"))
        state = await self.report(pid, "answered", ref)
        self.assertFalse(state["receipts"][0]["complete"])
        self.assertIn("500", state["limitations"][0])

    async def test_continuation_refuses_scope_change_and_retains_old_on_failure(self):
        pid = await create_plan(self.runtime, playbook="jira-ticket-bug", issue_key="EX-1")
        for extra in ({"env": "prod"}, {"task": "another task"}, {"rcid": "other"}):
            self.assertTrue((await self.continuation(pid, ["jira__sync_ticket"], **extra)).is_error)
            self.assertNotEqual(self.runtime.plans.get(pid).workflow.stage, "superseded")
        workflow = self.runtime.plans.get(pid).workflow
        async with workflow.lock:
            self.assertTrue((await self.continuation(pid, ["jira__sync_ticket"])).is_error)
        self.assertTrue((await self.continuation(pid, [])).is_error)

    async def test_bad_input_is_atomic_and_no_default_window(self):
        pid = await create_plan(self.runtime, since=None, until=None)
        old = dict(self.runtime.plans.get(pid).workflow.context)
        for values in ({"project": "unknown"}, {"env": "prod"}, {"since": "1h"},
                       {"since": "2026-11-22T00:00:00Z"}, {"query": "x" * 2001}):
            self.assertTrue((await self.operation(pid, "provide_input", inputs=values)).is_error)
            self.assertEqual(self.runtime.plans.get(pid).workflow.context, old)

    async def test_known_service_skips_discovery_and_empty_rows_stop(self):
        pid = await create_plan(self.runtime, playbook="service-errors", service="orders-api", rcid=None)
        ref = self.receipt(await self.tool(pid, "logs__list_ai_context_snapshots", env="dev"))
        state = await self.report(pid, "cache_miss", ref)
        self.assertEqual(state["active_stage"], "logs")
        self.data(total=0, logs=[])
        args = self.logs(pid)
        args.pop("rcid")
        ref = self.receipt(await self.tool(pid, "logs__get_openobserve_service_logs", **args, service="orders-api", level="Error"))
        self.assertFalse((await self.operation(pid, "finish", outcome="insufficient", refs=[ref])).is_error)
        self.assertNotIn("list_openobserve_services", [name for name, _ in self.client.calls])

    async def test_stale_ticket_refresh_reads_new_revision_and_listed_asset_only(self):
        pid = await create_plan(self.runtime, playbook="jira-ticket-bug", issue_key="EX-1")
        self.client.result = CallToolResult(content=[TextContent(type="text", text="Screenshot: [image](assets/screenshot.png)")])
        ref = self.receipt(await self.tool(pid, "jira__read_ticket", issue_key="EX-1"))
        self.assertTrue((await self.tool(pid, "jira__read_asset", issue_key="EX-1", filename="other.png")).is_error)
        self.receipt(await self.tool(pid, "jira__read_asset", issue_key="EX-1", filename="screenshot.png"))
        await self.report(pid, "cache_stale", ref)
        next_pid = self.plan_id(await self.continuation(pid, ["jira__sync_ticket"]))
        ref = self.receipt(await self.tool(next_pid, "jira__sync_ticket", issue_key="EX-1", force=False))
        await self.report(next_pid, "refreshed", ref)
        self.receipt(await self.tool(next_pid, "jira__read_ticket", issue_key="EX-1"))
        self.assertEqual([name for name, _ in self.client.calls].count("read_ticket"), 2)


class DefinitionValidationTest(unittest.TestCase):
    def test_unknown_nested_references_and_targets(self):
        node = {"tools": ["a__read"], "reports": {"done": "@missing"}}
        definition = WorkflowDefinition("read", (), {"read": node}, ("missing",))
        with self.assertRaises(ConfigError):
            compile_definition("a", {"a": definition}, {"a__read"})
        for target in ("missing", "@undeclared"):
            bad = WorkflowDefinition("read", (), {"read": {"tools": ["a__read"], "reports": {"done": target}}})
            with self.assertRaises(ConfigError):
                compile_definition("a", {"a": bad}, {"a__read"})
        with self.assertRaises(ConfigError):
            parse_definition('```workflow\n{}\n```\n```workflow\n{}\n```', "a")

    def test_all_playbook_prose_names_their_tools_and_limits(self):
        for name in ("rcid-bug", "jira-ticket-bug", "service-errors", "pr-review", "source-search"):
            text = (DOCS_ROOT / "playbooks" / f"{name}.md").read_text(encoding="utf-8")
            self.assertIn("## Steps and tool inputs", text)
            self.assertIn("## Answer and stop conditions", text)
            self.assertIn("```workflow", text)

    def test_readme_has_six_copyable_journeys_and_human_permission_guidance(self):
        text = (DOCS_ROOT.parent / "README.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("```text"), 6)
        for playbook in ("rcid-bug", "jira-ticket-bug", "service-errors", "pr-review", "source-search", "ticket-review"):
            self.assertIn(f"`{playbook}`", text)
        self.assertIn("If you omit the time window, the AI asks", text)
        self.assertIn("Allow a cache refresh", text)
        self.assertNotIn("hub_plan_id", text)
