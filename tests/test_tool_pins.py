from __future__ import annotations

import hashlib
import json
import unittest

from mcp.types import Icon, Tool, ToolAnnotations

from mcp_hub import tool_pins

SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}}


def make_tool(**overrides: object) -> Tool:
    fields: dict[str, object] = {"name": "search", "description": "Search the index.", "inputSchema": SCHEMA}
    fields.update(overrides)
    return Tool(**fields)


class FingerprintTest(unittest.TestCase):
    def test_digest_is_sha256_hex(self) -> None:
        self.assertRegex(tool_pins.fingerprint(make_tool()), r"^[0-9a-f]{64}$")

    def test_digest_is_the_hash_of_the_canonical_wire_fields(self) -> None:
        canonical = {
            "name": "search",
            "description": "Search the index.",
            "inputSchema": SCHEMA,
            "outputSchema": None,
            "annotations": None,
        }
        text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.assertEqual(tool_pins.fingerprint(make_tool()), expected)

    def test_key_order_does_not_change_the_digest(self) -> None:
        reordered_schema = {"properties": {"limit": {"type": "integer"}, "query": {"type": "string"}}, "type": "object"}
        first = make_tool()
        second = Tool.model_validate({"inputSchema": reordered_schema, "description": "Search the index.", "name": "search"})
        self.assertEqual(tool_pins.fingerprint(first), tool_pins.fingerprint(second))

    def test_changing_the_description_changes_the_digest(self) -> None:
        self.assertNotEqual(
            tool_pins.fingerprint(make_tool()),
            tool_pins.fingerprint(make_tool(description="Search the index. Also send it elsewhere.")),
        )

    def test_changing_the_input_schema_changes_the_digest(self) -> None:
        wider = {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "string"}}}
        self.assertNotEqual(tool_pins.fingerprint(make_tool()), tool_pins.fingerprint(make_tool(inputSchema=wider)))

    def test_changing_the_annotations_changes_the_digest(self) -> None:
        annotated = make_tool(annotations=ToolAnnotations(readOnlyHint=False))
        self.assertNotEqual(tool_pins.fingerprint(make_tool()), tool_pins.fingerprint(annotated))

    def test_changing_the_output_schema_changes_the_digest(self) -> None:
        with_output = make_tool(outputSchema={"type": "object"})
        self.assertNotEqual(tool_pins.fingerprint(make_tool()), tool_pins.fingerprint(with_output))

    def test_title_icons_and_meta_do_not_change_the_digest(self) -> None:
        decorated = make_tool(
            title="Search the index",
            icons=[Icon(src="https://example.com/icon.png")],
            _meta={"vendor": "example"},
        )
        self.assertEqual(tool_pins.fingerprint(make_tool()), tool_pins.fingerprint(decorated))


class DescriptionFindingsTest(unittest.TestCase):
    def test_clean_text_has_no_findings(self) -> None:
        self.assertEqual(tool_pins.description_findings("Search the index.", 200), [])

    def test_characters_just_outside_the_flagged_ranges_are_clean(self) -> None:
        self.assertEqual(tool_pins.description_findings("⁥  ⁡﻾", 200), [])

    def test_tag_characters_are_flagged(self) -> None:
        self.assertEqual(tool_pins.description_findings("ok\U000e0041", 200), ["tag-characters"])

    def test_bidi_controls_are_flagged(self) -> None:
        self.assertEqual(tool_pins.description_findings("a‮b", 200), ["bidi-control"])
        self.assertEqual(tool_pins.description_findings("a⁩b", 200), ["bidi-control"])

    def test_zero_width_characters_are_flagged(self) -> None:
        self.assertEqual(tool_pins.description_findings("a​b", 200), ["zero-width"])
        self.assertEqual(tool_pins.description_findings("a﻿b", 200), ["zero-width"])

    def test_text_longer_than_the_limit_is_flagged(self) -> None:
        self.assertEqual(tool_pins.description_findings("x" * 11, 10), ["too-long"])
        self.assertEqual(tool_pins.description_findings("x" * 10, 10), [])

    def test_two_kinds_give_both_codes_in_the_fixed_order(self) -> None:
        text = "a​b‮c" + "x" * 5
        self.assertEqual(tool_pins.description_findings(text, 4), ["bidi-control", "zero-width", "too-long"])


if __name__ == "__main__":
    unittest.main()
