from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HUB_ROOT = Path(__file__).resolve().parents[1]


def load_check_docs():
    spec = importlib.util.spec_from_file_location("check_docs", HUB_ROOT / "scripts" / "check_docs.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CheckDocsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.docs = load_check_docs()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_repository_docs_match_the_code(self) -> None:
        self.assertEqual(self.docs.all_problems(), [])

    def test_served_routes_include_the_active_bitnet_processing_stream(self) -> None:
        self.assertIn("/dashboard/api/bitnet/processing/stream", self.docs.served_routes())

    def test_anchors_follow_github_slugs(self) -> None:
        self.assertEqual(self.docs.slug(" Build the installer"), "build-the-installer")
        self.assertEqual(self.docs.slug("`hub__batch` limits (v2)"), "hubbatch-limits-v2")

    def test_broken_links_and_anchors_are_reported_but_code_and_urls_are_not(self) -> None:
        self.write("other.md", "# Real heading\n")
        page = self.write("page.md", "\n".join([
            "[ok](other.md#real-heading) [web](https://example.com/x) [mail](mailto:user@example.com)",
            "[gone](missing.md) [no heading](other.md#nothing)",
            "```", "[inside code](missing.md)", "```",
        ]))
        with patch.object(self.docs, "ROOT", self.root):
            problems = self.docs.link_problems([page])
        self.assertEqual(problems, [
            "page.md:2: link to a missing file: missing.md",
            "page.md:2: link to a missing heading: other.md#nothing",
        ])

    def test_events_must_be_documented_and_documented_events_must_exist(self) -> None:
        self.write("src/mod.py", 'audit.write("thing.happened")\nself._complete_call(call, "x", None, "call.done", 1)\n')
        doc = self.write("events.md", "| Event | Level |\n|---|---|\n| `call.done` | INFO |\n| `gone.event` | INFO |\n")
        with patch.object(self.docs, "SRC", self.root / "src"), patch.object(self.docs, "EVENTS_DOC", doc):
            self.assertEqual(self.docs.logged_events(), {"thing.happened", "call.done"})
            self.assertEqual(self.docs.event_problems(), [
                "audit event thing.happened is logged but not in events.md",
                "audit event gone.event is in events.md but never logged",
            ])

    def test_runbook_settings_and_retired_wording_are_checked(self) -> None:
        runbook = self.write("runbook.md", "Run `hubctl install` once.\n")
        settings_doc = self.write("settings.md", "| `http` | `port` | 1 | x |\n| `http` | `typo` | 1 | x |\n")
        old = self.write("docs/page.md", "Use -Target hub|app here.\n")
        kept = self.write("docs/history/old.md", "Use -Target hub|app here.\n")
        with patch.object(self.docs, "RUNBOOK", runbook), patch.object(self.docs, "ROOT", self.root), \
                patch.object(self.docs, "SETTINGS_DOC", settings_doc):
            missing_commands = self.docs.hubctl_problems()
            settings = self.docs.settings_problems()
            retired = self.docs.retired_problems([old, kept])
        self.assertIn("hubctl restart is not described in runbook.md", missing_commands)
        self.assertNotIn("hubctl install is not described in runbook.md", missing_commands)
        self.assertIn("docs/settings.md lists settings.http.typo, which does not exist", settings)
        self.assertIn("setting settings.jobs.maxActive is not in docs/settings.md", settings)
        self.assertEqual(retired, ["docs/page.md:1: retired wording '-Target hub|app'"])

    def test_default_children_need_a_repository_address_and_a_contract(self) -> None:
        servers = {"mcp-a": {"repository": "https://example.com/o/mcp-a.git"}, "mcp-b": {},
                   "mcp-c": {"repository": "git@example.com:o/mcp-c"}}
        self.write("config/hub.example.json", json.dumps({"servers": servers}))
        self.write("docs/mcps/mcp-a.md", "contract\n")
        with patch.object(self.docs, "ROOT", self.root), patch.object(self.docs, "DOCS", self.root / "docs"):
            problems = self.docs.child_problems()
        self.assertEqual(problems, [
            "default child mcp-b has no https `repository` in config/hub.example.json",
            "default child mcp-b has no contract docs/mcps/mcp-b.md",
            "default child mcp-c has no https `repository` in config/hub.example.json",
            "default child mcp-c has no contract docs/mcps/mcp-c.md",
        ])

    def test_mcp_tools_playbook_catalog_and_config_sync_checks_detect_drift(self) -> None:
        reference = self.write("docs/reference.md", "| `hub__status` | client | ok |\n| `hub__ghost` | client | x |\n")
        self.write("docs/playbooks/demo.md", "# demo\n")
        self.write("README.md", "No playbook here\n")
        self.write("docs/client-rules.md", "demo\n")
        self.write("docs/playbooks/README.md", "demo\n")
        self.write("docs/clients/templates/mcp-hub/SKILL.md", "demo\n")
        self.write("config/agents.example.json", json.dumps({"version": 1, "agents": {"coordinator": {}, "typo": {}}}))
        self.write("config/hub.example.json", json.dumps({
            "servers": {"mcp-a": {"repository": "https://example.com/o/mcp-a.git"}},
            "features": {"f": {"tools": [{"server": "mcp-a", "name": "t", "exposeAs": "a__t"}]}},
        }))
        self.write("docs/mcps/mcp-a.md", "# contract without tool\n")
        with patch.object(self.docs, "ROOT", self.root), patch.object(self.docs, "DOCS", self.root / "docs"), \
                patch.object(self.docs, "REFERENCE", reference):
            tools = self.docs.mcp_tool_problems()
            playbooks = self.docs.playbook_catalog_problems()
            agents = self.docs.agent_config_problems()
            children = self.docs.child_problems()
        self.assertIn("MCP tool hub__plan is exposed but not in docs/reference.md", tools)
        self.assertIn("MCP tool hub__ghost is in docs/reference.md but not exposed", tools)
        self.assertEqual(playbooks, ["playbook demo is not listed in README.md"])
        self.assertIn("agent role sql is missing from config/agents.example.json", agents)
        self.assertIn("config/agents.example.json lists unknown role typo", agents)
        self.assertEqual(children, ["tool a__t in config/hub.example.json is not documented in docs/mcps/mcp-a.md"])


if __name__ == "__main__":
    unittest.main()
