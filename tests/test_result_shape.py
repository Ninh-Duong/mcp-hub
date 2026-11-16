from __future__ import annotations

import unittest
from typing import Any

from mcp.types import CallToolResult, ImageContent, TextContent

from mcp_hub.results.shape import is_condensable, parse_page_args, with_condensed_meta

_SCHEMA: dict[str, Any] = {"type": "object", "properties": {"n": {"type": "integer"}}}


def _text_result(text: str = "hello", **extra: Any) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], **extra)


class IsCondensableTest(unittest.TestCase):
    def test_plain_text_result_without_schema_is_condensable(self) -> None:
        self.assertTrue(is_condensable(None, _text_result()))

    def test_tool_with_output_schema_is_not_condensable(self) -> None:
        self.assertFalse(is_condensable(_SCHEMA, _text_result()))

    def test_result_with_structured_content_is_not_condensable(self) -> None:
        result = _text_result(structured_content={"n": 1})
        self.assertFalse(is_condensable(None, result))

    def test_result_with_non_text_block_is_not_condensable(self) -> None:
        result = CallToolResult(
            content=[
                TextContent(type="text", text="chart"),
                ImageContent(type="image", data="AAAA", mimeType="image/png"),
            ],
        )
        self.assertFalse(is_condensable(None, result))

    def test_error_result_is_not_condensable(self) -> None:
        self.assertFalse(is_condensable(None, _text_result(is_error=True)))


class WithCondensedMetaTest(unittest.TestCase):
    def test_content_is_identical_after_marking(self) -> None:
        original = _text_result("line one")
        marked = with_condensed_meta(original, "r-abc", 2, 9)
        self.assertEqual(marked.content, original.content)

    def test_existing_meta_keys_survive(self) -> None:
        original = _text_result(meta={"progressToken": "p-1"})
        marked = with_condensed_meta(original, "r-abc", 2, 9)
        self.assertEqual(marked.meta["progressToken"], "p-1")
        self.assertEqual(
            marked.meta["mcp-hub/condensed"],
            {"result_id": "r-abc", "kept": 2, "total": 9},
        )

    def test_input_result_is_not_mutated(self) -> None:
        original = _text_result(meta={"progressToken": "p-1"})
        with_condensed_meta(original, "r-abc", 2, 9)
        self.assertEqual(original.meta, {"progressToken": "p-1"})


class ParsePageArgsTest(unittest.TestCase):
    def _parse(self, arguments: dict[str, Any]) -> tuple[int, int] | str:
        return parse_page_args(arguments, default_offset=0, default_limit=20000)

    def test_missing_keys_use_defaults(self) -> None:
        self.assertEqual(self._parse({}), (0, 20000))

    def test_integers_are_accepted(self) -> None:
        self.assertEqual(self._parse({"offset": 5, "limit": 10}), (5, 10))

    def test_digit_strings_are_accepted(self) -> None:
        self.assertEqual(self._parse({"offset": "7", "limit": "3"}), (7, 3))

    def test_list_value_is_rejected_and_named(self) -> None:
        error = self._parse({"limit": [1]})
        self.assertIsInstance(error, str)
        self.assertIn("limit", error)

    def test_dict_value_is_rejected_and_named(self) -> None:
        error = self._parse({"offset": {"n": 1}})
        self.assertIsInstance(error, str)
        self.assertIn("offset", error)

    def test_letters_are_rejected(self) -> None:
        error = self._parse({"offset": "abc"})
        self.assertIsInstance(error, str)
        self.assertIn("offset", error)

    def test_non_ascii_digits_are_rejected(self) -> None:
        self.assertIsInstance(self._parse({"limit": "²"}), str)

    def test_negative_offset_is_rejected(self) -> None:
        error = self._parse({"offset": -1})
        self.assertIsInstance(error, str)
        self.assertIn("offset", error)

    def test_zero_limit_is_rejected(self) -> None:
        error = self._parse({"limit": 0})
        self.assertIsInstance(error, str)
        self.assertIn("limit", error)

    def test_booleans_are_rejected(self) -> None:
        self.assertIn("offset", self._parse({"offset": True}))
        self.assertIn("limit", self._parse({"limit": True}))


if __name__ == "__main__":
    unittest.main()
