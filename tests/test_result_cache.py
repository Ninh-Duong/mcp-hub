from __future__ import annotations

import asyncio
import unittest

from mcp.types import CallToolResult, TextContent

from mcp_hub.result_cache import ResultCache, cache_key


def _result(text: str, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=is_error)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Child:
    """Counts calls and returns a fresh result each time."""

    def __init__(self, text: str = "ok", *, is_error: bool = False, error: Exception | None = None) -> None:
        self.text = text
        self.is_error = is_error
        self.error = error
        self.calls = 0
        self.release: asyncio.Event | None = None

    async def __call__(self) -> CallToolResult:
        self.calls += 1
        number = self.calls
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return _result(f"{self.text}-{number}", is_error=self.is_error)


def _text(result: CallToolResult) -> str:
    return result.content[0].text


class CacheKeyTest(unittest.TestCase):
    def test_key_is_canonical_for_argument_order(self) -> None:
        self.assertEqual(cache_key("t", {"a": 1, "b": [1, 2]}), cache_key("t", {"b": [1, 2], "a": 1}))
        self.assertNotEqual(cache_key("t", {"a": 1}), cache_key("u", {"a": 1}))
        self.assertEqual(cache_key("t", None), cache_key("t", {}))

    def test_unserializable_arguments_have_no_key(self) -> None:
        self.assertIsNone(cache_key("t", {"value": object()}))
        self.assertIsNone(cache_key("t", {"value": float("nan")}))


class ResultCacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_second_identical_call_is_a_hit(self) -> None:
        cache, child = ResultCache(), _Child()
        first, first_cached = await cache.get_or_call("t", {"q": 1}, 60, child)
        second, second_cached = await cache.get_or_call("t", {"q": 1}, 60, child)
        self.assertEqual((first_cached, second_cached), (False, True))
        self.assertEqual(_text(second), "ok-1")
        self.assertEqual(child.calls, 1)
        self.assertEqual(cache.stats(), {"hits": 1, "misses": 1, "entries": 1})

    async def test_different_arguments_miss(self) -> None:
        cache, child = ResultCache(), _Child()
        await cache.get_or_call("t", {"q": 1}, 60, child)
        _, cached = await cache.get_or_call("t", {"q": 2}, 60, child)
        self.assertFalse(cached)
        self.assertEqual(child.calls, 2)

    async def test_entry_expires_after_ttl(self) -> None:
        clock = _Clock()
        cache, child = ResultCache(clock=clock), _Child()
        await cache.get_or_call("t", {}, 10, child)
        clock.now += 9.9
        self.assertTrue((await cache.get_or_call("t", {}, 10, child))[1])
        clock.now += 0.2
        result, cached = await cache.get_or_call("t", {}, 10, child)
        self.assertFalse(cached)
        self.assertEqual(_text(result), "ok-2")

    async def test_zero_or_negative_ttl_bypasses_cache(self) -> None:
        cache, child = ResultCache(), _Child()
        for ttl in (0, -5, 0.0, 0, float("nan")):
            _, cached = await cache.get_or_call("t", {}, ttl, child)
            self.assertFalse(cached)
        self.assertEqual(child.calls, 5)
        self.assertEqual(cache.stats(), {"hits": 0, "misses": 0, "entries": 0})

    async def test_unserializable_arguments_bypass_cache(self) -> None:
        cache, child = ResultCache(), _Child()
        await cache.get_or_call("t", {"x": object()}, 60, child)
        _, cached = await cache.get_or_call("t", {"x": object()}, 60, child)
        self.assertFalse(cached)
        self.assertEqual(cache.stats()["entries"], 0)

    async def test_error_results_are_not_cached(self) -> None:
        cache, child = ResultCache(), _Child(is_error=True)
        first, _ = await cache.get_or_call("t", {}, 60, child)
        _, cached = await cache.get_or_call("t", {}, 60, child)
        self.assertTrue(first.is_error)
        self.assertFalse(cached)
        self.assertEqual(child.calls, 2)
        self.assertEqual(cache.stats()["entries"], 0)

    async def test_store_if_refuses_results_it_rejects(self) -> None:
        cache, child = ResultCache(), _Child("Error: upstream down")
        def looks_ok(result: CallToolResult) -> bool:
            return not _text(result).startswith("Error:")
        for _ in range(2):
            _, cached = await cache.get_or_call("t", {}, 60, child, store_if=looks_ok)
            self.assertFalse(cached)
        self.assertEqual(child.calls, 2)
        self.assertEqual(cache.stats()["entries"], 0)

    async def test_store_if_accepting_stores_the_result(self) -> None:
        cache, child = ResultCache(), _Child()
        await cache.get_or_call("t", {}, 60, child, store_if=lambda result: True)
        result, cached = await cache.get_or_call("t", {}, 60, child, store_if=lambda result: True)
        self.assertTrue(cached)
        self.assertEqual((_text(result), child.calls), ("ok-1", 1))

    async def test_exceptions_propagate_and_are_not_cached(self) -> None:
        cache, child = ResultCache(), _Child(error=RuntimeError("down"))
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                await cache.get_or_call("t", {}, 60, child)
        self.assertEqual(child.calls, 2)
        self.assertEqual(cache.stats()["entries"], 0)

    async def test_lru_evicts_oldest_by_entry_count(self) -> None:
        cache, child = ResultCache(max_entries=2), _Child()
        await cache.get_or_call("t", {"n": 1}, 60, child)
        await cache.get_or_call("t", {"n": 2}, 60, child)
        await cache.get_or_call("t", {"n": 1}, 60, child)  # n=1 becomes most recent
        await cache.get_or_call("t", {"n": 3}, 60, child)  # evicts n=2
        self.assertTrue((await cache.get_or_call("t", {"n": 1}, 60, child))[1])
        self.assertFalse((await cache.get_or_call("t", {"n": 2}, 60, child))[1])
        self.assertEqual(cache.stats()["entries"], 2)

    async def test_lru_evicts_by_bytes_and_skips_oversized_results(self) -> None:
        size = len(_result("ok-1").model_dump_json(by_alias=True))
        cache, child = ResultCache(max_bytes=size * 2 + 1), _Child()
        for n in range(3):
            await cache.get_or_call("t", {"n": n}, 60, child)
        self.assertEqual(cache.stats()["entries"], 2)
        self.assertFalse((await cache.get_or_call("t", {"n": 0}, 60, child))[1])
        tiny = ResultCache(max_bytes=size - 1)
        await tiny.get_or_call("t", {}, 60, _Child())
        self.assertEqual(tiny.stats()["entries"], 0)

    async def test_returned_results_are_independent_copies(self) -> None:
        cache, child = ResultCache(), _Child()
        first, _ = await cache.get_or_call("t", {}, 60, child)
        first.content[0].text = "mutated"
        second, _ = await cache.get_or_call("t", {}, 60, child)
        second.content.append(TextContent(type="text", text="extra"))
        third, _ = await cache.get_or_call("t", {}, 60, child)
        self.assertEqual(_text(third), "ok-1")
        self.assertEqual(len(third.content), 1)

    async def test_concurrent_identical_calls_share_one_child_call(self) -> None:
        cache, child = ResultCache(), _Child()
        child.release = asyncio.Event()
        calls = [asyncio.create_task(cache.get_or_call("t", {"q": 1}, 60, child)) for _ in range(3)]
        await asyncio.sleep(0)
        child.release.set()
        results = await asyncio.gather(*calls)
        self.assertEqual(child.calls, 1)
        self.assertEqual([cached for _, cached in results], [False, True, True])
        self.assertEqual({_text(result) for result, _ in results}, {"ok-1"})
        self.assertIsNot(results[0][0], results[1][0])

    async def test_concurrent_waiters_all_get_the_failure(self) -> None:
        cache, child = ResultCache(), _Child(error=RuntimeError("down"))
        child.release = asyncio.Event()
        calls = [asyncio.create_task(cache.get_or_call("t", {}, 60, child)) for _ in range(3)]
        await asyncio.sleep(0)
        child.release.set()
        outcomes = await asyncio.gather(*calls, return_exceptions=True)
        self.assertTrue(all(isinstance(item, RuntimeError) for item in outcomes))
        self.assertEqual(child.calls, 1)
        self.assertEqual(cache.stats()["entries"], 0)
        child.error, child.release = None, None
        self.assertFalse((await cache.get_or_call("t", {}, 60, child))[1])

    async def test_cancelled_leader_does_not_cancel_shared_call(self) -> None:
        cache, child = ResultCache(), _Child()
        child.release = asyncio.Event()
        leader = asyncio.create_task(cache.get_or_call("t", {}, 60, child))
        await asyncio.sleep(0)
        waiter = asyncio.create_task(cache.get_or_call("t", {}, 60, child))
        await asyncio.sleep(0)
        leader.cancel()
        child.release.set()
        result, cached = await waiter
        self.assertEqual((_text(result), cached), ("ok-1", True))
        with self.assertRaises(asyncio.CancelledError):
            await leader

    async def test_invalidate_drops_only_named_tools(self) -> None:
        cache, child = ResultCache(), _Child()
        await cache.get_or_call("t", {"n": 1}, 60, child)
        await cache.get_or_call("t", {"n": 2}, 60, child)
        await cache.get_or_call("t2", {"n": 1}, 60, child)
        self.assertEqual(cache.invalidate(["t"]), 2)
        self.assertFalse((await cache.get_or_call("t", {"n": 1}, 60, child))[1])
        self.assertTrue((await cache.get_or_call("t2", {"n": 1}, 60, child))[1])
        self.assertEqual(cache.invalidate(), 2)
        self.assertEqual(cache.stats()["entries"], 0)

    async def test_call_in_flight_during_invalidate_is_not_stored_or_joined(self) -> None:
        cache, child = ResultCache(), _Child()
        child.release = asyncio.Event()
        before = asyncio.create_task(cache.get_or_call("t", {}, 60, child))
        await asyncio.sleep(0)
        cache.invalidate(["t"])
        after = asyncio.create_task(cache.get_or_call("t", {}, 60, child))
        await asyncio.sleep(0)
        child.release.set()
        (first, _), (second, second_cached) = await asyncio.gather(before, after)
        self.assertEqual(child.calls, 2)
        self.assertFalse(second_cached)
        self.assertNotEqual(_text(first), _text(second))
        self.assertEqual(_text((await cache.get_or_call("t", {}, 60, child))[0]), _text(second))


if __name__ == "__main__":
    unittest.main()
