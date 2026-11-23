# Data model

Catalog entries add an attachment schema marker, total count, completeness and attachment records (Jira id, original filename, safe cached filename, MIME type, size and status). Existing image count and assets identity fields remain compatible. Status is cached, size_limit or download_failed. Cached records alone authorize content reads. An entry without the marker requires ordinary-sync backfill.
