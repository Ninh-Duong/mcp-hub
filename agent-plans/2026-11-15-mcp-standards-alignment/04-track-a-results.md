# Track A: results

Status: ready
Branch: `arch/track-a` (from `arch/base`). Worktree: `../mcp-hub-track-a`, which means the sibling directory of the Hub checkout named `mcp-hub-track-a`.

Owns (only these files):
- `src/mcp_hub/result_shape.py` (new)
- `src/mcp_hub/full_results.py`
- `tests/test_result_shape.py` (new)
- `tests/test_full_results.py` (additions only)

Read first: `src/mcp_hub/full_results.py`; `src/mcp_hub/server.py` functions `_get_full_result`, `_fetch_one_result` (around lines 480-505, for the `offset`/`limit` defaults), and `_maybe_condense` (around line 513). Do not edit `server.py`.

## T10 `is_condensable` (done)

File: `src/mcp_hub/result_shape.py`. Test file: `tests/test_result_shape.py`.

```python
def is_condensable(output_schema: Mapping[str, Any] | None, result: CallToolResult) -> bool:
```

Returns False when any of these holds: `output_schema` is truthy; `result.structured_content is not None`; any item in `result.content` is not a `TextContent`; `result.is_error` is true. Otherwise True.

Tests: one case per False rule, and one True case. Types come from `mcp.types`.

## T11 `with_condensed_meta` (done)

```python
def with_condensed_meta(result: CallToolResult, result_id: str, kept: int, total: int) -> CallToolResult:
```

Returns `result.model_copy(update=...)` with `content` unchanged and `meta` set to the old meta merged with `{"mcp-hub/condensed": {"result_id": result_id, "kept": kept, "total": total}}`. The input is not mutated.

Tests: content is identical; old meta keys survive; the input object is unchanged.

## T12 `parse_page_args` (done)

```python
def parse_page_args(arguments: Mapping[str, Any], *, default_offset: int, default_limit: int) -> tuple[int, int] | str:
```

Takes the defaults from the current code in `server.py` (verify the values there; pass them in, do not hard-code new ones). A missing key uses its default. A value is accepted when it is an `int` (not a `bool`) or a string of digits. `offset` must be >= 0 and `limit` >= 1. Anything else returns an error string that names the field, for example `"limit must be a whole number"`.

Tests: list, dict, letters, a negative offset, zero limit, `True`, digit strings, and missing keys.

## T13 `FullResultStore` byte cap (done)

File: `src/mcp_hub/full_results.py`. Test file: `tests/test_full_results.py`.

Add a keyword argument `max_bytes: int | None = None` to `FullResultStore.__init__`. `None` keeps today's behaviour exactly. When set, track the total UTF-8 bytes of the stored original text and evict the oldest entries until the total fits. An entry that alone is larger than `max_bytes` is not stored, and the put returns the same "not stored" value the store already uses for refused entries (read the file to find it).

Tests: existing tests pass unchanged; a fuzz-style test with 300 random-size entries asserts the total never exceeds `max_bytes`; a single oversized entry is not stored.

## Verify

```
cd "../mcp-hub-track-a"
uv run --no-sync python -m unittest tests.test_result_shape tests.test_full_results -v
uv run --no-sync python -m ruff check .
uv run --no-sync python scripts/check_code_standards.py
uv run --no-sync python scripts/package_qc.py
```

Start every shell call with `cd "../mcp-hub-track-<x>"` (relative to the Hub checkout root, where the shell starts); `cd` does not persist between calls.

## Stop and ask

- `server.py` default values differ from what this card assumes.
- `FullResultStore.put` has no clear "not stored" return value.

## Progress log

- T10 done: `is_condensable` in `result_shape.py` with 5 tests in `tests/test_result_shape.py`; ruff and code standards pass.
- T11 done: `with_condensed_meta` in `result_shape.py`, 3 more tests (8 in the file); ruff and code standards pass.
- T12 done: `parse_page_args` in `result_shape.py`, 10 more tests (18 in the file); defaults checked against `server.py` (offset 0, limit 20000, server-side max 50000 stays in server); ruff and code standards pass.
- T13 BLOCKED (stop rule, not started): `FullResultStore.put` always stores and returns a new ID; neither `full_results.py`, its tests, nor `server.py` has a "not stored" value for refused entries. The card does not state what `put` returns for an entry larger than `max_bytes`. Decision needed: the value to return (for example `None`, which changes the return type to `str | None` and touches the caller `server.py` `_maybe_condense`, or raising `ValueError`). T13 stays unticked until the orchestrator decides.
- T13 decision (orchestrator): `FullResultStore.put` returns `str | None`. It returns None only when the single entry is larger than `max_bytes`; nothing is stored and nothing is evicted. Otherwise it evicts the oldest entries until the total UTF-8 bytes fit and returns the new `r-<hex>` ID as before. `max_bytes` defaults to None, so the caller in `server.py` (`_maybe_condense`) never gets None until Phase 2 wires the setting. The `put` signature changes only in its return annotation.
- T13 done: `max_bytes` keyword on `FullResultStore.__init__`, with byte tracking and oldest-first eviction in `full_results.py`; 4 tests added in `tests/test_full_results.py` (oldest-first eviction, UTF-8 byte counting, oversized returns None and evicts nothing, and a seeded 300-entry fuzz test that never exceeds `max_bytes`); the 6 existing tests are unchanged; ruff and code standards pass.
