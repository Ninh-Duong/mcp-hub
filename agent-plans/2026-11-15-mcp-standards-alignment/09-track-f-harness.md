# Track F: investigation harness and instructions

Status: ready
Branch: `arch/track-f` (from `arch/base`). Worktree: `../mcp-hub-track-f` (sibling of the Hub checkout).

Owns:
- `src/mcp_hub/instructions.py` (new)
- `src/mcp_hub/agents/` (all files, including the new `harness.py`)
- `src/mcp_hub/jobs/runner.py`
- `tests/test_instructions.py` (new), `tests/test_agent_harness.py` (new)
- additions to existing `tests/test_*agent*.py` and `tests/test_job*.py` files

Do not edit `server.py`, `service.py`, `docs/` (Phase 2 owns the docs for this track's wiring), or `settings.py`.

Read first: `src/mcp_hub/agents/specialists.py` (`MAX_TOOL_RESULT_CHARS` around line 21; `max_rounds` around line 157), `src/mcp_hub/agents/providers.py`, `src/mcp_hub/jobs/runner.py` (`COORDINATOR_SECONDS` around line 37).

## Task checklist

- [x] T60 `build_instructions` (commit on `arch/track-f`)
- [x] T61 `ContextBudget`
- [ ] T62 use in the specialist loop
- [ ] T63 settings values, not constants
- [ ] T64 prompt caching

## T60 `build_instructions`

File: `src/mcp_hub/instructions.py`. Test: `tests/test_instructions.py`.

```python
def build_instructions(summary: str, sections: Sequence[tuple[str, Sequence[str]]], budget: int = 4096) -> str:
```

- The summary comes first; if it is longer than `budget`, cut it to `budget` characters.
- Each section is `(title, lines)`. Its title and then its lines are added while the total stays within `budget` (count one newline per line).
- A section that stops early gets the line `"+N more; see tools/list"`, where N is the number of lines left out. The note itself must fit in the budget; if it does not, drop the last line added to make room.
- Sections after the one that stops early are not added.

Tests: total length never exceeds the budget; sections keep their order; a long list is truncated with the `+N more` note; a summary longer than the budget is cut.

## T61 `ContextBudget`

File: `src/mcp_hub/agents/harness.py`. Test: `tests/test_agent_harness.py`.

```python
@dataclass
class Exchange:            # one tool call and its result, provider-neutral
    tool_call_id: str
    source_id: str         # the evidence id the Hub already returns for the stored result
    text: str

class ContextBudget:
    def __init__(self, max_chars: int, keep_last_rounds: int = 3) -> None: ...
    def compact(self, exchanges: list[Exchange]) -> list[Exchange]: ...
```

`compact` keeps the last `keep_last_rounds` exchanges verbatim. Older exchanges keep their `tool_call_id` and `source_id`, and their `text` becomes `"[cleared; evidence <source_id>: <first line of the original text>]"`. If the total still exceeds `max_chars` after that, it keeps dropping the text of the oldest remaining verbatim exchanges, in order, until it fits. It never removes an exchange or changes a `source_id`.

Tests: a transcript over the budget is brought under it; every `source_id` and `tool_call_id` survives; the last `keep_last_rounds` are unchanged.

## T62 use in the specialist loop

In `agents/specialists.py`, keep one `Exchange` per tool result, run `compact` before each model call, and rebuild that provider's message list from the compacted exchanges using the existing message-building code.

Do not change the wire format of any provider adapter. If rebuilding the messages needs a change to a provider adapter's wire format, stop and report (see stop rules).

Test: a fake provider records the message sizes; over ten rounds, the transcript stays within `jobs.contextChars` (use a small budget in the test).

## T63 settings values, not constants

- `MAX_TOOL_RESULT_CHARS` in `agents/specialists.py` becomes a value passed to the specialist object. Its constructor parameter defaults to `JobSettings().tool_result_chars`.
- `max_rounds` becomes `JobSettings().specialist_max_rounds` by the same pattern.
- `COORDINATOR_SECONDS` in `jobs/runner.py` becomes `JobSettings().coordinator_seconds` by the same pattern.

The settings module is the only place the default values are written. Keep the old module-level names as aliases of the defaults, so existing tests import the same names.

Test: a specialist built with `tool_result_chars=100` truncates a 1000-character result to 100 characters of text.

## T64 prompt caching (uses a skill)

Before editing `agents/providers.py`, invoke the `claude-api` skill and follow its current guidance for Anthropic `cache_control` and the minimum cacheable prefix for each model. Record the skill's guidance in the progress log in one line.

- Anthropic: put `cache_control` on the stable prefix (tool definitions, then the role prompt). Only when `JobSettings().prompt_cache` is true.
- OpenAI: keep the prefix byte-stable (sorted tools, no timestamps); the provider caches it automatically.
- Gemini: no change in this plan; record it as not done.

Tests: with `prompt_cache` false, the Anthropic request has no `cache_control`; with true, it has it on the prefix. Stable prefix: two builds with different job ids give the same tool block bytes.

## Verify

```
cd "../mcp-hub-track-f"
uv run --no-sync python -m unittest tests.test_instructions tests.test_agent_harness -v
uv run --no-sync python -m unittest discover tests -p "test_*agent*.py" -q
uv run --no-sync python -m unittest discover tests -p "test_job*.py" -q
uv run --no-sync python -m ruff check .
uv run --no-sync python scripts/check_code_standards.py
uv run --no-sync python scripts/package_qc.py
```

## Stop and ask

- Rebuilding messages from exchanges needs a provider wire-format change (T62).
- The `claude-api` skill is not available, or it contradicts the code.
- A test that asserts the exact old prompt text fails. Report which test; do not rewrite it.

## Progress log

- (agent: add one line per task here)
- 2026-11-15 (track F): T60 done. `instructions.py` and `tests/test_instructions.py` (9 tests); ruff and code standards pass.
- 2026-11-15 (track F): T61 done. `agents/harness.py` (`Exchange`, `ContextBudget`) and `tests/test_agent_harness.py` (9 tests). Cleared text cuts the first line to 120 characters; a result with no evidence (a failed call) clears to `[cleared: ...]` with no source ID. Ruff and code standards pass.
