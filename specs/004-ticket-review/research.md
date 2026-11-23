# Decisions

- Add a separate ticket-review playbook rather than overloading the existing Jira bug-investigation workflow.
- Reuse Jira read/sync/asset and source search/read tools. Ask the user to choose registered source aliases; never read child config or source directories directly.
- Add an explicit interaction-only graph stage and a multi-source definition flag. The old workflows remain single-project.
- Use fill-once role fields be_project/db_project/fe_project. Compare only confirmed roles, with source receipts carrying alias provenance so a path found in one source cannot unlock another.
- API/SP changes and related-feature impact are evidence-backed proposals, not automatic modifications. Unknown business rules become questions grouped by owner.
- User chose optional related-ticket reads after confirmation. A multi_ticket definition opts in explicitly; related_issue_keys is a bounded comma-separated set. Receipts carry issue provenance, assets are isolated by ticket, and refresh uses the selected issue's cache key.
