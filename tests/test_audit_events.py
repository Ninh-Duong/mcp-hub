from __future__ import annotations

import re
import unittest
from pathlib import Path

from mcp_hub import audit_events

EVENTS_DOC = Path(__file__).resolve().parents[1] / "docs" / "security-and-logging.md"
NAME = re.compile(r"^[a-z_]+(?:\.[a-z_]+)+$")


class AuditEventNamesTest(unittest.TestCase):
    def test_every_new_event_name_is_a_dotted_lowercase_name(self) -> None:
        self.assertEqual(
            set(audit_events.ALL),
            {
                "tools.list_changed.sent",
                "tool.definition.changed",
                "tool.description.suspicious",
                "tool.arguments.invalid",
            },
        )
        for name in audit_events.ALL:
            with self.subTest(name=name):
                self.assertRegex(name, NAME)

    def test_every_new_event_name_is_documented_in_the_event_table(self) -> None:
        text = EVENTS_DOC.read_text(encoding="utf-8")
        for name in audit_events.ALL:
            with self.subTest(name=name):
                self.assertIn(f"| `{name}` |", text)


if __name__ == "__main__":
    unittest.main()
