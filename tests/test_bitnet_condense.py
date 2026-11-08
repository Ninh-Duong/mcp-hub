from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from mcp_hub.bitnet.client import BitnetClient
from mcp_hub.bitnet.condense import BitnetCondenser, group_lines, template_of


class _FakeCondenseClient(BitnetClient):
    def __init__(self, reply: dict[str, Any] | None = None, should_raise: bool = False) -> None:
        self.reply = reply
        self.should_raise = should_raise
        self.call_count = 0

    async def complete_json(
        self, system: str, user: str, *, max_tokens: int = 256, timeout: float = 3.0,
    ) -> dict[str, Any] | None:
        self.call_count += 1
        if self.should_raise:
            raise RuntimeError("Client error")
        return self.reply


class BitnetCondenseTest(unittest.IsolatedAsyncioTestCase):
    def test_template_collapses_volatile_variants(self) -> None:
        l1 = "2026-11-08T10:00:00Z user a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d status 200"
        l2 = "2026-11-08T12:00:00Z user 00000000-0000-0000-0000-000000000000 status 404"
        self.assertEqual(template_of(l1), template_of(l2))

    def test_group_lines_under_50_lines_returns_unchanged(self) -> None:
        text = "\n".join(f"line {i}" for i in range(20))
        lines, total = group_lines(text)
        self.assertEqual(total, 20)
        self.assertEqual(len(lines), 20)
        self.assertFalse(any(l.startswith("[x") for l in lines))

    def test_group_lines_5000_lines_with_20_templates(self) -> None:
        raw_lines = []
        for i in range(5000):
            tmpl_name = f"service_{chr(ord('a') + (i % 20))}"
            raw_lines.append(f"2026-11-08T10:00:00Z {tmpl_name} processed id {i}")
        text = "\n".join(raw_lines)
        lines, total = group_lines(text)

        self.assertEqual(total, 5000)
        self.assertEqual(len(lines), 20)
        self.assertTrue(all(l.startswith("[x250] ") for l in lines))

    def test_group_lines_error_lines_sort_first(self) -> None:
        raw_lines = []
        for i in range(60):
            if i == 55:
                raw_lines.append("2026-11-08T10:00:00Z fatal error in service")
            else:
                raw_lines.append(f"2026-11-08T10:00:00Z normal info message {i % 5}")
        text = "\n".join(raw_lines)
        lines, total = group_lines(text)

        self.assertEqual(total, 60)
        self.assertIn("fatal error", lines[0])

    async def test_short_result_after_step_a_never_calls_client(self) -> None:
        text = "\n".join(f"info line {i % 2}" for i in range(60))
        client = _FakeCondenseClient(reply={"keep": [1]})
        condenser = BitnetCondenser(client, max_chars=6000)

        res = await condenser.condense(text, "find error")
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "rules")
        self.assertEqual(client.call_count, 0)

    async def test_empty_task_returns_rules_condensed(self) -> None:
        text = "\n".join(f"line {i}" for i in range(100))
        client = _FakeCondenseClient(reply={"keep": [1]})
        condenser = BitnetCondenser(client, max_chars=500)

        res = await condenser.condense(text, "")
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "rules")
        self.assertEqual(client.call_count, 0)

    async def test_bitnet_picks_lines_plus_error_lines(self) -> None:
        raw_lines = [
            f"service_tag_{i}: error on connect" if i == 10
            else f"service_tag_{i}: normal status event payload padding data"
            for i in range(100)
        ]
        text = "\n".join(raw_lines)

        client = _FakeCondenseClient(reply={"keep": [3, 7]})
        condenser = BitnetCondenser(client, max_chars=1000)

        res = await condenser.condense(text, "find issues")
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "bitnet")
        self.assertEqual(client.call_count, 1)
        self.assertIn("error on connect", res.text)

    async def test_out_of_range_numbers_ignored(self) -> None:
        raw_lines = [f"service_tag_{i}: line with lots of padding content to trigger chunking" for i in range(100)]
        text = "\n".join(raw_lines)
        client = _FakeCondenseClient(reply={"keep": [99999, -5, "not an int"]})
        condenser = BitnetCondenser(client, max_chars=500)

        res = await condenser.condense(text, "find something")
        self.assertIsNotNone(res)
        self.assertIn("omitted", res.text)

    async def test_client_returns_none_falls_back_to_rules(self) -> None:
        raw_lines = [f"service_tag_{i}: line with content to exceed max chars capacity" for i in range(100)]
        text = "\n".join(raw_lines)
        client = _FakeCondenseClient(reply=None)
        condenser = BitnetCondenser(client, max_chars=300)

        res = await condenser.condense(text, "task")
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "rules")

    async def test_budget_exhaustion_stops_further_chunks(self) -> None:
        # Create enough distinct lines to exceed 10,000 characters chunk threshold
        raw_lines = [f"service_tag_{i}: " + ("x" * 250) for i in range(60)]
        text = "\n".join(raw_lines)
        client = _FakeCondenseClient(reply={"keep": [1]})

        times = [0.0, 1.0, 15.0]

        def fake_clock() -> float:
            return times.pop(0) if times else 50.0

        condenser = BitnetCondenser(client, max_chars=1000, timeout=8.0, clock=fake_clock)
        res = await condenser.condense(text, "task")

        self.assertIsNotNone(res)
        self.assertEqual(client.call_count, 1)

    async def test_unexpected_exception_returns_none(self) -> None:
        client = _FakeCondenseClient()
        condenser = BitnetCondenser(client)
        with patch("mcp_hub.bitnet.condense.group_lines", side_effect=RuntimeError("boom")):
            res = await condenser.condense("some text", "task")
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
