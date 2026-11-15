from __future__ import annotations

import unittest
from typing import Any

from mcp.types import CallToolResult, ImageContent, TextContent

from mcp_hub.result_shape import is_condensable, with_condensed_meta

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


if __name__ == "__main__":
    unittest.main()
