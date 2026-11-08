from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from mcp_hub.bitnet.summarize import BitnetDraftSummarizer


class BitnetDraftSummarizerTest(unittest.IsolatedAsyncioTestCase):
    async def test_successful_summary_has_draft_prefix(self) -> None:
        client = AsyncMock()
        client.complete_text.return_value = "1. Found error in orders service [src-1]."
        summarizer = BitnetDraftSummarizer(client)

        result = await summarizer.summarize(
            "What went wrong?",
            [{"source_id": "src-1", "message": "Connection refused"}],
        )

        self.assertEqual(result, "Draft (local model):\n1. Found error in orders service [src-1].")
        client.complete_text.assert_awaited_once()
        prompt, user_text = client.complete_text.call_args.args
        self.assertIn("Summarize these findings", prompt)
        self.assertIn("What went wrong?", user_text)
        self.assertIn("src-1", user_text)

    async def test_empty_or_whitespace_from_client_returns_none(self) -> None:
        client = AsyncMock()
        summarizer = BitnetDraftSummarizer(client)

        client.complete_text.return_value = None
        self.assertIsNone(await summarizer.summarize("q", []))

        client.complete_text.return_value = ""
        self.assertIsNone(await summarizer.summarize("q", []))

        client.complete_text.return_value = "   \n\t  "
        self.assertIsNone(await summarizer.summarize("q", []))

    async def test_truncates_findings_at_10000_chars(self) -> None:
        client = AsyncMock()
        client.complete_text.return_value = "Summary"
        summarizer = BitnetDraftSummarizer(client)

        large_payload = [{"data": "x" * 20000}]
        await summarizer.summarize("long query", large_payload)

        _, user_text = client.complete_text.call_args.args
        self.assertLessEqual(len(user_text), 10000)


if __name__ == "__main__":
    unittest.main()
