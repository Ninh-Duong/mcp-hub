# Data Model

WorkflowDefinition may explicitly opt into multi_source and an interaction-only node. Ticket-review context includes issue_key and optional registered be_project/db_project/fe_project aliases. The confirm_sources node has no child tools; the sources_confirmed decision is client-reported and locks the selected aliases.

Receipt carries the source project for source calls. Readable paths are indexed by both project alias and relative path. The Plan policy and workflow scope both enforce selected aliases; the existing five playbooks keep their original project restriction.

WorkflowDefinition may also opt into multi_ticket. related_issue_keys is normalized before confirmation, then locked with source selection. Related reads execute only in the related ticket node; attachments and refresh prerequisites refer to the same issue. Every selected related ticket must be attempted; missing-cache evidence allows useful source comparison but requires an insufficient finish until all selected evidence exists.

Review output is returned to the client, not saved into tracked files or Jira. It distinguishes requirements, observed source evidence, proposals, confirmations and hypotheses. Review completion and implementation readiness are distinct.
