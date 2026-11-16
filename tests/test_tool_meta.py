"""tool_meta: the read-only check for tools, as objects and as dicts (moved from server.py)."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from mcp_hub.tool_meta import is_read_only_tool


class IsReadOnlyToolTest(unittest.TestCase):
    def test_a_read_only_hint_on_an_object_is_read_only(self) -> None:
        self.assertTrue(is_read_only_tool(SimpleNamespace(annotations=SimpleNamespace(read_only_hint=True))))

    def test_the_hint_must_be_exactly_true(self) -> None:
        self.assertFalse(is_read_only_tool(SimpleNamespace(annotations=SimpleNamespace(read_only_hint="true"))))

    def test_without_annotations_a_tool_is_not_read_only(self) -> None:
        self.assertFalse(is_read_only_tool(SimpleNamespace(annotations=None)))
        self.assertFalse(is_read_only_tool(SimpleNamespace()))

    def test_dict_form_accepts_both_key_spellings(self) -> None:
        self.assertTrue(is_read_only_tool({"annotations": {"readOnlyHint": True}}))
        self.assertTrue(is_read_only_tool({"annotations": {"read_only_hint": True}}))
        self.assertFalse(is_read_only_tool({"annotations": {"readOnlyHint": False}}))
        self.assertFalse(is_read_only_tool({}))


if __name__ == "__main__":
    unittest.main()
