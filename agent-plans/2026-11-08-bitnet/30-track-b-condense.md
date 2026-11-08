# Track B: result condenser

Status: approved · Branch: `feat/bitnet-condense` (worktree `../mcp-hub-b`, from `master` after track 0 is merged) · Parallel with A and C · Read [00-overview.md](00-overview.md) first.

Goal: implement `ResultCondenser` (overview 7.3). Condensing is extractive only: step A is deterministic, and step B has BitNet pick line numbers.

## Owns

- `src/mcp_hub/bitnet/condense.py` (new)
- `tests/test_bitnet_condense.py` (new)

Use, but do not change: `mcp_hub.extensions.Condensed`, `mcp_hub.bitnet.client.BitnetClient`.

## Tasks

### [ ] T201 Step A: deterministic dedupe and grouping
```python
def template_of(line: str) -> str:
    """Replace volatile parts with placeholders: ISO/epoch timestamps -> <ts>, GUIDs -> <id>, hex >= 8 -> <hex>,
    numbers -> <n>, quoted strings longer than 40 chars -> <str>."""

def group_lines(text: str) -> tuple[list[str], int]:
    """Return (representative lines, total line count). Keep the first occurrence of each template, prefixed with
    '[xN] ' when the template occurs N > 1 times, in first-seen order. Lines with error markers
    (error|exception|fail|fatal|timeout|loi, matched on the line folded to ASCII: casefold + strip diacritics) sort before the others, keeping relative order."""
```
- Pre-compile the regexes as module constants. The function is pure: no I/O, no clock.
- Skip text with fewer than 50 lines: `group_lines` returns the lines unchanged.
- Tests:
  - 5,000 lines with 20 templates give 20 lines, each with the right `[xN]`;
  - error lines come first;
  - GUID and timestamp variants collapse to one template;
  - fewer than 50 lines are returned unchanged.

### [ ] T202 Step B: BitNet picks relevant lines
```python
class BitnetCondenser:  # implements extensions.ResultCondenser
    def __init__(self, client: BitnetClient, *, max_chars: int, timeout: float) -> None: ...
    async def condense(self, text: str, task: str None) -> Condensed None: ...
```
1. Run step A in a thread: `lines, total = await asyncio.to_thread(group_lines, text)`.
2. If `"\n".join(lines)` is at most `max_chars`, or `task` is empty, return `Condensed(joined, len(lines), total, "rules")`.
3. Otherwise:
   - split the lines into chunks of at most 10,000 characters, numbering the lines globally from 1;
   - send each chunk, sequentially, with the system message: "Return JSON {\"keep\": [line numbers]} listing only lines relevant to the task. At most 40 numbers. No prose.";
   - the user message is `Task: <task>\n\n<numbered lines>`;
   - use `max_tokens=200`, with a per-call timeout of the remaining budget, where the budget is `self.timeout` measured with `time.monotonic()`.
4. Collect the valid integers that are in range. Always also keep every error-marker line from step A, up to the `max_chars` cap.
5. Build the output in the original order. If it is still longer than `max_chars`, cut at a line boundary and add the line `[… N more lines omitted]`.
6. If the budget runs out, or every BitNet call failed, return the step A output cut to `max_chars` with `source="rules"`. Return `source="bitnet"` only when at least one chunk answered.
7. Never raise. On an unexpected exception, return `None`; the core then returns the original.
- Tests (fake client, fake monotonic clock):
  - a short result after step A → no client call, `source="rules"`;
  - the model keeps lines 3 and 7 → output has those lines plus the error lines, in order;
  - out-of-range numbers are ignored;
  - a client that always returns `None` → step A output, `source="rules"`;
  - the budget is exhausted after the first chunk → no further calls;
  - the output never exceeds `max_chars` plus the omission line;
  - an exception gives `None`.

### [ ] T203 Quality pass
- Each function is at most 50 lines; coverage is at least 90 %; `package_qc.py` passes.
- SC-003 holds (a test in T201 or T202 asserts it).

## Acceptance for track B
- No module outside `src/mcp_hub/bitnet/` was changed.
- The condenser output is always a subset of the input lines, apart from the `[xN] ` prefixes and the omission line.

## Blocked

- (none)

## Progress log

- 2026-11-08 Claude (Opus): track written. Next: T201.
