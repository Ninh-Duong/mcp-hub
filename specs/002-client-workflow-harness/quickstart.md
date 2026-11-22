# Validation

Run `uv run --no-sync python -m unittest tests.test_workflow tests.test_workflow_runtime` for offline scenarios. Then run `uv run --no-sync python scripts/package_qc.py` and `uv run --no-sync python scripts/smoke.py`.

Select rcid-bug in hub__plan with project/env/rcid and optional since/until. List snapshots, report cache_hit or cache_miss with receipt references, follow allowed_tools, then finish supported/insufficient/blocked. A remote query before cache must fail before reaching the child.

## Implementation validation

Offline package QC passed: 1,246 tests, 3 environment-dependent skips, 92% overall line/branch coverage. The workflow modules have 97% combined coverage. Lint, privacy scanning, code standards, documentation checks and compile checks passed. The external surface baseline intentionally adds rcid, hub__workflow and the three policy settings. Live smoke is tracked separately in tasks.md.
