from __future__ import annotations

from dataclasses import dataclass, replace


# A cleared result keeps this many characters of its first line in the note.
CLEARED_LINE_CHARS = 120


@dataclass
class Exchange:
    """One tool call and its result, provider-neutral."""

    tool_call_id: str
    source_id: str  # the evidence ID the Hub already returns for the stored result; empty when there is none
    text: str


class ContextBudget:
    """Keeps a specialist's tool results within a character budget. Evidence IDs are never dropped."""

    def __init__(self, max_chars: int, keep_last_rounds: int = 3) -> None:
        self.max_chars = max_chars
        self.keep_last_rounds = keep_last_rounds

    def compact(self, exchanges: list[Exchange]) -> list[Exchange]:
        """Return copies in which older results are cleared, oldest first, until the text fits.

        The last `keep_last_rounds` exchanges stay verbatim while the budget allows. If it still does not
        fit, the oldest verbatim text goes next; a clear that would not shorten the text is skipped.
        """
        result = [replace(exchange) for exchange in exchanges]
        for exchange in result[:max(0, len(result) - self.keep_last_rounds)]:
            exchange.text = _cleared(exchange)
        overflow = _total(result) - self.max_chars
        for original, copy in zip(exchanges, result):
            if overflow <= 0:
                break
            cleared = _cleared(original)
            saved = len(copy.text) - len(cleared)
            if saved > 0:
                copy.text = cleared
                overflow -= saved
        return result


def _total(exchanges: list[Exchange]) -> int:
    return sum(len(exchange.text) for exchange in exchanges)


def _cleared(exchange: Exchange) -> str:
    first_line = exchange.text.split("\n", 1)[0][:CLEARED_LINE_CHARS]
    if not exchange.source_id:
        return f"[cleared: {first_line}]"
    return f"[cleared; evidence {exchange.source_id}: {first_line}]"
