# Interface contract

Keep jira_sync_ticket(issue_key, force=false) and jira_read_asset(issue_key, filename). Sync includes every attachment type and reports total/cached/unavailable counts. Image reads retain MCP image blocks; non-image reads return an embedded resource containing the encrypted cache's decrypted bytes, MIME and a Jira attachment URI. Binary retrieval does not promise text extraction. Original names and local references are included in ticket Markdown. Tools still enforce the existing account gate.
