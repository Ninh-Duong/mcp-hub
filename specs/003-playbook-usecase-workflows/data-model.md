# Data Model

Playbook owns an optional WorkflowDefinition: entry, required initial inputs, nodes, transitions and explicit subflows. Nodes own tools, required inputs and semantic events. Transitions refer to successful receipts from the current stage; failed receipts can block a workflow. Workflow owns mutable context (project/env/window and task identifiers), bounded receipts, client-reported decisions and a per-plan lock. Stage can await input or refresh permission without consuming calls.

Receipt retains metadata and safe scope anchors for source paths and PR revisions. Raw tool results remain in the existing evidence store, not audit. Context can only fill missing values. A continuation preserves scope, stage, history, call budgets and references; adds only declared playbook refresh writes; and makes the old workflow superseded. Restart or eviction requires a new plan, not replay.
