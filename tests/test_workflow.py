from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta

from mcp_hub.planning.workflow import Workflow, WorkflowError, pilot_window
from mcp_hub.planning.playbooks import load_playbooks, parse_playbook
from mcp_hub.config import ConfigError, DOCS_ROOT
from scripts.call_report import workflow_report


def workflow():
    return Workflow(load_playbooks()["rcid-bug"].workflow, "request-1", "dev",
                    {"project": "orders", "organization": "org", "stream": "stream", "field": "request_id"},
                    (datetime(2026, 11, 22, tzinfo=UTC), datetime(2026, 11, 22, 1, tzinfo=UTC)), 10, 50)


class WorkflowTest(unittest.IsolatedAsyncioTestCase):
    def test_cache_only_and_source_paths(self):
        for source in (False, True):
            w = workflow()
            first = w.record("logs__list_ai_context_snapshots", "succeeded", 2)
            w.report("cache_hit", [first.receipt_id])
            cached = w.record("logs__read_ai_context", "succeeded", 3)
            w.report("findings", [cached.receipt_id], source)
            if source:
                search = w.record("source__search", "succeeded", 1)
                w.report("source_found", [search.receipt_id])
                read = w.record("source__read", "succeeded", 2)
                w.report("source_read", [read.receipt_id])
            w.finish("supported", [cached.receipt_id])
            self.assertEqual(w.status()["outcome"], "supported")
            self.assertTrue(all(d["trust"] == "client-reported" for d in w.decisions))
            with self.assertRaises(WorkflowError):
                w.finish("insufficient", [])

    def test_failed_calls_references_and_busy_transitions(self):
        w = workflow()
        failed = w.record("logs__list_ai_context_snapshots", "failed", 1)
        for decision, refs in (("cache_miss", [failed.receipt_id]), ("cache_miss", ["fake"]), ("cache_hit", [])):
            with self.assertRaises(WorkflowError):
                w.report(decision, refs)
        w.report("access_blocked", [failed.receipt_id])
        with self.assertRaises(WorkflowError):
            w.finish("insufficient", [])
        w.finish("blocked", [failed.receipt_id])

    async def test_lock_refuses_mutations(self):
        w = workflow()
        async with w.lock:
            with self.assertRaises(WorkflowError):
                w.finish("blocked", [])
            with self.assertRaises(WorkflowError):
                w.report("cache_miss", [])

    def test_scope_and_budget(self):
        w = workflow()
        with self.assertRaises(WorkflowError):
            w.check("source__search", {"project": "orders"})
        w.stage = "logs"
        args = {"env": "dev", "organization": "org", "stream": "stream", "field": "request_id", "rcid": "request-1",
                "from": w.window[0].isoformat(), "to": w.window[1].isoformat(), "limit": 50}
        w.check("logs__find_openobserve_logs_by_rcid", args)
        for key, value in (("rcid", "other"), ("env", "prod"), ("field", "other"), ("organization", "other"),
                           ("stream", "other"), ("from", "1h"), ("limit", 51), ("limit", True), ("to", "2026-11-23T00:00:00Z")):
            with self.subTest(key=key), self.assertRaises(WorkflowError):
                w.check("logs__find_openobserve_logs_by_rcid", {**args, key: value})
        w.max_receipts = 1
        w.record("query", "succeeded", 2)
        with self.assertRaises(WorkflowError):
            w.check("logs__find_openobserve_logs_by_rcid", args)

    def test_remaining_stage_checks_and_reports(self):
        w = workflow()
        w.stage = "cache_read"
        with self.assertRaises(WorkflowError):
            w.check("logs__read_ai_context", {})
        w.check("logs__read_ai_context", {"snapshot": "snapshot-1", "file": "SUMMARY.lhub"})
        ref = w.record("logs__read_ai_context", "succeeded", 1)
        w.report("cache_insufficient", [ref.receipt_id])
        with self.assertRaises(WorkflowError):
            w.report("findings", [ref.receipt_id], False)
        w.stage = "source_search"
        with self.assertRaises(WorkflowError):
            w.check("source__search", {"project": "other"})
        w.check("source__search", {"project": "orders"})
        ref = w.record("source__search", "succeeded", 1)
        with self.assertRaises(WorkflowError):
            w.report("findings", [ref.receipt_id], False)
        for outcome in ("invalid", "supported"):
            with self.assertRaises(WorkflowError):
                w.finish(outcome, [])
        with self.assertRaises(WorkflowError):
            w.finish("insufficient", "not-a-list")
        w.finish("insufficient", [])

    def test_window_and_parser(self):
        args = {"project": "orders", "env": "dev", "rcid": "r"}
        start, end = pilot_window(args, 3600)
        self.assertEqual(end - start, timedelta(hours=1))
        for bad in ({}, {**args, "env": ""}, {**args, "since": "1h"},
                    {**args, "since": "2026-11-22T00:00:00", "until": "2026-11-22T01:00:00Z"}):
            with self.assertRaises(WorkflowError):
                pilot_window(bad, 3600)
        text = (DOCS_ROOT / "playbooks" / "rcid-bug.md").read_text(encoding="utf-8")
        for old, new in (("workflow_cache_list:", "workflow_other:"),
                         ("workflow_source_read: [source__read]", "workflow_source_read: [source__search]")):
            with self.assertRaises(ConfigError):
                parse_playbook(text.replace(old, new), "rcid-bug")
        self.assertFalse(load_playbooks()["jira-ticket-bug"].workflow)

    def test_metadata_only_report(self):
        lines = ["bad", "[]", json.dumps({"event": "hub.workflow.call", "duration_ms": 20, "arguments": "secret"}),
                 json.dumps({"event": "hub.workflow.refused"}),
                 json.dumps({"event": "hub.workflow.updated", "action": "finish"})]
        self.assertEqual(workflow_report(lines), ["Workflows: 1 calls, 1 refusals, 1 finished, 20 ms tool time"])
        self.assertEqual(workflow_report([]), [])
