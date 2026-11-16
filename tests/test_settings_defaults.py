"""The tunables moved from code into settings keep their previous defaults (FR-016, FR-017), and the consumers read them.

Each row is (settings group, key, the literal the code used before the move). A change to any default is a behaviour
change and must be made on purpose, with this table updated in the same commit.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

from mcp_hub.bitnet.summarize import BitnetDraftSummarizer
from mcp_hub.settings import ActivitySettings, BitnetSettings, JobSettings, Settings

MOVED = (
    ("activity", "batch_size", 50),
    ("activity", "batch_seconds", 1.0),
    ("activity", "queue_limit", 10000),
    ("activity", "stream_keepalive_seconds", 15.0),
    ("bitnet", "sidecar_context_tokens", 4096),
    ("bitnet", "sidecar_health_seconds", 0.5),
    ("bitnet", "sidecar_recheck_seconds", 1.0),
    ("bitnet", "summary_input_chars", 10000),
    ("bitnet", "summary_output_tokens", 400),
    ("bitnet", "summary_timeout_seconds", 30.0),
    ("bitnet", "setup_wait_seconds", 5.0),
    ("bitnet", "condense_chunk_chars", 10000),
    ("bitnet", "condense_min_chars", 9000),
    ("bitnet", "condense_timeout_seconds", 8.0),
    ("jobs", "provider_timeout_seconds", 60.0),
    ("jobs", "provider_output_tokens", 2048),
    ("jobs", "coordinator_input_chars", 12000),
    ("jobs", "coordinator_output_tokens", 1200),
    ("jobs", "coordinator_summary_chars", 4000),
    ("jobs", "detail_page_limit", 2000),
    ("jobs", "question_max_chars", 5000),
)
_GROUP_CLASSES = {"activity": ActivitySettings, "bitnet": BitnetSettings, "jobs": JobSettings}


class MovedDefaultsTest(unittest.TestCase):
    def test_every_moved_tunable_keeps_its_previous_default(self) -> None:
        for group, key, previous in MOVED:
            with self.subTest(setting=f"{group}.{key}"):
                self.assertEqual(getattr(_GROUP_CLASSES[group](), key), previous)

    def test_the_groups_are_reachable_from_the_settings_root(self) -> None:
        root = Settings()
        self.assertIsInstance(root.activity, ActivitySettings)
        self.assertEqual(root.jobs.detail_page_limit, 2000)
        self.assertEqual(root.bitnet.condense_min_chars, 9000)


class SummarizerReadsItsSettingsTest(unittest.TestCase):
    def test_the_input_is_cut_to_the_configured_length_and_the_limits_are_passed(self) -> None:
        settings = BitnetSettings(summary_input_chars=50, summary_output_tokens=7, summary_timeout_seconds=3.5)
        client = AsyncMock()
        client.complete_text = AsyncMock(return_value="a draft")
        summarizer = BitnetDraftSummarizer(client, settings)
        asyncio.run(summarizer.summarize("why", [{"text": "x" * 500}]))
        _, user_text = client.complete_text.call_args.args
        self.assertLessEqual(len(user_text), 50)
        self.assertEqual(client.complete_text.call_args.kwargs, {"max_tokens": 7, "timeout": 3.5})


if __name__ == "__main__":
    unittest.main()
