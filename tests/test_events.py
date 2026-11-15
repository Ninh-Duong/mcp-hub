from __future__ import annotations

import asyncio
import unittest

from mcp.server.subscriptions import ToolsListChanged

from mcp_hub.events import HubEvents

DEBOUNCE = 0.02
SETTLE = 0.1  # several debounce windows, so a pending publish has fired


class HubEventsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.events = HubEvents(DEBOUNCE)
        self.published: list[object] = []
        self.events.bus.subscribe(self.published.append)

    async def asyncTearDown(self) -> None:
        self.events.close()

    async def test_prime_records_the_baseline_without_publishing(self) -> None:
        self.events.prime("a")
        await asyncio.sleep(SETTLE)
        self.assertEqual(self.published, [])
        self.assertFalse(await self.events.tools_changed("a"))
        await asyncio.sleep(SETTLE)
        self.assertEqual(self.published, [])

    async def test_the_same_signature_as_the_last_one_is_not_a_change(self) -> None:
        self.events.prime("a")
        self.assertTrue(await self.events.tools_changed("b"))
        self.assertFalse(await self.events.tools_changed("b"))
        await asyncio.sleep(SETTLE)
        self.assertEqual(self.published, [ToolsListChanged()])

    async def test_a_burst_inside_the_window_publishes_once(self) -> None:
        self.events.prime("a")
        for signature in ("b", "c", "d", "e", "f"):
            self.assertTrue(await self.events.tools_changed(signature))
        await asyncio.sleep(SETTLE)
        self.assertEqual(self.published, [ToolsListChanged()])

    async def test_a_change_after_the_window_publishes_again(self) -> None:
        self.events.prime("a")
        await self.events.tools_changed("b")
        await asyncio.sleep(SETTLE)
        await self.events.tools_changed("c")
        await asyncio.sleep(SETTLE)
        self.assertEqual(self.published, [ToolsListChanged(), ToolsListChanged()])

    async def test_close_cancels_a_pending_publish(self) -> None:
        self.events.prime("a")
        await self.events.tools_changed("b")
        self.events.close()
        await asyncio.sleep(SETTLE)
        self.assertEqual(self.published, [])


if __name__ == "__main__":
    unittest.main()
