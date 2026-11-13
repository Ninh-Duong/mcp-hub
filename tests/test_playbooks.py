"""Playbooks: parsing their frontmatter, merging includes, and the repository's own playbooks."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mcp_hub.config import HUB_ROOT, ConfigError
from mcp_hub.playbooks import load_playbooks, parse_playbook

BASIC = """---
id: basic
when: The user gives a thing.
servers: [child-a]
tools: [a__read,
        a__list]
max_limit: 40
needs: [logs]
---
## Steps
1. Read it.
"""


def write(directory: Path, name: str, text: str) -> None:
    (directory / f"{name}.md").write_text(text, encoding="utf-8")


class ParsePlaybookTest(unittest.TestCase):
    def test_frontmatter_and_body(self) -> None:
        playbook = parse_playbook(BASIC, "basic")
        self.assertEqual(playbook.tools, ("a__read", "a__list"))  # continued on an indented line
        self.assertEqual((playbook.servers, playbook.max_limit, playbook.needs), (("child-a",), 40, ("logs",)))
        self.assertTrue(playbook.body.startswith("## Steps"))

    def test_invalid_playbooks_name_the_problem(self) -> None:
        cases = {
            "no frontmatter": "## Steps",
            "id differs": BASIC.replace("id: basic", "id: other"),
            "no when": BASIC.replace("when: The user gives a thing.\n", ""),
            "not a list": BASIC.replace("servers: [child-a]", "servers: child-a"),
            "no tools": BASIC.replace("tools: [a__read,\n        a__list]", "tools: []"),
            "bad limit": BASIC.replace("max_limit: 40", "max_limit: many"),
            "unknown need": BASIC.replace("needs: [logs]", "needs: [jira]"),
            "line without colon": BASIC.replace("max_limit: 40", "max_limit 40"),
        }
        for label, text in cases.items():
            with self.subTest(label), self.assertRaises(ConfigError):
                parse_playbook(text, "basic")


class LoadPlaybooksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_includes_add_servers_tools_needs_and_steps(self) -> None:
        write(self.root, "basic", BASIC)
        write(self.root, "outer", BASIC.replace("id: basic", "id: outer").replace("needs: [logs]", "includes: [basic]")
              .replace("servers: [child-a]", "servers: [child-b]").replace("max_limit: 40", "max_limit: 90"))
        write(self.root, "README", "# not a playbook")
        outer = load_playbooks(self.root)["outer"]
        self.assertEqual(outer.servers, ("child-b", "child-a"))
        self.assertEqual((outer.needs, outer.max_limit), (("logs",), 40))
        self.assertIn("# Included: basic", outer.body)
        self.assertEqual(set(load_playbooks(self.root)), {"basic", "outer"})

    def test_unknown_or_nested_includes_are_refused(self) -> None:
        write(self.root, "outer", BASIC.replace("id: basic", "id: outer").replace("needs: [logs]", "includes: [missing]"))
        with self.assertRaises(ConfigError):
            load_playbooks(self.root)

    def test_a_missing_directory_has_no_playbooks(self) -> None:
        self.assertEqual(load_playbooks(self.root / "none"), {})


class RepositoryPlaybooksTest(unittest.TestCase):
    def test_the_shipped_playbooks_load_and_name_only_allowlisted_tools(self) -> None:
        playbooks = load_playbooks()
        self.assertEqual(set(playbooks), {"rcid-bug", "jira-ticket-bug", "service-errors", "pr-review", "source-search"})
        example = json.loads((HUB_ROOT / "config" / "hub.example.json").read_text(encoding="utf-8"))
        allowlisted = {tool["exposeAs"]: tool["server"] for feature in example["features"].values()
                       for tool in feature["tools"]}
        for playbook in playbooks.values():
            with self.subTest(playbook.id):
                self.assertLessEqual(set(playbook.tools), set(allowlisted))
                self.assertLessEqual({allowlisted[tool] for tool in playbook.tools}, set(playbook.servers))


if __name__ == "__main__":
    unittest.main()
