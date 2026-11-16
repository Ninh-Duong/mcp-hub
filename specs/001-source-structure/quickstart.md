# Quickstart: Validating the Refactor

Run these checks from the repository root after each phase. All commands are offline unless marked as live.

## Prerequisites

- Windows PowerShell or PowerShell 7 (`pwsh`)
- `uv` available, and the locked environment synced (`uv sync`)
- The refactor branch checked out: `git branch --show-current` prints `refactor/source-structure`

## 1. Quality gate (every phase)

```powershell
uv run --no-sync python scripts/package_qc.py
```

Expected: `[QC] ... passed` for lint, unit tests, coverage (≥ 85 percent), leak check, code standards, docs check, and compile check. Live smoke is skipped by default.

## 2. Contract diff (every phase)

Regenerate the surfaces listed in `contracts/README.md` and compare them with `tests/contract_baseline/`: `uv run --no-sync python -m unittest tests.test_surface_contract -v`.

Expected: no difference for tools, routes, audit events, entry points, or existing settings keys. New settings keys appear only where research.md R7 lists them, each with its documented default.

## 3. Live smoke (every phase that touches children or the Hub)

```powershell
uv run --no-sync python scripts/smoke.py
```

Expected: each enabled child connects in turn, then the Hub is checked. A child without configured credentials is reported as skipped, not as a failure. This needs real child configuration, so run it on the maintainer's machine, not in CI.

## 4. Layering check

The import-graph test is part of the unit tests in step 1. To inspect it alone:

```powershell
uv run --no-sync python -m unittest tests.test_module_boundaries -v
```

Expected: every import edge matches the table in `data-model.md`. Added in Phase 2.

## 5. Latency (Phase 2 and after)

```powershell
uv run --no-sync python scripts/bench_call_path.py
```

Compare with `tests/contract_baseline/bench.json` (median p50 and mean). Expected: not more than 5 percent slower.

## 6. Desktop app (Phase 3 and after)

```powershell
uv run --no-sync python -m mcp_hub.desktop
```

Manual check: the status view shows children and the Hub state; the update card appears; mini mode toggles; the usage and processing views update while the Hub runs; closing the window saves preferences and stops only the usage and processing streams (`desktop/app.py` `closeEvent`); the Hub keeps running.

## 7. Documentation

```powershell
uv run --no-sync python scripts/check_docs.py
```

Expected: no broken link, no missing settings key in `docs/settings.md`, no missing event or route in `docs/reference.md`. Included in step 1.

## Failure handling

- A failing gate blocks the phase commit. Fix it in the same phase; do not move on.
- A contract diff that cannot be explained is a behavior change. Stop and report it instead of updating the baseline.
- If the live smoke fails because a child is not configured, report that child as not configured. Do not treat it as a refactor regression.
