from __future__ import annotations

import json
import unittest

from mcp_hub.config import ConfigError
from mcp_hub.planning.playbooks import load_playbooks
from mcp_hub.planning.workflow import Workflow, WorkflowError
from mcp_hub.planning.workflow_definition import parse_definition, compile_definition, node_tier
from mcp_hub.planning.workflow_scope import fill_context, absolute, result_observations, error_kind
from mcp.types import CallToolResult, TextContent
from scripts.call_report import workflow_report


def workflow(name="rcid-bug", **overrides):
    context = {"project": "orders", "env": "dev", "rcid": "request-1", "organization": "org", "stream": "stream",
               "field": "request_id", "since": "2026-11-22T00:00:00+00:00", "until": "2026-11-22T01:00:00+00:00", **overrides}
    return Workflow(load_playbooks()[name].workflow, context, 20, 50)


class WorkflowTest(unittest.IsolatedAsyncioTestCase):
    def test_all_definitions_are_compiled_and_jira_references_are_explicit(self):
        books = load_playbooks()
        self.assertTrue(all(book.workflow for book in books.values()))
        self.assertIn("rcid-bug.cache_list", books["jira-ticket-bug"].workflow.nodes)
        self.assertIn("service-errors.logs", books["jira-ticket-bug"].workflow.nodes)

    def test_cache_and_source_paths(self):
        for source in (False, True):
            w = workflow()
            listed = w.record("logs__list_ai_context_snapshots", "succeeded", 2)
            w.report("cache_hit", [listed.receipt_id])
            cached = w.record("logs__read_ai_context", "succeeded", 3)
            w.report("findings", [cached.receipt_id], source)
            if source:
                search = w.record("source__search", "succeeded", 1, observations={"paths": ("app.py",)})
                w.report("source_found", [search.receipt_id])
                w.check("source__read", {"project": "orders", "path": "app.py"})
                read = w.record("source__read", "succeeded", 1)
                w.report("source_read", [read.receipt_id])
            w.finish("supported", [cached.receipt_id])
            self.assertEqual(w.status()["outcome"], "supported")
            self.assertTrue(all(d["trust"] == "client-reported" for d in w.decisions))
            with self.assertRaises(WorkflowError):
                w.finish("insufficient", [])

    def test_failed_calls_references_and_access(self):
        w = workflow()
        failed = w.record("logs__list_ai_context_snapshots", "failed", 1)
        for decision, refs in (("cache_miss", [failed.receipt_id]), ("cache_hit", ["fake"]), ("cache_hit", [])):
            with self.assertRaises(WorkflowError):
                w.report(decision, refs)
        w.report("access_blocked", [failed.receipt_id])
        with self.assertRaises(WorkflowError):
            w.finish("insufficient", [])
        w.finish("blocked", [failed.receipt_id])

    async def test_busy_transitions(self):
        w = workflow()
        async with w.lock:
            for mutation in (lambda: w.finish("blocked", []), lambda: w.report("cache_miss", []), lambda: w.provide_input({})):
                with self.assertRaises(WorkflowError):
                    mutation()

    def test_log_scope_and_budget(self):
        w = workflow()
        w.stage = "logs"
        args = {"env": "dev", "organization": "org", "stream": "stream", "field": "request_id", "rcid": "request-1",
                "from": w.context["since"], "to": w.context["until"], "limit": 50}
        w.check("logs__find_openobserve_logs_by_rcid", args)
        for key, value in (("rcid", "other"), ("env", "prod"), ("field", "other"), ("organization", "other"),
                           ("stream", "other"), ("from", "1h"), ("limit", 51), ("limit", True)):
            with self.subTest(key=key), self.assertRaises(WorkflowError):
                w.check("logs__find_openobserve_logs_by_rcid", {**args, key: value})
        w.max_receipts = 1
        w.record("query", "succeeded", 1)
        with self.assertRaises(WorkflowError):
            w.check("logs__find_openobserve_logs_by_rcid", args)

    def test_fill_once_context_and_no_inferred_window(self):
        w = Workflow(load_playbooks()["rcid-bug"].workflow, {"project": "orders", "env": "dev", "rcid": "r"}, 10, 50)
        self.assertIsNone(w.window)
        self.assertEqual(w.status()["stage"], "awaiting_input")
        with self.assertRaises(WorkflowError):
            w.report("cache_miss", [])
        for values in ({"unknown": "x"}, {"rcid": "other"}, {"since": "1h"}, {"query": ""},
                       {"since": "2026-11-22T00:00:00Z"}, {"path": "../private"}):
            with self.subTest(values=values), self.assertRaises(WorkflowError):
                w.provide_input(values)
        w.provide_input({"since": "2026-11-22T00:00:00Z", "until": "2026-11-22T01:00:00Z"})
        self.assertIsNotNone(w.window)
        self.assertIn("log_target", w.missing())
        with self.assertRaises(WorkflowError):
            fill_context({}, {"since": "2026-11-23T00:00:00Z", "until": "2026-11-22T00:00:00Z"}, 2000, 24)
        with self.assertRaises(WorkflowError):
            absolute("2026-11-22T00:00:00")

    def test_definitions_reject_bad_graphs(self):
        good = {"entry": "read", "inputs": [], "nodes": {"read": {"tools": ["a__read"], "reports": {"done": "ready"}}}}
        def parse(data):
            return parse_definition("```workflow\n" + json.dumps(data) + "\n```", "example")
        definition = parse(good)
        self.assertEqual(compile_definition("example", {"example": definition}, {"a__read"}).entry, "read")
        for bad in ({**good, "entry": "missing"}, {**good, "inputs": ["unknown"]}, {**good, "unknown": True}):
            with self.assertRaises(ConfigError):
                parse(bad)
        for tools in (set(), {"other"}):
            with self.assertRaises(ConfigError):
                compile_definition("example", {"example": definition}, tools)
        cyclic = parse({**good, "nodes": {"read": {"tools": ["a__read"], "reports": {"again": "read"}}}})
        with self.assertRaises(ConfigError):
            compile_definition("example", {"example": cyclic}, {"a__read"})
        with self.assertRaises(ConfigError):
            parse_definition("```workflow\nnot json\n```", "example")

    def test_observations_and_report(self):
        result = CallToolResult(content=[TextContent(type="text", text=json.dumps({"total": 10, "matches": [{"path": "app.py"}]}))])
        observed = result_observations(result, 20, 2000)
        self.assertEqual(observed["paths"], ("app.py",))
        self.assertFalse(observed["complete"])
        self.assertIsNone(result_observations(CallToolResult(content=[]), 20, 2000)["complete"])
        self.assertEqual(error_kind(CallToolResult(content=[TextContent(type="text", text="No access")], is_error=True)), "access")
        self.assertEqual(error_kind(CallToolResult(content=[TextContent(type="text", text="Ticket not cached")], is_error=True)), "cache_missing")
        lines = ["bad", "[]", json.dumps({"event": "hub.workflow.call", "duration_ms": 20}),
                 json.dumps({"event": "hub.workflow.refused"}), json.dumps({"event": "hub.workflow.updated", "action": "finish"})]
        self.assertEqual(workflow_report(lines), ["Workflows: 1 calls, 1 refusals, 1 finished, 20 ms tool time"])

    def test_node_tier_and_workflow_active_tier(self):
        for terminal in ("ready", "blocked", "finished", "superseded"):
            self.assertEqual(node_tier(terminal, {"tools": []}), "orchestrate")
        self.assertEqual(node_tier("confirm", {"tools": [], "interaction": True}), "orchestrate")
        self.assertEqual(node_tier("plan_step", {"tools": ["a__b"], "tier": "orchestrate"}), "orchestrate")
        self.assertEqual(node_tier("read_step", {"tools": ["a__b"]}), "execute")

        missing_ctx = Workflow(load_playbooks()["rcid-bug"].workflow, {"project": "orders", "env": "dev"}, 10, 50)
        self.assertEqual(missing_ctx.active_tier(), "orchestrate")
        self.assertEqual(missing_ctx.status()["active_tier"], "orchestrate")

        complete = workflow()
        self.assertEqual(complete.active_tier(), "execute")
        complete.stage = "ready"
        self.assertEqual(complete.active_tier(), "orchestrate")

        explicit_def = parse_definition(
            "```workflow\n" + json.dumps({
                "entry": "step",
                "inputs": [],
                "nodes": {"step": {"tools": ["a__b"], "tier": "orchestrate", "reports": {"done": "ready"}}},
            }) + "\n```",
            "explicit-tier",
        )
        explicit_wf = Workflow(explicit_def, {}, 5, 50)
        self.assertEqual(explicit_wf.active_tier(), "orchestrate")
