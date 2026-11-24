# Specification Quality Checklist

- [X] US1 covers shared-caller impact (`source__callers`, `source__trace`, `source__read_symbol`, `source__search`, `source__read`), duplicate-function reuse audit, and optional Jira ticket alignment across confirmed `be_project`/`db_project`/`fe_project` sources.
- [X] US2 enforces the two-gate confirmation flow: Gate 1 (`confirm_risks`, `interaction: true`, `tier: "orchestrate"`) and Gate 2 (`ready` after `ponytail_check`) with `/ponytail` simplification audit (requiring confirmation before edits) and Mermaid workflow diagram.
- [X] US3 exposes all 8 read-only `mcp-read-source` graph/search/read tools in `config/hub.example.json` and `docs/mcps/mcp-read-source.md` with provenance-checked `changed_paths` and per-project receipt path unlocking.
- [X] No implicit workspace edits, Jira comments, or live SQL writes occur inside the `change-review` workflow.
- [X] All tracked spec artifacts are 100% English with repo-relative paths only, and offline test / `package_qc.py` / `smoke.py` acceptance criteria are measurable.
