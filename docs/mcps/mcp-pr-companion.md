# PR Companion MCP contract

- Repository: sibling `../mcp-pr-companion` (Node 20.12+). Its own rules: `../mcp-pr-companion/AGENTS.md`.
- Transport: local stdio process launched by the Hub as `node bin/cli.js server` in the child repo (`config/private/mcps/mcp-pr-companion.json`, template `config/mcps/mcp-pr-companion.example.json`). `bin/cli.js` runs `npm ci` / `npm run build` when needed, so the first start can take a minute; `inheritEnv` includes `APPDATA`, `LOCALAPPDATA`, `PATHEXT`, and `COMSPEC` for npm on Windows.
- Configuration: the Hub passes no secrets. The child reads its own `.env` (`BITBUCKET_EMAIL`, `BITBUCKET_API_TOKEN`; optional `ALLOWED_EMAILS`, `PRIVACY_*`, `AI_CONTEXT_*`). The user fills it; agents never read it. Optional non-secret overrides go in the child config's `env`, e.g. `env.PRIVACY_FILE_PATH_MODE`.
- `timeoutSeconds` is 300 because the first sync of a large PR downloads the full diff.

## Tools

The PR tools take `pr_url` (`https://bitbucket.org/<workspace>/<repo>/pull-requests/<id>`). No tool writes to Bitbucket.

- `pr__get_pr_context_pack`: **call first.** Syncs the PR from Bitbucket when it is not cached locally or has new commits, then returns Markdown: Read Strategy (`Read next` / `Optional` / `Skip` file ids), overview, change summary, description, commits. Writes only the child's local `ai-context/` cache. Input: `refresh` (force a re-sync), `include_file_table` (append the table of every file).
- `pr__search_pr_files`: read-only, local. Lists changed files filtered by `path` (substring of the **sanitized** path), `language`, `status`, `change_kind`, `risk_tag`, up to `limit` (default 50, max 200). Output: JSON `{total, files}`.
- `pr__get_pr_file_context`: read-only, local. Markdown for one `file_id` (`file_0001` or `1`) with the sanitized diff, paged by `offset` / `max_chars` (default 16000). A page that does not reach the end finishes with `continue with offset N.`
- `pr__get_pr_file_changes`: read-only, local. JSON for one `file_id`: classification and evidence, risk level/tags/evidence, symbols, and `patch: {text, total_chars, next_offset}` (`include_patch`, `offset`, `max_chars`).
- `pr__get_pr_sync_status`: read-only, local. Whether the PR is cached, its revision, sync time and commits. Never contacts Bitbucket.
- `pr__check_bitbucket_connection`: read-only. One Bitbucket request that verifies the child's credentials (and `ALLOWED_EMAILS`). No input. Output: `{"connected": true}`; otherwise an error naming the cause (HTTP 401 credentials, HTTP 403 missing `read:user`, unreachable). Returns no account data.

## Care

- The file tools fail with `Error: PR is not synced yet…` until `pr__get_pr_context_pack` has run for that `pr_url`.
- Follow the Read Strategy: open the `Read next` ids, then `Optional` if needed. Do not page through every file of a large PR.
- Keep paging with `next_offset` / `continue with offset N` instead of raising `max_chars` to the maximum.
- Paths, emails, UUIDs and secrets are sanitized by design. Never try to recover the real values, and do not ask the user for them.
- A pack that starts with `Note: could not check Bitbucket…` is cached data that may be stale. Say so when you rely on it.
- Each `pr__get_pr_context_pack` call makes at least one Bitbucket request. Avoid `refresh: true` in loops (Bitbucket rate-limits per user).
- `Error: Access denied … ALLOWED_EMAILS` means the child is locked to other accounts. Tell the user; do not work around it.

## Hub integration

- Read-only smoke check: `pr__check_bitbucket_connection`; success means a non-error result whose JSON has `"connected": true`. If the payload changes, update `scripts/smoke.py` (`_is_connected`) and `tests/test_smoke.py`.
- Compatibility notes: preserve the six child tool names, their read/write annotations, and the launch command `node bin/cli.js server`. The child's own rules (`../mcp-pr-companion/AGENTS.md`) list which docs to update when a tool changes.

## Diagnostics

- Child logs: `../mcp-pr-companion/Logs/mcp-server.log` (server and launcher; `[LAUNCHER]` lines explain startup failures). Hub events: `logs/mcp-hub.jsonl`.
- Child self-check: `node bin/cli.js check` in the child repo; `node bin/cli.js prepare` installs and builds.
- End-to-end: `uv run python scripts/smoke.py` (calls `pr__check_bitbucket_connection`).
- `Failed to fetch PR metadata: HTTP 401/403`: the token in the child's `.env` is invalid or lacks `read:pullrequest` / `read:repository`.
