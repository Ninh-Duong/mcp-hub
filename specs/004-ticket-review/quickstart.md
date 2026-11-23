# Validation

Run the ticket-review offline tests, workflow/parser regressions, package QC and smoke. Confirm source selection before calling source tools. Search BE and DB separately and verify a path found in BE cannot authorize reading that path in DB. Review without FE must still list FE/QC questions as unverified where appropriate.

Select related_issue_keys only after the user chooses them. Unselected issues must be refused; assets and cache-refresh prerequisites must stay with the matching issue. A missing related cache can remain a visible limitation but cannot produce a supported finish without the missing evidence.

Example client request: Use mcp-hub to review ticket EX-123 for requirement clarity and implementation impact. Ask me which BE/database/optional FE projects to compare before reading their source.

## Recorded validation

Package QC passed with 1,277 tests (three skips) and 92% coverage, including a repeated pre-merge run. The source/Jira/log/PR/Hub smoke checks passed; live SQL is an external certificate-related failure and is not used by this playbook. The user requested branch publication and merge after this limitation was disclosed; see tasks.md. Restart the Hub and reload the client connection to load the change. Version remains 0.9.0.
