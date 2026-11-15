# Track Q: quality and conformance (Phase 3)

Status: in progress (T090 to T092 done; the closing package_qc run is pending)
Branch: `arch/track-q` (from `arch/base` after Phase 2 merges). Worktree: `../mcp-hub-track-q`.

Owns:
- `tests/test_conformance_eras.py` (new; flat in `tests/`, no package)
- `pyproject.toml` (the `[tool.ruff]` section only)
- `docs/packaging-and-qc.md`
- `AGENTS.md` (the `ruff` wording in the definition of done only)
- Lint fixes in any `src/` or `tests/` file that the new ruff families flag (one commit per family; no `noqa`)
- Lint fixes in `scripts/call_report.py`, `scripts/smoke.py` and `scripts/bench_call_path.py` (decision D11; same behaviour, no new options, `scripts/package_qc.py` unchanged)

## Tasks

- [x] T090 `tests/test_conformance_eras.py`: run an in-memory `Client` against a server built by the Hub's `create_mcp_server` with fake children, in the default mode and in `mode="legacy"`. Check: `tools/list` annotations are preserved and the order is stable; `tools/call` returns a success and an error result (`isError`); a child tools change produces one `tools/list_changed` notification; a condensed or structured result validates against its `outputSchema`; the instructions are at most 4096 characters; prompts list. Read the SDK's in-memory client helper first (verify its name under `mcp/`).
- [x] T091 Add ruff rule family `B` to `[tool.ruff.lint]`; fix the findings in code (no suppression); one commit.
- [x] T092 Add ruff rule family `UP`; fix the findings; one commit. Update the `ruff check` wording in `AGENTS.md` and `docs/packaging-and-qc.md`.

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
- 2026-11-15 T090 done: `tests/test_conformance_eras.py` adds 14 tests (7 era checks each in the default mode and in `mode="legacy"`). They passed on the first run against the existing code, so no source change. Legacy: the Hub publishes one change but sends no `tools/list_changed` to the 2025 client; the test pins that accepted limitation.
- 2026-11-15 T091 blocked, not committed: `B` in `[tool.ruff.lint] select` gives 22 findings. Two are outside this card's Owns list, in `scripts/`: `scripts/call_report.py:74` (B905, `zip` without `strict`) and `scripts/smoke.py:268` (B904, `raise ... from`). `scripts/package_qc.py` runs `ruff check .` over the whole repo, so the family cannot pass without editing them, and a per-file ignore is not allowed. The other 20 are 12 in `src/` (B008 defaults, B905) and 8 in `tests/` (B905, B007, B023). None needs a public API change. Decision needed: add `scripts/` to Owns for lint fixes, or say how to proceed.
- 2026-11-15 T092 not started: it is blocked by the same scripts rule. `UP` gives 96 findings: 43 in `src/`, 51 in `tests/`, and 2 in `scripts/` (`scripts/bench_call_path.py:19` UP035, `scripts/smoke.py:274` UP017). 95 are auto-fixable; `src/mcp_hub/jobs/runner.py:148` (UP047, generic function) needs a manual change. The fixes I checked change no public API.
- 2026-11-15 T091 done (D11 lifts the scripts block; this line supersedes the T091 blocker above): `B` is in `[tool.ruff.lint] select`, and the 22 findings are fixed in code, none suppressed. `zip(strict=True)` where the lengths are equal by construction; `strict=False` where the code relies on truncation or the lengths can drift. The B008 defaults are module-level instances of frozen dataclasses, so values and types are unchanged. Unit suite: 1143 tests OK (one skipped); ruff and the standards check pass.
- 2026-11-15 T092 done: `UP` is in `[tool.ruff.lint] select`; 96 findings fixed in code. 95 are ruff's safe rewrites (`datetime.UTC`, `collections.abc` imports, `TimeoutError`, unquoted annotations), plus the unused `timezone` imports they left. UP047 in `src/mcp_hub/jobs/runner.py` is by hand: `_db` uses PEP 695 type parameters and the module-level `TypeVar` is removed. No public API changed. The `ruff check` wording is updated in `AGENTS.md` and `docs/packaging-and-qc.md`. Unit suite: 1143 tests OK (one skipped); ruff, the standards check and the docs check pass.
