from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
