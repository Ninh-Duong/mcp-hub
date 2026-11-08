from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable

from ..extensions import Condensed, ResultCondenser
from .client import BitnetClient
from .rules import fold


_TS_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b")
_GUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX_RE = re.compile(r"\b(?:0x)?[0-9a-fA-F]{8,}\b")
_NUM_RE = re.compile(r"\b\d+\b")
_LONG_STR_RE = re.compile(r'(["\'])(?:\\.|[^\\]){41,}\1')
_ERROR_MARKER_RE = re.compile(r"\b(error|exception|fail|fatal|timeout|loi)\b")
_SYSTEM_PROMPT = (
    'Return JSON {"keep": [line numbers]} listing only lines relevant to the task. '
    'At most 40 numbers. No prose.'
)


def template_of(line: str) -> str:
    """Replace volatile parts with placeholders."""
    line = _TS_RE.sub("<ts>", line)
    line = _GUID_RE.sub("<id>", line)
    line = _HEX_RE.sub("<hex>", line)
    line = _LONG_STR_RE.sub("<str>", line)
    return _NUM_RE.sub("<n>", line)


def group_lines(text: str) -> tuple[list[str], int]:
    """Group lines by template; error lines sort before others."""
    lines = text.splitlines()
    total = len(lines)
    if total < 50:
        return lines, total

    counts: dict[str, int] = {}
    first_seen: dict[str, str] = {}
    for line in lines:
        tmpl = template_of(line)
        if tmpl not in first_seen:
            first_seen[tmpl] = line
            counts[tmpl] = 1
        else:
            counts[tmpl] += 1

    formatted: list[str] = []
    for tmpl, line in first_seen.items():
        n = counts[tmpl]
        formatted.append(f"[x{n}] {line}" if n > 1 else line)

    errors = [line for line in formatted if _ERROR_MARKER_RE.search(fold(line))]
    others = [line for line in formatted if not _ERROR_MARKER_RE.search(fold(line))]
    return [*errors, *others], total


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


def _format_capped(lines: list[str], max_chars: int) -> str:
    kept: list[str] = []
    current_len = 0
    for i, line in enumerate(lines):
        line_len = len(line) + (1 if kept else 0)
        if current_len + line_len > max_chars:
            omitted = len(lines) - i
            omission_msg = f"[... {omitted} more lines omitted]"
            return "\n".join(kept) + (f"\n{omission_msg}" if kept else omission_msg)
        kept.append(line)
        current_len += line_len
    return "\n".join(kept)


class BitnetCondenser(ResultCondenser):
    def __init__(
        self,
        client: BitnetClient,
        *,
        max_chars: int = 6000,
        timeout: float = 8.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.max_chars = max_chars
        self.timeout = timeout
        self.clock = clock

    async def condense(self, text: str, task: str | None = None, *args: Any, **kwargs: Any) -> Condensed | None:
        try:
            return await self._condense(text, task)
        except Exception:
            return None

    async def _condense(self, text: str, task: str | None) -> Condensed:
        lines, total = await asyncio.to_thread(group_lines, text)
        joined = "\n".join(lines)
        if len(joined) <= self.max_chars or not task or not task.strip():
            capped = _format_capped(lines, self.max_chars)
            return Condensed(capped, len(capped.splitlines()), total, "rules")

        start = self.clock()
        chunks = _chunk_numbered_lines(lines, max_chars=10000)
        selected_indices: set[int] = set()
        bitnet_answered = False

        for chunk in chunks:
            budget = self.timeout - (self.clock() - start)
            if budget <= 0:
                break
            chunk_text = "\n".join(f"{idx}: {l}" for idx, l in chunk)
            user_msg = f"Task: {task}\n\n{chunk_text}"
            reply = await self.client.complete_json(_SYSTEM_PROMPT, user_msg, max_tokens=200, timeout=budget)
            if reply and isinstance(reply.get("keep"), list):
                bitnet_answered = True
                valid_ids = {idx for idx, _ in chunk}
                for num in reply["keep"]:
                    if isinstance(num, int) and num in valid_ids and len(selected_indices) < 40:
                        selected_indices.add(num)

        # Always keep error lines from step A
        for idx, line in enumerate(lines, 1):
            if _ERROR_MARKER_RE.search(fold(line)):
                selected_indices.add(idx)

        if not selected_indices:
            capped = _format_capped(lines, self.max_chars)
            return Condensed(capped, len(capped.splitlines()), total, "rules")

        final_lines = [lines[idx - 1] for idx in range(1, len(lines) + 1) if idx in selected_indices]
        capped_output = _format_capped(final_lines, self.max_chars)
        src = "bitnet" if bitnet_answered else "rules"
        return Condensed(capped_output, len(capped_output.splitlines()), total, src)
