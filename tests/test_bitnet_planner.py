from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any

from mcp_hub.bitnet.client import BitnetClient
from mcp_hub.bitnet.planner import BitnetPlanAssistant
from mcp_hub.extensions import PlanContext


class _FakeBitnetClient(BitnetClient):
    def __init__(self, reply: dict[str, Any] | None = None, should_raise: bool = False) -> None:
        self.reply = reply
        self.should_raise = should_raise
        self.called = False

    async def complete_json(
        self, system: str, user: str, *, max_tokens: int = 256, timeout: float = 3.0,
    ) -> dict[str, Any] | None:
        self.called = True
        if self.should_raise:
            raise RuntimeError("Client exploded")
        return self.reply


class BitnetPlannerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
        self.playbooks = {
            "rcid-bug": "when you have a request ID",
            "jira-ticket-bug": "when you have a Jira ticket key",
        }
        self.servers = ("mcp-server-logs", "mcp-sqlserver")
        self.envs = ("dev", "stg", "prod")

    def _context(self, task: str) -> PlanContext:
        return PlanContext(
            task=task,
            playbooks=self.playbooks,
            servers=self.servers,
            envs=self.envs,
            now=self.now,
        )

    async def test_resolved_by_rules_never_calls_client(self) -> None:
        # Task specifies rcid (playbook), servers (logs, sql), env (prod), window (hom nay)
        task = "check rcid: 12345678-1234-1234-1234-123456789abc sql logs production hom nay"
        client = _FakeBitnetClient(reply={"env": "dev"})
        assistant = BitnetPlanAssistant(client)

        hints = await assistant.hints(self._context(task))
        self.assertFalse(client.called)
        self.assertIsNotNone(hints)
        self.assertEqual(hints.source, "rules")
        self.assertEqual(hints.env, "prod")

    async def test_model_fills_env_gives_bitnet_source(self) -> None:
        # Task only gives window and logs; env is missing
        task = "check logs hom nay"
        client = _FakeBitnetClient(reply={"env": "prod"})
        assistant = BitnetPlanAssistant(client)

        hints = await assistant.hints(self._context(task))
        self.assertTrue(client.called)
        self.assertIsNotNone(hints)
        self.assertEqual(hints.source, "bitnet")
        self.assertEqual(hints.env, "prod")
        self.assertIn("mcp-server-logs", hints.servers)

    async def test_model_returns_out_of_enum_env_dropped(self) -> None:
        task = "check logs hom nay"
        client = _FakeBitnetClient(reply={"env": "invalid-env"})
        assistant = BitnetPlanAssistant(client)

        hints = await assistant.hints(self._context(task))
        self.assertTrue(client.called)
        self.assertIsNotNone(hints)
        self.assertEqual(hints.source, "rules")
        self.assertIsNone(hints.env)

    async def test_model_returns_none_falls_back_to_rules(self) -> None:
        task = "check logs hom nay"
        client = _FakeBitnetClient(reply=None)
        assistant = BitnetPlanAssistant(client)

        hints = await assistant.hints(self._context(task))
        self.assertTrue(client.called)
        self.assertIsNotNone(hints)
        self.assertEqual(hints.source, "rules")
        self.assertIsNone(hints.env)

    async def test_client_raises_falls_back_to_rules(self) -> None:
        task = "check logs hom nay"
        client = _FakeBitnetClient(should_raise=True)
        assistant = BitnetPlanAssistant(client)

        hints = await assistant.hints(self._context(task))
        self.assertTrue(client.called)
        self.assertIsNotNone(hints)
        self.assertEqual(hints.source, "rules")

    async def test_window_hours_over_168_dropped(self) -> None:
        task = "investigate logs"
        client = _FakeBitnetClient(reply={"window_hours": 500})
        assistant = BitnetPlanAssistant(client)

        hints = await assistant.hints(self._context(task))
        self.assertTrue(client.called)
        self.assertIsNotNone(hints)
        self.assertIsNone(hints.since)

    async def test_rule_value_not_overwritten_by_model(self) -> None:
        # Rule finds env="prod", client proposes env="dev"
        task = "check logs in production"
        client = _FakeBitnetClient(reply={"env": "dev", "window_hours": 12})
        assistant = BitnetPlanAssistant(client)

        hints = await assistant.hints(self._context(task))
        self.assertTrue(client.called)
        self.assertIsNotNone(hints)
        self.assertEqual(hints.env, "prod")  # rule value preserved!
        self.assertEqual(hints.source, "bitnet")  # bitnet filled window
        self.assertIsNotNone(hints.since)

    async def test_completely_empty_unresolved_returns_none(self) -> None:
        task = "hello world"
        client = _FakeBitnetClient(reply=None)
        assistant = BitnetPlanAssistant(client)

        hints = await assistant.hints(self._context(task))
        self.assertIsNone(hints)

    async def test_model_fills_playbook_and_servers(self) -> None:
        task = "unclear task"
        client = _FakeBitnetClient(reply={"playbook": "rcid-bug", "servers": ["mcp-sqlserver"]})
        assistant = BitnetPlanAssistant(client)

        hints = await assistant.hints(self._context(task))
        self.assertIsNotNone(hints)
        self.assertEqual(hints.playbook, "rcid-bug")
        self.assertEqual(hints.servers, ("mcp-sqlserver",))
        self.assertEqual(hints.source, "bitnet")


if __name__ == "__main__":
    unittest.main()
