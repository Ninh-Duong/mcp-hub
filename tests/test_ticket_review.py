"""Ticket requirement review: explicit source confirmation and repository-isolated evidence."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.types import CallToolResult, CallToolRequestParams, TextContent

from mcp_hub.client_profiles import load_client_profiles
from mcp_hub.config import DOCS_ROOT, ConfigError
from mcp_hub.planning.playbooks import load_playbooks
from mcp_hub.planning.workflow_definition import parse_definition, compile_definition
from mcp_hub.planning.workflow_runtime import WORKFLOW_META
from mcp_hub.projects import ProjectConfig
from mcp_hub.settings import PolicySettings, Settings
from tests.helpers.workflow import runtime_fixture, create_plan, call


class TicketReviewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime, self.client = runtime_fixture(Path(self.temp.name))
        for alias in ("backend", "database", "frontend", "unselected"):
            self.runtime.projects[alias] = ProjectConfig(alias, ("dev",), "dev")
        self.pid = await create_plan(self.runtime, task="Review ticket requirements", playbook="ticket-review", issue_key="EX-123",
                                     project=None, env=None, rcid=None, since=None, until=None)

    async def tool(self, name, **arguments):
        return await call(self.runtime, name, {"hub_plan_id": self.pid, **arguments})

    async def op(self, action="status", **arguments):
        return await self.tool("hub__workflow", action=action, **arguments)

    def ref(self, result):
        self.assertFalse(result.is_error, result.content)
        return result.meta[WORKFLOW_META]["receipt_id"]

    async def ticket(self):
        self.client.result = CallToolResult(content=[TextContent(type="text", text="Ticket requirements and acceptance criteria")])
        ref = self.ref(await self.tool("jira__read_ticket", issue_key="EX-123"))
        result = await self.op("report", decision="ticket_reviewed", refs=[ref])
        self.assertFalse(result.is_error, result.content)
        return ref

    async def select(self, **roles):
        result = await self.op("provide_input", inputs=roles)
        self.assertFalse(result.is_error, result.content)

    async def confirm(self):
        result = await self.op("report", decision="sources_confirmed", refs=[])
        self.assertFalse(result.is_error, result.content)

    async def search(self, project, path):
        self.client.result = CallToolResult(content=[TextContent(type="text", text=json.dumps({"matches": [{"path": path, "line": 1}], "total": 1}))])
        return self.ref(await self.tool("source__search", project=project, query="TicketField"))

    async def read(self, project, path):
        self.client.result = CallToolResult(content=[TextContent(type="text", text="Implementation evidence")])
        return self.ref(await self.tool("source__read", project=project, path=path, max_lines=100))

    async def test_ticket_and_human_confirmation_precede_all_source_reads(self):
        self.assertTrue((await self.tool("source__search", project="backend", query="api")).is_error)
        self.assertTrue((await self.op("report", decision="sources_confirmed", refs=[])).is_error)
        self.assertEqual(self.client.calls, [])
        await self.ticket()
        status = json.loads((await self.op()).content[0].text)
        self.assertEqual(status["active_stage"], "confirm_sources")
        self.assertIn("source_selection", status["missing_inputs"])
        self.assertIn("backend", status["source_choices"])
        await self.select(be_project="backend", db_project="database")
        self.assertTrue((await self.tool("source__search", project="backend", query="api")).is_error)
        await self.confirm()
        self.assertEqual(len(self.client.calls), 1)
        self.assertTrue((await self.tool("source__search", project="unselected", query="api")).is_error)
        self.assertEqual(len(self.client.calls), 1)

    async def test_be_db_paths_are_isolated_and_complete_review_covers_both(self):
        await self.ticket()
        await self.select(be_project="backend", db_project="database")
        await self.confirm()
        await self.search("backend", "shared.sql")
        self.assertTrue((await self.tool("source__read", project="database", path="shared.sql")).is_error)
        be_ref = await self.read("backend", "shared.sql")
        denied = await self.op("report", decision="review_complete", refs=[be_ref])
        self.assertTrue(denied.is_error)
        await self.search("database", "procedure.sql")
        db_ref = await self.read("database", "procedure.sql")
        reported = await self.op("report", decision="review_complete", refs=[be_ref, db_ref])
        self.assertFalse(reported.is_error, reported.content)
        finished = await self.op("finish", outcome="supported", refs=[be_ref, db_ref])
        self.assertFalse(finished.is_error)
        state = json.loads(finished.content[0].text)
        self.assertEqual(state["outcome"], "supported")
        projects = {r["project"] for r in state["receipts"] if r["tool"] == "source__read"}
        self.assertEqual(projects, {"backend", "database"})

    async def test_selection_may_be_corrected_before_confirmation_but_never_expanded_after(self):
        await self.ticket()
        await self.select(be_project="unselected")
        await self.select(be_project="backend", db_project="database")
        await self.confirm()
        for values in ({"be_project": "unselected"}, {"fe_project": "frontend"}):
            result = await self.op("provide_input", inputs=values)
            self.assertTrue(result.is_error)
        self.assertEqual(self.runtime.plans.get(self.pid).workflow.context["be_project"], "backend")

    async def test_unknown_alias_or_raw_directory_does_not_change_context(self):
        await self.ticket()
        before = dict(self.runtime.plans.get(self.pid).workflow.context)
        for alias in ("missing-project", "/unregistered/source"):
            self.assertTrue((await self.op("provide_input", inputs={"be_project": alias})).is_error)
            self.assertEqual(self.runtime.plans.get(self.pid).workflow.context, before)
        self.assertTrue((await self.op("report", decision="sources_confirmed", refs=[])).is_error)

    async def test_optional_fe_has_the_same_coverage_rule(self):
        await self.ticket()
        await self.select(be_project="backend", fe_project="frontend")
        await self.confirm()
        await self.search("backend", "api.py")
        ref = await self.read("backend", "api.py")
        self.assertTrue((await self.op("report", decision="review_complete", refs=[ref])).is_error)
        await self.search("frontend", "view.ts")
        fe_ref = await self.read("frontend", "view.ts")
        self.assertFalse((await self.op("report", decision="review_complete", refs=[ref, fe_ref])).is_error)

    async def test_declined_sources_can_return_ticket_questions_without_supported_comparison(self):
        ref = await self.ticket()
        self.assertTrue((await self.op("finish", outcome="supported", refs=[ref])).is_error)
        result = await self.op("finish", outcome="insufficient", refs=[ref])
        self.assertFalse(result.is_error)
        self.assertEqual(len(self.client.calls), 1)

    async def test_new_roles_do_not_expand_existing_source_playbook_scope(self):
        pid = await create_plan(self.runtime, playbook="source-search", query="retry")
        result = await call(self.runtime, "hub__workflow", {"hub_plan_id": pid, "action": "provide_input", "inputs": {"be_project": "backend"}})
        self.assertTrue(result.is_error)
        result = await call(self.runtime, "source__search", {"hub_plan_id": pid, "project": "backend", "query": "retry"})
        self.assertTrue(result.is_error)

    async def test_multi_source_works_with_a_separate_ticket_project(self):
        self.pid = await create_plan(self.runtime, task="Review with ticket project", playbook="ticket-review", issue_key="EX-123",
                                     project="orders", rcid=None, since=None, until=None)
        await self.ticket()
        await self.select(be_project="backend")
        await self.confirm()
        await self.search("backend", "api.py")
        await self.read("backend", "api.py")

    async def continue_review(self):
        old = self.runtime.plans.get(self.pid)
        state = json.loads((await self.op()).content[0].text)
        result = await call(self.runtime, "hub__plan", {"task": old.task, "playbook": "ticket-review",
                            **state["scope"], "writes": ["jira__sync_ticket"], "continue_from": self.pid})
        self.assertFalse(result.is_error, result.content)
        import re
        self.pid = re.search(r'hub_plan_id="([^"]+)"', result.content[0].text)[1]

    async def test_related_tickets_require_confirmation_and_keep_their_own_assets(self):
        await self.select(be_project="backend", related_issue_keys="ex-124,EX-125")
        self.assertTrue((await self.tool("jira__read_ticket", issue_key="EX-124")).is_error)
        await self.ticket()
        await self.confirm()
        self.assertTrue((await self.tool("jira__read_ticket", issue_key="EX-999")).is_error)
        self.assertTrue((await self.tool("source__search", project="backend", query="api")).is_error)
        self.assertTrue((await self.tool("jira__sync_ticket", issue_key="EX-125")).is_error)
        self.client.result = CallToolResult(content=[TextContent(type="text", text="[attachment](assets/first.png)")])
        first = self.ref(await self.tool("jira__read_ticket", issue_key="EX-124"))
        self.assertTrue((await self.op("report", decision="related_reviewed", refs=[first])).is_error)
        self.client.result = CallToolResult(content=[TextContent(type="text", text="Second related requirements")])
        second = self.ref(await self.tool("jira__read_ticket", issue_key="EX-125"))
        self.assertTrue((await self.tool("jira__read_asset", issue_key="EX-125", filename="first.png")).is_error)
        self.assertFalse((await self.op("report", decision="related_reviewed", refs=[first, second])).is_error)
        await self.search("backend", "api.py")
        source = await self.read("backend", "api.py")
        self.assertFalse((await self.op("report", decision="review_complete", refs=[source])).is_error)
        self.assertTrue((await self.op("finish", outcome="supported", refs=[source])).is_error)
        self.assertFalse((await self.op("finish", outcome="supported", refs=[source, first, second])).is_error)

    async def test_missing_related_cache_refresh_is_scoped_to_the_selected_issue(self):
        await self.ticket()
        await self.select(be_project="backend", related_issue_keys="EX-124")
        await self.confirm()
        self.client.result = CallToolResult(content=[TextContent(type="text", text="Ticket not cached")], is_error=True)
        failed = await self.tool("jira__read_ticket", issue_key="EX-124")
        self.assertTrue(failed.is_error)
        self.assertTrue((await self.tool("jira__sync_ticket", issue_key="EX-124")).is_error)
        await self.continue_review()
        self.assertTrue((await self.tool("jira__sync_ticket", issue_key="EX-999")).is_error)
        self.client.result = CallToolResult(content=[TextContent(type="text", text="Related cache refreshed")])
        self.ref(await self.tool("jira__sync_ticket", issue_key="EX-124"))
        related = self.ref(await self.tool("jira__read_ticket", issue_key="EX-124"))
        self.assertFalse((await self.op("report", decision="related_reviewed", refs=[related])).is_error)

    async def test_missing_related_cache_is_a_limitation_and_cannot_be_certified(self):
        await self.ticket()
        await self.select(be_project="backend", related_issue_keys="EX-124")
        await self.confirm()
        self.client.result = CallToolResult(content=[TextContent(type="text", text="Ticket not cached")], is_error=True)
        result = await self.tool("jira__read_ticket", issue_key="EX-124")
        failed = result.meta[WORKFLOW_META]["receipt_id"]
        self.assertFalse((await self.op("report", decision="related_reviewed", refs=[failed])).is_error)
        await self.search("backend", "api.py")
        source = await self.read("backend", "api.py")
        self.assertFalse((await self.op("report", decision="review_complete", refs=[source])).is_error)
        self.assertTrue((await self.op("finish", outcome="supported", refs=[source])).is_error)
        self.assertFalse((await self.op("finish", outcome="insufficient", refs=[source, failed])).is_error)

    async def test_scope_can_be_cleared_before_confirmation_and_related_keys_are_validated(self):
        await self.ticket()
        await self.select(be_project="backend", fe_project="frontend", related_issue_keys="EX-124")
        await self.select(fe_project="", related_issue_keys="")
        self.assertNotIn("fe_project", self.runtime.plans.get(self.pid).workflow.context)
        for keys in ("EX-123", "not-an-issue", "EX-124,"):
            self.assertTrue((await self.op("provide_input", inputs={"related_issue_keys": keys})).is_error)
        await self.confirm()
        self.assertTrue((await self.op("provide_input", inputs={"related_issue_keys": "EX-124"})).is_error)


class TicketReviewContractTest(unittest.TestCase):
    def test_report_sections_and_no_unrequested_write_or_sql_tools(self):
        book = load_playbooks()["ticket-review"]
        self.assertTrue(book.workflow.multi_source)
        self.assertEqual(set(book.tools), {"jira__read_ticket", "jira__sync_ticket", "jira__read_asset", "source__search", "source__read"})
        for section in ("Ticket summary", "Missing or ambiguous requirements", "Ticket versus source", "Common/product",
                        "BE (including DB/SP)", "FE", "QC", "Related feature impact", "Acceptance and QC matrix", "limitations"):
            self.assertIn(section, book.body)
        self.assertIn("Respond in the user's language", book.body)
        self.assertIn("hypotheses", book.body)

    def test_interaction_definitions_require_evidence_and_do_not_hide_tools(self):
        base = {"entry": "choose", "inputs": [], "nodes": {"choose": {"tools": [], "interaction": True,
                "evidence": ["jira__read_ticket"], "reports": {"selected": "ready"}}}}
        definition = parse_definition("```workflow\n" + json.dumps(base) + "\n```", "example")
        self.assertIsNotNone(definition)
        with self.assertRaises(ConfigError):
            compile_definition("example", {"example": definition}, set())
        base["nodes"]["choose"]["tools"] = ["source__search"]
        with self.assertRaises(ConfigError):
            parse_definition("```workflow\n" + json.dumps(base) + "\n```", "example")
        self.assertTrue((DOCS_ROOT / "playbooks" / "ticket-review.md").is_file())


class TicketReviewExecutionRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.runtime, self.client = runtime_fixture(root)
        self.runtime.client_profiles = load_client_profiles(private_root=root)
        self.runtime.shared_service = True
        self.runtime.settings = Settings(
            policy=PolicySettings(mode="enforce", confirm_playbook="off", confirm_execution="always"),
        )
        for alias in ("backend", "database"):
            self.runtime.projects[alias] = ProjectConfig(alias, ("dev",), "dev")

    def _ctx(self) -> SimpleNamespace:
        return SimpleNamespace(
            request_id="exec-test",
            session=SimpleNamespace(client_params=SimpleNamespace(client_info=SimpleNamespace(name="Antigravity"))),
        )

    async def call_agy(self, name: str, arguments: dict) -> CallToolResult:
        return await self.runtime.call_tool(self._ctx(), CallToolRequestParams(name=name, arguments=arguments))

    async def _start_antigravity_review(self) -> tuple[str, str]:
        task = "Review ticket requirements"
        base_args = {"task": task, "playbook": "ticket-review", "issue_key": "EX-123"}
        offer = await self.call_agy("hub__plan", base_args)
        self.assertFalse(offer.is_error)
        token = re.search(r"confirm_token: (\S+?)\.?$", offer.content[0].text, re.M)[1]
        created = await self.call_agy("hub__plan", {
            **base_args, "confirm_token": token,
            "execution_mode": "multi_agent", "orchestrate_model": "pro", "execute_model": "flash",
        })
        self.assertFalse(created.is_error, created.content[0].text)
        return task, re.search(r'hub_plan_id="([^"]+)"', created.content[0].text)[1]

    async def test_execution_routing_across_ticket_review_stages(self) -> None:
        _, pid = await self._start_antigravity_review()
        ticket_status = json.loads((await self.call_agy("hub__workflow", {"hub_plan_id": pid, "action": "status"})).content[0].text)
        self.assertEqual(ticket_status["active_stage"], "ticket")
        self.assertEqual((ticket_status["execution"]["active_tier"], ticket_status["execution"]["delegate_stage"],
                          ticket_status["execution"]["recommended_model"]), ("execute", True, "flash"))

        self.client.result = CallToolResult(content=[TextContent(type="text", text="Ticket text")])
        read_res = await self.call_agy("jira__read_ticket", {"hub_plan_id": pid, "issue_key": "EX-123"})
        self.assertEqual((read_res.meta[WORKFLOW_META]["execution"]["active_tier"],
                          read_res.meta[WORKFLOW_META]["execution"]["delegate_stage"]), ("execute", True))
        ref = read_res.meta[WORKFLOW_META]["receipt_id"]

        confirm_res = await self.call_agy("hub__workflow", {
            "hub_plan_id": pid, "action": "report", "decision": "ticket_reviewed", "refs": [ref],
        })
        confirm_status = json.loads(confirm_res.content[0].text)
        self.assertEqual(confirm_status["active_stage"], "confirm_sources")
        self.assertEqual((confirm_status["execution"]["active_tier"], confirm_status["execution"]["delegate_stage"],
                          confirm_status["execution"]["recommended_model"]), ("orchestrate", False, "pro"))

        await self.call_agy("hub__workflow", {
            "hub_plan_id": pid, "action": "provide_input", "inputs": {"be_project": "backend"},
        })
        compare_status = json.loads((await self.call_agy("hub__workflow", {
            "hub_plan_id": pid, "action": "report", "decision": "sources_confirmed", "refs": [],
        })).content[0].text)
        self.assertEqual(compare_status["active_stage"], "compare")
        self.assertEqual((compare_status["execution"]["active_tier"], compare_status["execution"]["delegate_stage"],
                          compare_status["execution"]["recommended_model"]), ("execute", True, "flash"))

        self.client.result = None
        await self.call_agy("source__search", {"hub_plan_id": pid, "project": "backend", "query": "TicketField"})
        self.client.result = CallToolResult(content=[TextContent(type="text", text="Source code")])
        src_read = await self.call_agy("source__read", {"hub_plan_id": pid, "project": "backend", "path": "app.py"})
        ready_status = json.loads((await self.call_agy("hub__workflow", {
            "hub_plan_id": pid, "action": "report", "decision": "review_complete",
            "refs": [src_read.meta[WORKFLOW_META]["receipt_id"]],
        })).content[0].text)
        self.assertEqual(ready_status["active_stage"], "ready")
        self.assertEqual((ready_status["execution"]["active_tier"], ready_status["execution"]["delegate_stage"],
                          ready_status["execution"]["recommended_model"]), ("orchestrate", False, "pro"))

    async def test_continuation_preserves_execution_and_refuses_changes(self) -> None:
        task, pid = await self._start_antigravity_review()
        self.client.result = CallToolResult(content=[TextContent(type="text", text="Ticket text")])
        read_res = await self.call_agy("jira__read_ticket", {"hub_plan_id": pid, "issue_key": "EX-123"})
        await self.call_agy("hub__workflow", {
            "hub_plan_id": pid, "action": "report", "decision": "ticket_reviewed",
            "refs": [read_res.meta[WORKFLOW_META]["receipt_id"]],
        })
        filled = json.loads((await self.call_agy("hub__workflow", {
            "hub_plan_id": pid, "action": "provide_input", "inputs": {"be_project": "backend"},
        })).content[0].text)
        self.assertEqual((filled["active_stage"], filled["execution"]["active_tier"], filled["execution"]["delegate_stage"]),
                         ("confirm_sources", "orchestrate", False))

        scope = filled["scope"]
        for extra in (
            {"client": "claude-code"},
            {"execution_mode": "single_agent", "orchestrate_model": "pro"},
            {"execution_mode": "multi_agent", "orchestrate_model": "pro", "execute_model": "flash_lite"},
        ):
            bad_cont = await self.call_agy("hub__plan", {
                "task": task, "playbook": "ticket-review", **scope,
                "writes": ["jira__sync_ticket"], "continue_from": pid, **extra,
            })
            self.assertTrue(bad_cont.is_error)
            self.assertIn("session execution setup", bad_cont.content[0].text)

        cont = await self.call_agy("hub__plan", {
            "task": task, "playbook": "ticket-review", **scope,
            "writes": ["jira__sync_ticket"], "continue_from": pid,
        })
        self.assertFalse(cont.is_error, cont.content[0].text)
        self.assertIn("Session execution (antigravity): mode=multi_agent, orchestrate=pro, execute=flash.",
                      cont.content[0].text)
        new_pid = re.search(r'hub_plan_id="([^"]+)"', cont.content[0].text)[1]
        self.assertEqual(self.runtime.plans.get(new_pid).execution, self.runtime.plans.get(pid).execution)
