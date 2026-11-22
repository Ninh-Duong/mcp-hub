# Validation

Run the workflow/parser/runtime unit tests offline, then `uv run --no-sync python scripts/package_qc.py` and `uv run --no-sync python scripts/smoke.py`.

For each playbook: confirm selection, inspect status, provide missing context, execute allowed tools and report actual receipt references. Test cache hit/miss, no-data and failure branches. For Jira/PR, deny refresh and verify no write; then explicitly allow the exact writes using continue_from and verify no completed calls replay.

Run all five README examples through offline fixtures. Missing log time must be requested, and bounded source search must not claim a repository-wide count.

## Recorded validation

Package QC passed with 1,263 tests (three skips) and 92% coverage. Live smoke remains an external-integration limitation; see tasks.md. Reload the Hub and its client connection to use the new code after review; this implementation did not restart the user's shared service.
