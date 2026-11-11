from __future__ import annotations

import asyncio
import time
from typing import Callable

from ..condense import (
    _ERROR_MARKER_RE,
    compact_json_payload,
    format_capped as _format_capped,
    group_lines,
    head_tail_preview,
    template_of,
)
from ..extensions import Condensed, ResultCondenser
from ..plan_merge import fold
from .client import BitnetClient

__all__ = ["BitnetCondenser", "group_lines", "template_of", "_format_capped"]

_SYSTEM_PROMPT = (
    'Return JSON {"keep": [line numbers]} listing only lines relevant to the task. '
    'At most 40 numbers. No prose.'
)


def _chunk_numbered_lines(lines: list[str], max_chars: int = 10000) -> list[list[tuple[int, str]]]:
    chunks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    current_len = 0
    for idx, line in enumerate(lines, 1):
        line_repr = f"{idx}: {line}\n"
        if current and (current_len + len(line_repr) > max_chars):
            chunks.append(current)
            current = []
            current_len = 0
        current.append((idx, line))
        current_len += len(line_repr)
    if current:
        chunks.append(current)
    return chunks


class BitnetCondenser(ResultCondenser):
    def __init__(
        self,
        client: BitnetClient,
        *,
        max_chars: int = 9000,
        timeout: float = 8.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.max_chars = max_chars
        self.timeout = timeout
        self.clock = clock

    async def condense(
        self, text: str, task: str | None = None, *, max_chars: int | None = None,
    ) -> Condensed | None:
        budget = self.max_chars if max_chars is None else max_chars
        try:
            return await self._condense(text, task, budget)
        except Exception:
            return None

    async def _condense(self, text: str, task: str | None, budget: int) -> Condensed:
        compacted = await asyncio.to_thread(compact_json_payload, text, budget, task)
        if compacted is not None:
            compact_text, lossless = compacted
            return Condensed(compact_text, 1, 1, "rules", lossless=lossless)
        lines, total = await asyncio.to_thread(group_lines, text)
        joined = "\n".join(lines)
        if len(joined) <= budget or len(lines) < 5 or not task or not task.strip():
            capped = head_tail_preview(lines[0], budget) if len(lines) == 1 and len(lines[0]) > budget else _format_capped(lines, budget)
            return Condensed(capped, len(capped.splitlines()), total, "rules")
        return await self._condense_with_model(lines, total, task, budget)

    async def _condense_with_model(self, lines: list[str], total: int, task: str, budget: int) -> Condensed:
        selected, bitnet_answered = await self._select_lines(lines, task)
        for idx, line in enumerate(lines, 1):
            if _ERROR_MARKER_RE.search(fold(line)):
                selected.add(idx)
        if not selected:
            capped = _format_capped(lines, budget)
            return Condensed(capped, len(capped.splitlines()), total, "rules")
        final_lines = [lines[idx - 1] for idx in range(1, len(lines) + 1) if idx in selected]
        capped_output = _format_capped(final_lines, budget)
        return Condensed(capped_output, len(capped_output.splitlines()), total, "bitnet" if bitnet_answered else "rules")

    async def _select_lines(self, lines: list[str], task: str) -> tuple[set[int], bool]:
        start = self.clock()
        selected: set[int] = set()
        answered = False
        for chunk in _chunk_numbered_lines(lines, max_chars=10000):
            budget = self.timeout - (self.clock() - start)
            if budget <= 0:
                break
            reply = await self.client.complete_json(
                _SYSTEM_PROMPT, f"Task: {task}\n\n" + "\n".join(f"{idx}: {line}" for idx, line in chunk),
                max_tokens=200, timeout=budget,
            )
            if reply and isinstance(reply.get("keep"), list):
                answered = True
                valid_ids = {idx for idx, _ in chunk}
                for num in reply["keep"]:
                    if isinstance(num, int) and num in valid_ids and len(selected) < 40:
                        selected.add(num)
        return selected, answered
