from __future__ import annotations

import unittest

from mcp_hub.full_results import FullResultStore


class FullResultsTest(unittest.TestCase):
    def test_put_and_page_round_trip_with_pagination(self) -> None:
        store = FullResultStore(max_entries=10, ttl_seconds=60.0)
        text = "abcdefghij" * 10  # 100 chars
        rid = store.put(text)
        self.assertTrue(rid.startswith("r-"))

        # First page
        page1 = store.page(rid, offset=0, limit=40)
        self.assertIsNotNone(page1)
        self.assertEqual(page1["offset"], 0)
        self.assertEqual(page1["limit"], 40)
        self.assertEqual(page1["total_chars"], 100)
        self.assertEqual(page1["text"], text[:40])
        self.assertEqual(page1["next_offset"], 40)

        # Second page
        page2 = store.page(rid, offset=40, limit=40)
        self.assertIsNotNone(page2)
        self.assertEqual(page2["text"], text[40:80])
        self.assertEqual(page2["next_offset"], 80)

        # Third (final) page
        page3 = store.page(rid, offset=80, limit=40)
        self.assertIsNotNone(page3)
        self.assertEqual(page3["text"], text[80:100])
        self.assertIsNone(page3["next_offset"])

        # Out-of-bounds offset
        page4 = store.page(rid, offset=200, limit=40)
        self.assertIsNotNone(page4)
        self.assertEqual(page4["text"], "")
        self.assertIsNone(page4["next_offset"])

    def test_lru_eviction_when_max_entries_exceeded(self) -> None:
        store = FullResultStore(max_entries=2, ttl_seconds=60.0)
        r1 = store.put("result 1")
        r2 = store.put("result 2")

        # Access r1 to make it most recently used
        self.assertIsNotNone(store.page(r1))

        # Add r3, which should evict r2 (least recently used)
        r3 = store.put("result 3")
        self.assertIsNotNone(store.page(r1))
        self.assertIsNone(store.page(r2))
        self.assertIsNotNone(store.page(r3))

    def test_ttl_expiry_with_simulated_clock(self) -> None:
        current_time = 100.0

        def clock() -> float:
            return current_time

        store = FullResultStore(max_entries=5, ttl_seconds=10.0, clock=clock)
        rid = store.put("result content")
        self.assertIsNotNone(store.page(rid))

        # Advance clock past TTL
        current_time = 111.0
        self.assertIsNone(store.page(rid))

    def test_unknown_result_id_returns_none(self) -> None:
        store = FullResultStore(max_entries=5, ttl_seconds=60.0)
        self.assertIsNone(store.page("r-nonexistent"))


if __name__ == "__main__":
    unittest.main()
