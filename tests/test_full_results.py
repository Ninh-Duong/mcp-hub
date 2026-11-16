from __future__ import annotations

import random
import unittest

from mcp_hub.results.full_results import FullResultStore


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

    def test_search_json_records_and_fields_projection(self) -> None:
        store = FullResultStore(max_entries=5, ttl_seconds=60.0)
        payload = '{"rows":[{"Id":1,"TenantId":967,"Extra":"aaa"},{"Id":2,"TenantId":976,"Extra":"bbb"}]}'
        rid = store.put(payload)

        found = store.search(rid, contains="976", fields=["Id", "TenantId"])
        self.assertIsNotNone(found)
        self.assertIn('"rows_matched": "1/2"'.replace(": ", ":"), found["text"])
        self.assertIn('"TenantId":976', found["text"])
        self.assertNotIn("Extra", found["text"])

        with self.assertRaises(ValueError):
            store.search(rid, regex="[unclosed")

    def test_search_plain_text_lines_and_single_line_snippets(self) -> None:
        store = FullResultStore(max_entries=5, ttl_seconds=60.0)
        rid_lines = store.put("ok 1\nFAIL needle here\nok 3")
        res = store.search(rid_lines, regex=r"needle\s+here")
        self.assertIn("L2: FAIL needle here", res["text"])

        rid_blob = store.put(("a" * 300) + "Target976Token" + ("b" * 300))
        res_blob = store.search(rid_blob, contains="976token")
        self.assertIn("Target976Token", res_blob["text"])


_ALPHABET = ("a", "é", "漢", "\U0001f600")  # 1, 2, 3 and 4 UTF-8 bytes


def _random_text(rng: random.Random, length: int) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(length))


def _live_bytes(store: FullResultStore, sizes: dict[str, int]) -> tuple[int, int]:
    """Total bytes and count of the stored entries that are still retrievable."""
    live = [size for rid, size in sizes.items() if store.page(rid) is not None]
    return sum(live), len(live)


class FullResultByteCapTest(unittest.TestCase):
    def test_oldest_entries_are_evicted_to_fit_max_bytes(self) -> None:
        store = FullResultStore(max_entries=10, ttl_seconds=60.0, max_bytes=10)
        first = store.put("aaaa")
        second = store.put("bbbb")
        third = store.put("cccc")
        self.assertIsNone(store.page(first))
        self.assertIsNotNone(store.page(second))
        self.assertIsNotNone(store.page(third))

    def test_byte_count_is_utf8_bytes_not_characters(self) -> None:
        store = FullResultStore(max_entries=10, ttl_seconds=60.0, max_bytes=10)
        self.assertIsNone(store.put("é" * 6))  # 6 characters, 12 bytes
        self.assertIsInstance(store.put("é" * 5), str)  # 5 characters, 10 bytes: fits exactly

    def test_oversized_entry_returns_none_and_evicts_nothing(self) -> None:
        store = FullResultStore(max_entries=10, ttl_seconds=60.0, max_bytes=10)
        kept = store.put("abcde")
        self.assertIsInstance(kept, str)
        self.assertIsNone(store.put("x" * 11))
        page = store.page(kept)
        self.assertIsNotNone(page)
        self.assertEqual(page["text"], "abcde")

    def test_total_bytes_never_exceed_max_bytes_under_random_sizes(self) -> None:
        rng = random.Random(20261115)
        max_bytes = 4096
        store = FullResultStore(max_entries=1000, ttl_seconds=60.0, max_bytes=max_bytes)
        sizes: dict[str, int] = {}
        for _ in range(300):
            text = _random_text(rng, rng.randint(0, 2000))
            rid = store.put(text)
            if rid is None:
                continue
            sizes[rid] = len(text.encode("utf-8"))
            self.assertIsNotNone(store.page(rid))
            total, _ = _live_bytes(store, sizes)
            self.assertLessEqual(total, max_bytes)
        total, count = _live_bytes(store, sizes)
        self.assertGreater(count, 0)
        self.assertLess(count, len(sizes))  # the cap forced some evictions


if __name__ == "__main__":
    unittest.main()
