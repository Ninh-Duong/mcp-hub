# Interface Contract

hub__plan and provide_input accept be_project/db_project/fe_project as bounded registered aliases and related_issue_keys as comma-separated selected keys. Only explicit multi_source/multi_ticket definitions widen these scopes. status advertises missing source selection and registered alias choices without roots. Optional selections may be cleared before confirmation, never after.

The confirm_sources interaction node accepts a sources_confirmed report after a successful ticket read and at least one source alias selection. No source or related-ticket read is allowed before this report. Selected roles/keys cannot change or grow after confirmation; a refresh continuation cannot widen them. Source calls attach project provenance; Jira calls attach issue provenance. A source path or attachment from one scope cannot unlock another. Supported finish requires referenced reads for every selected source and related ticket.

The final review has Summary, Missing/ambiguous requirements, Source comparison, Confirmations (common/BE/FE/QC), Candidate API/DB changes, Related feature impact, Acceptance/regression cases and Limitations. Each confirmation names owner, question, reason/evidence and blocking status. No implementation or Jira-comment tool is offered.
