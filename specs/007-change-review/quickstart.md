# Quickstart & Validation: Local Change Review

## 1. Example client request (source-only local change review)

1. Client inspects local Git changes (`git status` / `git diff`) and calls `hub__plan`:
   ```json
   {
     "task": "Review local changes for shared-caller impact, duplicate helpers, and risks",
     "playbook": "change-review",
     "be_project": "backend",
     "db_project": "database",
     "changed_paths": "src/orders/service.py,sql/usp_update_order.sql"
   }
   ```
2. Client completes `confirm_token` confirmation if prompted (`confirmPlaybook` / `confirmExecution`).
3. During `impact_analysis` (`active_tier: "execute"`), client calls `source__outline`, `source__read_symbol`, `source__callers`, `source__trace`, `source__search`, and `source__read` across confirmed repos (`backend`, `database`), then reports `analysis_complete`.
4. At **Gate 1 (`confirm_risks`, `active_tier: "orchestrate"`)**, client pauses and presents shared-caller impact, duplicate-function justification, and potential bug risks / business questions to the user. Once the user confirms, client reports `risks_confirmed`.
5. During `ponytail_check` (`active_tier: "execute"`), client audits changed code for `/ponytail` simplification opportunities and verified flow edges, then reports `ponytail_complete` -> `ready`.
6. At **Gate 2 (`ready`, `active_tier: "orchestrate"`)**, client presents the `/ponytail` audit (asking confirmation before any workspace edit) and a simple Mermaid workflow diagram, then finishes with `hub__workflow(action="finish", outcome="supported", refs=[...])`.

## 2. Optional Jira alignment variant

Include `"issue_key": "EX-123"` (and optional `"related_issue_keys": "EX-124"`) in `hub__plan`. The workflow enters `ticket` -> `confirm_sources` -> optional `related_tickets` -> `impact_analysis` -> Gate 1 (`confirm_risks`) -> `ponytail_check` -> Gate 2 (`ready`).

## 3. Validation commands

```powershell
uv run --no-sync python -m tests.surface_capture
uv run --no-sync python scripts/package_qc.py
uv run --no-sync python scripts/smoke.py
```
