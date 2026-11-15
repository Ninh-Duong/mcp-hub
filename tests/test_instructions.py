from __future__ import annotations

import unittest

from mcp_hub.instructions import build_instructions


MORE_PREFIX = "+"
MORE_SUFFIX = " more; see tools/list"


class BuildInstructionsTest(unittest.TestCase):
    def test_total_length_never_exceeds_the_budget(self) -> None:
        sections = [
            ("Logs", [f"logs line number {index} with some words" for index in range(40)]),
            ("Jira", [f"jira line {index}" for index in range(10)]),
        ]
        for budget in range(1, 400, 7):
            with self.subTest(budget=budget):
                text = build_instructions("Summary of the hub. " * 3, sections, budget=budget)
                self.assertLessEqual(len(text), budget)

    def test_default_budget_is_4096(self) -> None:
        sections = [("Tools", [f"tool line {index} " + "x" * 80 for index in range(200)])]
        text = build_instructions("Hub summary", sections)
        self.assertLessEqual(len(text), 4096)
        self.assertTrue(text.startswith("Hub summary"))

    def test_sections_keep_their_order(self) -> None:
        sections = [
            ("First section", ["alpha"]),
            ("Second section", ["beta"]),
            ("Third section", ["gamma"]),
        ]
        text = build_instructions("Summary", sections, budget=4096)
        positions = [text.index(title) for title, _lines in sections]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(text.split("\n"), ["Summary", "First section", "alpha", "Second section", "beta",
                                            "Third section", "gamma"])

    def test_long_list_is_truncated_with_more_note(self) -> None:
        items = [f"tool_{index:03d}" for index in range(100)]
        text = build_instructions("Summary", [("Tools", items)], budget=200)
        lines = text.split("\n")
        shown = lines[2:-1]
        self.assertEqual(lines[:2], ["Summary", "Tools"])
        self.assertEqual(shown, items[:len(shown)])
        self.assertEqual(lines[-1], f"{MORE_PREFIX}{100 - len(shown)}{MORE_SUFFIX}")
        self.assertLessEqual(len(text), 200)

    def test_sections_after_a_truncated_section_are_not_added(self) -> None:
        sections = [
            ("Logs", [f"log {index}" for index in range(50)]),
            ("Jira", ["jira only"]),
        ]
        text = build_instructions("Summary", sections, budget=120)
        self.assertIn(MORE_SUFFIX, text)
        self.assertNotIn("Jira", text)
        self.assertNotIn("jira only", text)

    def test_note_is_dropped_line_for_line_when_it_does_not_fit(self) -> None:
        # Only room for the summary, the title and a few short lines, not the note after them.
        text = build_instructions("S", [("T", ["aa", "bb", "cc", "dd"])], budget=12)
        self.assertLessEqual(len(text), 12)
        self.assertNotIn(MORE_SUFFIX, text)
        self.assertEqual(text.split("\n")[0], "S")

    def test_a_section_other_than_the_tool_list_names_its_source_in_the_note(self) -> None:
        items = [f"rule line {index}" for index in range(100)]
        text = build_instructions("Summary", [("", items, "docs/client-rules.md")], budget=200)
        last = text.split("\n")[-1]
        self.assertRegex(last, r"^\+\d+ more lines in docs/client-rules\.md$")
        self.assertNotIn("tools/list", text)
        self.assertLessEqual(len(text), 200)

    def test_complete_section_has_no_note(self) -> None:
        text = build_instructions("Summary", [("Tools", ["one", "two"])], budget=4096)
        self.assertEqual(text, "Summary\nTools\none\ntwo")
        self.assertNotIn(MORE_SUFFIX, text)

    def test_summary_longer_than_the_budget_is_cut(self) -> None:
        text = build_instructions("x" * 500, [("Tools", ["one"])], budget=100)
        self.assertEqual(text, "x" * 100)

    def test_empty_inputs_give_empty_text(self) -> None:
        self.assertEqual(build_instructions("", [], budget=50), "")


if __name__ == "__main__":
    unittest.main()
