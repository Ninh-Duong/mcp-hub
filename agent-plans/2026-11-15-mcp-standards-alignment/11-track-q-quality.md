# Track Q: quality and conformance (Phase 3)

Status: waiting for Phase 2
Branch: `arch/track-q` (from `arch/base` after Phase 2 merges). Worktree: `../mcp-hub-track-q`.

Owns:
- `tests/test_conformance_eras.py` (new; flat in `tests/`, no package)
- `pyproject.toml` (the `[tool.ruff]` section only)
- `docs/packaging-and-qc.md`
- `AGENTS.md` (the `ruff` wording in the definition of done only)
- Lint fixes in any `src/` or `tests/` file that the new ruff families flag (one commit per family; no `noqa`)

## Tasks

- [ ] T090 `tests/test_conformance_eras.py`: run an in-memory `Client` against a server built by the Hub's `create_mcp_server` with fake children, in the default mode and in `mode="legacy"`. Check: `tools/list` annotations are preserved and the order is stable; `tools/call` returns a success and an error result (`isError`); a child tools change produces one `tools/list_changed` notification; a condensed or structured result validates against its `outputSchema`; the instructions are at most 4096 characters; prompts list. Read the SDK's in-memory client helper first (verify its name under `mcp/`).
- [ ] T091 Add ruff rule family `B` to `[tool.ruff.lint]`; fix the findings in code (no suppression); one commit.
- [ ] T092 Add ruff rule family `UP`; fix the findings; one commit. Update the `ruff check` wording in `AGENTS.md` and `docs/packaging-and-qc.md`.

## Verify

```
cd "../mcp-hub-track-q"
uv run --no-sync python -m unittest tests.test_conformance_eras -v
uv run --no-sync python -m ruff check .
uv run --no-sync python scripts/package_qc.py
```

## Stop and ask

- The SDK does not offer an in-memory client that accepts the legacy mode.
- A ruff `UP` fix would change a public API.

## Progress log

- (agent: add one line per task here)
