# Decisions

- Reuse PlanBook and WorkflowRuntimeMixin; replace pilot-specific state transitions with validated JSON graphs in each Markdown definition.
- Reuse standard JSON and jsonschema already installed. Reject unknown definition fields, invalid targets and graph cycles at startup.
- Resolve explicitly named subflows once at startup; includes still merges tools/prose only. Jira names rcid/service branches explicitly.
- Keep optional task context in the workflow, supplying unset values through provide_input. Never replace fixed values. Windows are absolute and bounded.
- Clone receipts/seen call history only through same-scope continue_from; do not replay successful calls. Supersede the previous workflow only after every validation succeeds.
- PR freshness/status, file and comment readers are write-capable per their contract. Ask permission before the first such operation and declare exact tools in writes.
