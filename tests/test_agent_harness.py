from __future__ import annotations

import unittest
from types import SimpleNamespace

from mcp.types import CallToolResult, TextContent

from mcp_hub.agents.harness import ContextBudget, Exchange
from mcp_hub.agents.providers import ModelProvider, ModelToolCall, ModelTurn
from mcp_hub.agents.specialists import SpecialistExecutor


def _exchanges(count: int, size: int) -> list[Exchange]:
    return [
        Exchange(
            tool_call_id=f"call-{index}",
            source_id=f"logs-{index:08x}",
            text=f"row {index}\n" + "x" * size,
        )
        for index in range(count)
    ]


def _total(exchanges: list[Exchange]) -> int:
    return sum(len(exchange.text) for exchange in exchanges)


class ContextBudgetTest(unittest.TestCase):
    def test_transcript_over_the_budget_is_brought_under_it(self) -> None:
        compacted = ContextBudget(max_chars=3000).compact(_exchanges(10, 1000))
        self.assertLessEqual(_total(compacted), 3000)

    def test_every_source_and_call_id_survives(self) -> None:
        exchanges = _exchanges(10, 1000)
        compacted = ContextBudget(max_chars=500).compact(exchanges)
        self.assertEqual(len(compacted), len(exchanges))
        self.assertEqual([e.source_id for e in compacted], [e.source_id for e in exchanges])
        self.assertEqual([e.tool_call_id for e in compacted], [e.tool_call_id for e in exchanges])

    def test_last_exchanges_stay_verbatim_when_the_budget_allows(self) -> None:
        exchanges = _exchanges(10, 1000)
        compacted = ContextBudget(max_chars=5000, keep_last_rounds=3).compact(exchanges)
        self.assertEqual([e.text for e in compacted[-3:]], [e.text for e in exchanges[-3:]])
        for older in compacted[:-3]:
            self.assertTrue(older.text.startswith("[cleared; evidence "), older.text)

    def test_cleared_note_keeps_the_source_id_and_first_line(self) -> None:
        exchanges = [
            Exchange("call-0", "logs-abc", "first line\nsecond line"),
            Exchange("call-1", "logs-def", "last result"),
        ]
        compacted = ContextBudget(max_chars=10000, keep_last_rounds=1).compact(exchanges)
        self.assertEqual(compacted[0].text, "[cleared; evidence logs-abc: first line]")
        self.assertEqual(compacted[1].text, "last result")

    def test_cleared_first_line_is_cut_for_one_long_line(self) -> None:
        exchanges = [Exchange("call-0", "logs-abc", "z" * 5000)]
        compacted = ContextBudget(max_chars=10000, keep_last_rounds=0).compact(exchanges)
        self.assertEqual(compacted[0].text, "[cleared; evidence logs-abc: " + "z" * 120 + "]")

    def test_result_without_evidence_is_cleared_without_a_source(self) -> None:
        exchanges = [Exchange("call-0", "", "Error: bad arguments")]
        compacted = ContextBudget(max_chars=10000, keep_last_rounds=0).compact(exchanges)
        self.assertEqual(compacted[0].text, "[cleared: Error: bad arguments]")
        self.assertEqual(compacted[0].source_id, "")

    def test_input_list_is_not_changed(self) -> None:
        exchanges = _exchanges(6, 800)
        before = [e.text for e in exchanges]
        ContextBudget(max_chars=1000).compact(exchanges)
        self.assertEqual([e.text for e in exchanges], before)

    def test_budget_too_small_for_the_notes_is_best_effort(self) -> None:
        exchanges = _exchanges(4, 300)
        compacted = ContextBudget(max_chars=0).compact(exchanges)
        self.assertEqual(len(compacted), 4)
        self.assertTrue(all(e.text.startswith("[cleared; evidence ") for e in compacted))

    def test_empty_transcript_stays_empty(self) -> None:
        self.assertEqual(ContextBudget(max_chars=10).compact([]), [])


class _RecordingProvider(ModelProvider):
    """Returns scripted turns and records the size of each transcript it is sent."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.sizes: list[int] = []
        self.transcripts: list[list[dict]] = []

    async def complete(self, messages: list[dict], tools: list, *, max_output_tokens: int = 2048) -> ModelTurn:
        self.transcripts.append(list(messages))
        self.sizes.append(sum(len(str(message.get("content") or "")) for message in messages))
        return self.turns.pop(0)


def _query_turns(count: int) -> list[ModelTurn]:
    turns = [
        ModelTurn(text="", tool_calls=(ModelToolCall(f"call-{index}", "logs__query", {"n": index}),))
        for index in range(count)
    ]
    return [*turns, ModelTurn(text="All checked", tool_calls=())]


def _logs_runtime() -> SimpleNamespace:
    tool = SimpleNamespace(name="logs__query", annotations=SimpleNamespace(read_only_hint=True))
    return SimpleNamespace(public_tools=[tool], audit=None)


class SpecialistContextBudgetTest(unittest.IsolatedAsyncioTestCase):
    async def test_transcript_stays_within_context_chars_over_ten_rounds(self) -> None:
        provider = _RecordingProvider(_query_turns(10))

        async def fake_call(job_id, role, name, arguments, tool, source_id) -> CallToolResult:
            text = f"row {arguments['n']}\n" + "x" * 1000
            return CallToolResult(content=[TextContent(type="text", text=text)], is_error=False)

        executor = SpecialistExecutor(
            runtime=_logs_runtime(), provider=provider, save_result=lambda *args: None, call_tool=fake_call,
            max_rounds=11, context_chars=3000,
        )
        result = await executor.run("job-1", "logs", "Analyze logs")
        self.assertEqual(result.summary, "All checked")
        self.assertEqual(len(provider.sizes), 11)
        self.assertLessEqual(max(provider.sizes), 3000, provider.sizes)
        first_ref = result.evidence_refs[0].source_id
        self.assertIn(f"[cleared; evidence {first_ref}:", str(provider.transcripts[-1]))
        self.assertEqual(len(result.evidence_refs), 10)


if __name__ == "__main__":
    unittest.main()
