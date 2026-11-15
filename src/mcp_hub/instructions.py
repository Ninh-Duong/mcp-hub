from __future__ import annotations

from collections.abc import Sequence


# Replaces the lines of a section that did not fit; N is the number of lines left out.
MORE_NOTE = "+{count} more; see tools/list"


def build_instructions(summary: str, sections: Sequence[tuple[str, Sequence[str]]], budget: int = 4096) -> str:
    """Join the summary and the sections into text of at most `budget` characters.

    Every line after the first costs one character for its newline. A section whose lines do not all
    fit keeps the lines that do, then gets the "+N more" note; the sections after it are left out.
    """
    lines: list[str] = []
    head = summary[:budget]
    if head:
        lines.append(head)
    for title, items in sections:
        if not _add_section(lines, budget, title, items):
            break
    return "\n".join(lines)


def _fits(lines: list[str], line: str, budget: int) -> bool:
    return len("\n".join([*lines, line])) <= budget


def _add_section(lines: list[str], budget: int, title: str, items: Sequence[str]) -> bool:
    """Add one section; return False when it stopped early (or never started), so later sections are skipped."""
    start = len(lines)
    if not _fits(lines, title, budget):
        return False
    lines.append(title)
    for item in items:
        if not _fits(lines, item, budget):
            _add_more_note(lines, budget, start, len(items))
            return False
        lines.append(item)
    return True


def _add_more_note(lines: list[str], budget: int, start: int, total: int) -> None:
    """Append the note for a section that starts at `start`; drop its last line until the note fits."""
    while len(lines) > start:
        shown = len(lines) - start - 1
        note = MORE_NOTE.format(count=total - shown)
        if _fits(lines, note, budget):
            lines.append(note)
            return
        lines.pop()
