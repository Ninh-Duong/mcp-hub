# Track D: schema rewrite and policy

Status: ready
Branch: `arch/track-d` (from `arch/base`). Worktree: `../mcp-hub-track-d` (sibling of the Hub checkout).

Owns:
- `src/mcp_hub/schema_rewrite.py` (new)
- `src/mcp_hub/policy.py`
- `tests/test_schema_rewrite.py` (new)
- `tests/test_policy.py` (additions only; existing tests must pass unchanged)

Read first: `src/mcp_hub/policy.py` (`PLAN_ID_ARG`, `_PLAN_ID_SCHEMA` and `with_plan_id` around line 162; `token_hex(6)` around line 189; the repeat refusal around line 321). Do not edit `server.py`.

## [x] T40 `schema_rewrite.with_plan_id`

File: `src/mcp_hub/schema_rewrite.py`. Test: `tests/test_schema_rewrite.py`.

Move `PLAN_ID_ARG` and `_PLAN_ID_SCHEMA` here (keep the names). Then:

```python
def with_plan_id(schema: Mapping[str, Any] | None) -> dict[str, Any]:
```

Rules:
- Flat root (no `allOf`, `anyOf`, `oneOf` or `$ref` at the root): copy the schema and add `PLAN_ID_ARG` to `properties`. Same result as today.
- Composed root, not closed (no `additionalProperties: false`): return `{"type": "object", "properties": {PLAN_ID_ARG: _PLAN_ID_SCHEMA}, "allOf": [original]}`.
- Composed root, closed: the inner copy must not forbid the extra key, so the outer schema takes the closure. Return `{"type": "object", "properties": {**inner_properties, PLAN_ID_ARG: _PLAN_ID_SCHEMA}, "additionalProperties": False, "allOf": [inner_without_additionalProperties]}`, where `inner_properties` is the original root's `properties` (empty if none).

Tests (use `jsonschema.Draft202012Validator`): for each shape, a valid instance that includes `hub_plan_id` passes; the same instance without it passes when the original would have accepted it; an unknown key is still refused when the original refused it.

## T41 re-export

`policy.py` imports `PLAN_ID_ARG` and `with_plan_id` from `schema_rewrite`, so existing imports keep working. Existing tests pass unchanged.

## T42 `validate_args`

File: `src/mcp_hub/schema_rewrite.py`.

```python
def validate_args(schema: Mapping[str, Any] | None, args: Mapping[str, Any]) -> list[str]:
```

Uses `jsonschema.Draft202012Validator(schema).iter_errors(args)`. Each error becomes a dotted field path (`".".join(str(part) for part in error.absolute_path)`), or `"<root>"` when the path is empty. Returns the paths in the order the errors come. A schema that is not valid JSON Schema (`jsonschema.SchemaError`) returns `[]`. A `None` schema returns `[]`.

Tests: a wrong type on a nested field gives its dotted path; a missing required key gives `"<root>"`; a broken schema gives `[]`; a valid argument gives `[]`.

## T43 plan id length

`policy.py`: change `token_hex(6)` to `token_hex(16)` (128 bits). Check that no existing test asserts the old length; if one does, stop and report.

## T44 repeat after seconds

The policy refuses an identical call that already succeeded. Add an optional clock to the policy object (default `time.monotonic`) and remember when the identical success happened. When `repeat_after_seconds` is greater than 0 and that many seconds have passed, the repeat is allowed. When it is 0 (the default), behaviour is unchanged.

The default for the setting comes from `PolicySettings().repeat_after_seconds` (defined in `settings.py`). Read it in the constructor default, not from the Hub config. Phase 2 passes the real value.

Tests with a fake clock: refused at 0 seconds; allowed after the window; unchanged behaviour when the window is 0.

## Verify

```
cd "../mcp-hub-track-d"
uv run --no-sync python -m unittest tests.test_schema_rewrite tests.test_policy -v
uv run --no-sync python -m ruff check .
uv run --no-sync python scripts/check_code_standards.py
uv run --no-sync python scripts/package_qc.py
```

## Stop and ask

- An existing test asserts the old plan id length or the old `with_plan_id` output for a composed root.
- The repeat check cannot be made time-based without changing a public signature used by `server.py`.

## Progress log

- T40 done: `schema_rewrite.py` with `with_plan_id` (flat, allOf/anyOf/oneOf/$ref, closed roots) and 6 tests. Deviation: the wrapper also takes `$defs`/`definitions` from the root, so `$ref` roots resolve.
- (agent: add one line per task here)
