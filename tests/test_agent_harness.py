from __future__ import annotations

import unittest

from mcp_hub.agents.harness import ContextBudget, Exchange


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


if __name__ == "__main__":
    unittest.main()
