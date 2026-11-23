# Feature Specification: Complete Ticket Attachments

**Created**: 2026-11-23
**Status**: Authorized for implementation

## User Scenarios & Testing

### US1 - Sync the complete attachment set (P1)

Syncing a ticket includes images and other attached files by default. Every file is stored encrypted and identified by its original name, type, size and local reference. A file that cannot be downloaded is explicitly reported rather than silently omitted.

### US2 - Reuse and repair cached attachments (P1)

Unchanged files are reused. An old image-only cache, a missing file or an incomplete sync must be repaired by the next ordinary sync, even when the ticket has not changed. Removed attachments disappear from the cache.

### US3 - Read attachments with their context (P2)

The agent can request a listed attachment through the existing read tool. Images remain viewable; other files are returned with their content type and original name. The agent considers filename, file content and ticket references when relating evidence to a case or acceptance criterion, and labels unsupported interpretation clearly.

## Functional Requirements

- FR-001: All attachment types are attempted by default, subject to the existing per-file size limit and account access.
- FR-002: Attachment bytes and metadata remain encrypted; no plaintext temporary file is created.
- FR-003: Preserve original filenames and distinguish duplicate/sanitized names without overwriting another attachment.
- FR-004: Expose cached, unavailable and over-limit status for every attachment; an incomplete cache is never reported as fully up to date.
- FR-005: Preserve existing image reads and tool arguments; support retrieving other cached files without following external links or executing them.
- FR-006: Do not add OCR, document conversion, archive extraction, comments ingestion or background refresh in this change.

## Success Criteria

Offline tests prove mixed-file sync, encryption, exact binary retrieval, unchanged reuse, old-cache backfill, missing-file repair, collision isolation, size/download failures and attachment removal. Existing image workflows and both supported MCP generations remain compatible.

## Assumptions

Complete means every Jira attachment is represented and attempted; configured size limits remain in force. Downloading a document is distinct from extracting its text. Only requested sync operations write cache; read tools remain read-only. Fixtures contain invented project data only.
