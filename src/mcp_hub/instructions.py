from __future__ import annotations

from collections.abc import Sequence


# Replaces the lines of the tool list section that did not fit; N is the number of lines left out.
MORE_NOTE = "+{count} more; see tools/list"
# The same note for any other section: it names the file that holds the section's full text.
SOURCE_NOTE = "+{count} more lines in {source}"
# A section is its title, its lines, and the file its full text lives in; without a file it is the tool list.
Section = tuple[str, Sequence[str]] | tuple[str, Sequence[str], str]


def build_instructions(summary: str, sections: Sequence[Section], budget: int = 4096) -> str:
    """Join the summary and the sections into text of at most `budget` characters.

    Every line after the first costs one character for its newline. A section whose lines do not all
    fit keeps the lines that do, then gets its "+N more" note; the sections after it are left out.
    """
    lines: list[str] = []
    head = summary[:budget]
    if head:
        lines.append(head)
    for section in sections:
        if not _add_section(lines, budget, section):
            break
    return "\n".join(lines)


def _fits(lines: list[str], line: str, budget: int) -> bool:
    return len("\n".join([*lines, line])) <= budget


def _add_section(lines: list[str], budget: int, section: Section) -> bool:
    """Add one section; return False when it stopped early (or never started), so later sections are skipped."""
    title, items, *extra = section
    source = extra[0] if extra else None
    start = len(lines)
    if not _fits(lines, title, budget):
        return False
    lines.append(title)
    for item in items:
        if not _fits(lines, item, budget):
            _add_more_note(lines, budget, start, len(items), source)
            return False
        lines.append(item)
    return True


def _add_more_note(lines: list[str], budget: int, start: int, total: int, source: str | None) -> None:
    """Append the note for a section that starts at `start`; drop its last line until the note fits."""
    while len(lines) > start:
        shown = len(lines) - start - 1
        note = _more_note(total - shown, source)
        if _fits(lines, note, budget):
            lines.append(note)
            return
        lines.pop()


def _more_note(count: int, source: str | None) -> str:
    if source is None:
        return MORE_NOTE.format(count=count)
    return SOURCE_NOTE.format(count=count, source=source)
