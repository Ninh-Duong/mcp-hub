"""Change review playbook: git-diff impact analysis, duplicate audit, two-gate confirmations, and /ponytail."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.types import CallToolRequestParams, CallToolResult, TextContent

from mcp_hub.client_profiles import load_client_profiles
from mcp_hub.config import DOCS_ROOT, ConfigError
from mcp_hub.planning.playbooks import load_playbooks
from mcp_hub.planning.workflow_definition import compile_definition, parse_definition
from mcp_hub.planning.workflow_runtime import WORKFLOW_META
from mcp_hub.projects import ProjectConfig
from mcp_hub.settings import PolicySettings, Settings
from tests.helpers.workflow import call, create_plan, runtime_fixture


class ChangeReviewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime, self.client = runtime_fixture(Path(self.temp.name))
        for alias in ("backend", "database", "frontend", "unselected"):
            self.runtime.projects[alias] = ProjectConfig(alias, ("dev",), "dev")

    async def start(self, **overrides: object) -> str:
        args = {
            "task": "Review current git changes for impact and duplicates",
            "playbook": "change-review",
            "project": None, "env": None, "rcid": None, "since": None, "until": None,
            **overrides,
        }
        return await create_plan(self.runtime, **args)

    async def tool(self, pid: str, name: str, **arguments: object) -> CallToolResult:
        return await call(self.runtime, name, {"hub_plan_id": pid, **arguments})

    async def op(self, pid: str, action: str = "status", **arguments: object) -> CallToolResult:
        return await self.tool(pid, "hub__workflow", action=action, **arguments)

    def ref(self, result: CallToolResult) -> str:
        self.assertFalse(result.is_error, result.content)
        return result.meta[WORKFLOW_META]["receipt_id"]

    async def test_pure_code_review_flow_with_callers_unlock_and_two_gates(self) -> None:
        pid = await self.start()
        status = json.loads((await self.op(pid)).content[0].text)
        self.assertEqual((status["stage"], status["active_stage"], status["active_tier"]),
                         ("awaiting_input", "impact_scan", "orchestrate"))
        self.assertEqual(status["missing_inputs"], ["source_selection", "changed_paths"])

        for bad in ("../secret.py", "/etc/passwd", "C" + ":/repo/a.py", ".", "a.py, ../b.py"):
            self.assertTrue((await self.op(pid, "provide_input", inputs={"changed_paths": bad})).is_error)

        provided = await self.op(pid, "provide_input",
                                 inputs={"be_project": "backend", "changed_paths": r".\src\svc.cs, src/svc.cs"})
        self.assertFalse(provided.is_error, provided.content)
        scan_status = json.loads(provided.content[0].text)
        self.assertEqual((scan_status["stage"], scan_status["active_tier"]), ("impact_scan", "execute"))
        self.assertEqual(scan_status["scope"]["changed_paths"], "src/svc.cs")

        # Unbound path is refused; changed_paths path is allowed for outline/read.
        self.assertTrue((await self.tool(pid, "source__read", project="backend", path="src/other.cs")).is_error)
        self.assertTrue((await self.tool(pid, "source__index", project="backend", force=True)).is_error)

        self.client.result = CallToolResult(content=[TextContent(type="text", text="class Svc { Run() L1-10 }")])
        outline_ref = self.ref(await self.tool(pid, "source__outline", project="backend", path="src/svc.cs"))

        # Scope locks after the first source__* call.
        self.assertTrue((await self.op(pid, "provide_input", inputs={"fe_project": "frontend"})).is_error)
        self.assertTrue((await self.op(pid, "provide_input", inputs={"changed_paths": "src/svc.cs,src/new.cs"})).is_error)

        # source__callers unlocks caller path for source__read.
        callers_payload = {
            "target": {"symbol": "Svc.Run", "path": "src/svc.cs", "start_line": 1},
            "callers": [{"caller": "Api.Get", "path": "src/api.cs", "start_line": 5, "call_lines": [8]}],
            "total": 1, "truncated": False,
        }
        self.client.result = CallToolResult(content=[TextContent(type="text", text=json.dumps(callers_payload))])
        callers_ref = self.ref(await self.tool(pid, "source__callers", project="backend", symbol="Svc.Run", limit=50))

        self.client.result = CallToolResult(content=[TextContent(type="text", text="caller implementation")])
        read_ref = self.ref(await self.tool(pid, "source__read", project="backend", path="src/api.cs", max_lines=100))

        # Gate 1: confirm_risks (interaction, orchestrate).
        gate1 = await self.op(pid, "report", decision="impact_analyzed", refs=[outline_ref, callers_ref, read_ref])
        self.assertFalse(gate1.is_error, gate1.content)
        gate1_state = json.loads(gate1.content[0].text)
        self.assertEqual((gate1_state["active_stage"], gate1_state["active_tier"], gate1_state["allowed_tools"]),
                         ("confirm_risks", "orchestrate", []))

        # Gate 2: ponytail_check -> ready -> finish supported.
        gate2 = await self.op(pid, "report", decision="risks_confirmed", refs=[])
        self.assertFalse(gate2.is_error, gate2.content)
        self.assertEqual(json.loads(gate2.content[0].text)["active_stage"], "ponytail_check")

        self.client.result = CallToolResult(content=[TextContent(type="text", text="svc code")])
        pony_ref = self.ref(await self.tool(pid, "source__read", project="backend", path="src/svc.cs", max_lines=50))
        ready = await self.op(pid, "report", decision="ponytail_complete", refs=[pony_ref])
        self.assertFalse(ready.is_error, ready.content)
        self.assertEqual(json.loads(ready.content[0].text)["active_stage"], "ready")

        finished = await self.op(pid, "finish", outcome="supported", refs=[callers_ref, read_ref, pony_ref])
        self.assertFalse(finished.is_error, finished.content)
        self.assertEqual(json.loads(finished.content[0].text)["outcome"], "supported")

    async def test_optional_jira_and_multi_source_isolation_and_coverage(self) -> None:
        pid = await self.start(
            issue_key="EX-456",
            related_issue_keys="EX-457",
            be_project="backend",
            db_project="database",
            changed_paths="src/svc.cs,db/sp_run.sql",
        )
        status = json.loads((await self.op(pid)).content[0].text)
        self.assertEqual(status["active_stage"], "ticket")

        self.client.result = CallToolResult(content=[TextContent(type="text", text="Feature ticket requirements")])
        ticket_ref = self.ref(await self.tool(pid, "jira__read_ticket", issue_key="EX-456"))
        rel_stage = await self.op(pid, "report", decision="ticket_reviewed", refs=[ticket_ref])
        self.assertFalse(rel_stage.is_error, rel_stage.content)
        self.assertEqual(json.loads(rel_stage.content[0].text)["active_stage"], "related_tickets")

        self.client.result = CallToolResult(content=[TextContent(type="text", text="Related ticket")])
        rel_ref = self.ref(await self.tool(pid, "jira__read_ticket", issue_key="EX-457"))
        scan_stage = await self.op(pid, "report", decision="related_reviewed", refs=[rel_ref])
        self.assertFalse(scan_stage.is_error, scan_stage.content)
        self.assertEqual(json.loads(scan_stage.content[0].text)["active_stage"], "impact_scan")

        # related_issue_keys must stay locked at impact_scan even before the first source__* call.
        self.assertTrue((await self.op(pid, "provide_input", inputs={"related_issue_keys": ""})).is_error)
        self.assertTrue((await self.op(pid, "provide_input", inputs={"related_issue_keys": "EX-457,EX-458"})).is_error)

        # Callers path discovered in backend must NOT unlock the same path in database.
        callers_payload = {"callers": [{"caller": "Api.Get", "path": "shared/x.sql", "start_line": 1}], "total": 1}
        self.client.result = CallToolResult(content=[TextContent(type="text", text=json.dumps(callers_payload))])
        be_ref = self.ref(await self.tool(pid, "source__callers", project="backend", symbol="Svc.Run"))
        self.assertTrue((await self.tool(pid, "source__read", project="database", path="shared/x.sql")).is_error)

        # source_coverage blocks impact_analyzed until database also has source evidence.
        self.assertTrue((await self.op(pid, "report", decision="impact_analyzed", refs=[be_ref])).is_error)

        self.client.result = CallToolResult(content=[TextContent(type="text", text="CREATE PROCEDURE sp_run")])
        db_ref = self.ref(await self.tool(pid, "source__read", project="database", path="db/sp_run.sql"))
        self.assertFalse((await self.op(pid, "report", decision="impact_analyzed", refs=[be_ref, db_ref])).is_error)

        # risks_confirmed_ready transitions directly to ready.
        self.assertFalse((await self.op(pid, "report", decision="risks_confirmed_ready", refs=[])).is_error)
        self.assertTrue((await self.op(pid, "finish", outcome="supported", refs=[be_ref, db_ref])).is_error)
        self.assertFalse((await self.op(pid, "finish", outcome="supported", refs=[be_ref, db_ref, rel_ref])).is_error)

    async def test_changed_paths_refused_in_non_change_review_playbooks(self) -> None:
        refused_plan = await call(self.runtime, "hub__plan", {
            "task": "Search code", "playbook": "source-search", "project": "backend",
            "query": "retry", "changed_paths": "src/a.py",
        })
        self.assertTrue(refused_plan.is_error)

        pid = await create_plan(self.runtime, playbook="source-search", project="backend", query="retry")
        refused_input = await self.op(pid, "provide_input", inputs={"changed_paths": "src/a.py"})
        self.assertTrue(refused_input.is_error)


class ChangeReviewContractAndRoutingTest(unittest.IsolatedAsyncioTestCase):
    def test_contract_and_evidence_any_validation(self) -> None:
        book = load_playbooks()["change-review"]
        self.assertTrue(book.workflow.multi_source)
        self.assertTrue(book.workflow.multi_ticket)
        self.assertIn("source__callers", book.tools)
        self.assertNotIn("sql__run_select", book.tools)
        for phrase in ("Shared-caller impact analysis", "New function vs. existing codebase audit",
                       "/ponytail", "mermaid"):
            self.assertIn(phrase, book.body)
        self.assertTrue((DOCS_ROOT / "playbooks" / "change-review.md").is_file())

        bad_missing = {"entry": "gate", "inputs": [], "nodes": {"gate": {"tools": [], "interaction": True,
                       "reports": {"ok": "ready"}}}}
        with self.assertRaises(ConfigError):
            parse_definition("```workflow\n" + json.dumps(bad_missing) + "\n```", "bad")

        bad_tool = {"entry": "gate", "inputs": [], "nodes": {"gate": {"tools": [], "interaction": True,
                    "evidence_any": ["source__callers"], "reports": {"ok": "ready"}}}}
        definition = parse_definition("```workflow\n" + json.dumps(bad_tool) + "\n```", "bad")
        with self.assertRaises(ConfigError):
            compile_definition("bad", {"bad": definition}, {"source__read"})

    async def test_execution_routing_uses_orchestrate_on_confirm_risks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, client = runtime_fixture(root)
            runtime.client_profiles = load_client_profiles(private_root=root)
            runtime.shared_service = True
            runtime.settings = Settings(
                policy=PolicySettings(mode="enforce", confirm_playbook="off", confirm_execution="always"),
            )
            runtime.projects["backend"] = ProjectConfig("backend", ("dev",), "dev")
            ctx = SimpleNamespace(
                request_id="cr-exec",
                session=SimpleNamespace(client_params=SimpleNamespace(client_info=SimpleNamespace(name="Antigravity"))),
            )
            base = {"task": "Review local diff", "playbook": "change-review",
                    "be_project": "backend", "changed_paths": "src/a.cs"}
            offer = await runtime.call_tool(ctx, CallToolRequestParams(name="hub__plan", arguments=base))
            token = re.search(r"confirm_token: (\S+?)\.?$", offer.content[0].text, re.M)[1]
            created = await runtime.call_tool(ctx, CallToolRequestParams(name="hub__plan", arguments={
                **base, "confirm_token": token,
                "execution_mode": "multi_agent", "orchestrate_model": "pro", "execute_model": "flash",
            }))
            pid = re.search(r'hub_plan_id="([^"]+)"', created.content[0].text)[1]

            st1 = json.loads((await runtime.call_tool(ctx, CallToolRequestParams(
                name="hub__workflow", arguments={"hub_plan_id": pid, "action": "status"},
            ))).content[0].text)
            self.assertEqual((st1["active_stage"], st1["execution"]["active_tier"], st1["execution"]["delegate_stage"]),
                             ("impact_scan", "execute", True))

            client.result = CallToolResult(content=[TextContent(type="text", text="code")])
            r1 = await runtime.call_tool(ctx, CallToolRequestParams(
                name="source__read", arguments={"hub_plan_id": pid, "project": "backend", "path": "src/a.cs"},
            ))
            st2 = json.loads((await runtime.call_tool(ctx, CallToolRequestParams(
                name="hub__workflow", arguments={
                    "hub_plan_id": pid, "action": "report", "decision": "impact_analyzed",
                    "refs": [r1.meta[WORKFLOW_META]["receipt_id"]],
                },
            ))).content[0].text)
            self.assertEqual((st2["active_stage"], st2["execution"]["active_tier"], st2["execution"]["delegate_stage"]),
                             ("confirm_risks", "orchestrate", False))


if __name__ == "__main__":
    unittest.main()
