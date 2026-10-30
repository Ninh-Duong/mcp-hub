from __future__ import annotations

import unittest

from mcp_hub.agents.specialists import ROLE_PREFIXES
from mcp_hub.config import HUB_ROOT


def _prompt(role: str) -> str:
    return (HUB_ROOT / "docs" / "agents" / f"{role}.md").read_text(encoding="utf-8")


class AgentPromptsTest(unittest.TestCase):
    def test_jira_prompt_reads_the_cache_before_syncing(self) -> None:
        prompt = _prompt("jira")
        self.assertLess(prompt.index("jira__read_ticket"), prompt.index("jira__sync_ticket"))
        self.assertIn("only when the ticket is not cached", prompt)
        self.assertIn("`force=true` only when the user explicitly asked", prompt)
        self.assertNotIn("sync it to the documented encrypted local cache and read it", prompt)

    def test_logs_prompt_reads_ai_context_before_remote_queries(self) -> None:
        prompt = _prompt("logs")
        self.assertLess(prompt.index("logs__list_ai_context_snapshots"), prompt.index("Query OpenObserve"))

    def test_every_specialist_prompt_forbids_repeats_and_warm_up_health_checks(self) -> None:
        for role in ROLE_PREFIXES:
            with self.subTest(role=role):
                prompt = _prompt(role)
                self.assertIn("Never repeat an identical", prompt)
                self.assertIn("only after a real call failed", prompt)


if __name__ == "__main__":
    unittest.main()
