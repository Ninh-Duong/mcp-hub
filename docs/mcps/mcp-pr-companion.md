# PR Companion MCP contract

- Repository: sibling `../mcp-pr-companion` (Node 20.12+). Its own rules: `../mcp-pr-companion/AGENTS.md`.
- Hub discovers this supported sibling automatically from the relative path. No private Hub config is needed unless overriding or disabling it; `hub__status` reports detection and connection.
- Transport: local stdio process launched by the Hub as `node bin/cli.js server` in the child repo (`config/private/mcps/mcp-pr-companion.json`, template `config/mcps/mcp-pr-companion.example.json`). `bin/cli.js` runs `npm ci` / `npm run build` when needed, so the first start can take a minute; `inheritEnv` includes `APPDATA`, `LOCALAPPDATA`, `PATHEXT`, and `COMSPEC` for npm on Windows.
- Configuration: the Hub passes no secrets. The child reads its own `.env` (`BITBUCKET_EMAIL`, `BITBUCKET_API_TOKEN`; optional `BITBUCKET_WORKSPACE`, `BITBUCKET_REPOSITORY`, `TICKET_PREFIXES`, `ALLOWED_EMAILS`, `AUTO_SYNC_ON_START`, `PRIVACY_*`, `AI_CONTEXT_*`, `DEBUG`). The user fills it; agents never read it. `BITBUCKET_REPOSITORY` is needed only for `pr__list_my_open_prs` / `pr__sync_my_open_prs` without a `repository` argument, for `AUTO_SYNC_ON_START`, and for `node bin/cli.js check`. Optional non-secret overrides go in the child config's `env`, e.g. `env.PRIVACY_FILE_PATH_MODE`; never set it to `full` under the Hub, which would return real paths.
- `timeoutSeconds` is 300 because the first sync of a large PR downloads the full diff, and `pr__sync_my_open_prs` syncs several PRs in one call.
- Data at rest: everything the child stores under `../mcp-pr-companion/ai-context/` is encrypted (AES-256-GCM, key from the repository UUID). Every tool that touches local data first unlocks the repository with a live Bitbucket check (cached 60 s), so the cache cannot be read offline or by an account without read access to the repository. Details: `../mcp-pr-companion/docs/ENCRYPTION.md`.
- Canonical storage paths group PRs under `open`, `draft`, `merged`, or `declined`; the group reflects the last successful sync. Draft PRs are Bitbucket `OPEN` PRs with the draft flag set. Existing status-less folders remain readable and move only after a valid encrypted revision is published.
- `AUTO_SYNC_ON_START=true` in the child's `.env` makes every launch (one per client session) sync the account's open PRs in the background.

## Tools

The PR tools take `pr_url` (`https://bitbucket.org/<workspace>/<repo>/pull-requests/<id>`). No tool writes to Bitbucket.

- `pr__get_pr_context_pack`: **call first.** Syncs the PR from Bitbucket when it is not cached locally or its commits, state, or draft flag changed, then returns Markdown: Read Strategy (`Read next` / `Optional` / `Skip` file ids), overview, change summary, description, commits. Writes only the child's local `ai-context/` cache and moves the directory to the matching status group. Input: `refresh` (force a re-sync), `include_file_table` (append the table of every file).
- `pr__search_pr_files`: read-only (local cache, after the access check). Lists changed files filtered by `path` (substring of the **sanitized** path), `language`, `status`, `change_kind`, `risk_tag`, up to `limit` (default 50, max 200). Output: JSON `{total, files}`.
- `pr__get_pr_file_context`: read-only (local cache, after the access check). Markdown for one `file_id` (`file_0001` or `1`) with the sanitized diff, paged by `offset` / `max_chars` (default 16000). A page that does not reach the end finishes with `continue with offset N.`
- `pr__get_pr_file_changes`: read-only (local cache, after the access check). JSON `{change, patch?}` for one `file_id`: `change` holds classification and evidence, risk level/tags/evidence, symbols; `patch` is `{text, total_chars, next_offset}` (`include_patch`, default true, `offset`, `max_chars`).
- `pr__get_pr_sync_status`: read-only. Whether the PR is cached, its revision, PR status, sync time and commits. Makes only the access-check request.
- `pr__list_synced_prs`: read-only. Lists the local cache after unlocking each repository. Inputs: optional `repository` (`workspace/repo`), optional `status` (`open`, `draft`, `merged`, `declined`), `offset` (default 0), and `limit` (default 50, max 200). Output: JSON `{total, prs:[{pr_url, repository, id, title, pr_status, updated_at}], next_offset}`.
- `pr__sync_my_open_prs`: **writes** the child's local encrypted cache only (never Bitbucket). Syncs every open PR authored by the child's account in `repository` (`workspace/repo`) or every `BITBUCKET_REPOSITORY` entry, 3 at a time, skipping PRs whose commits, state, and draft flag match unless `force: true`. Its existing open-only behavior is unchanged. Output: JSON `{total, synced, unchanged, failed, prs:[{pr_url, id, title, status, ticket, overall_risk, files_changed, updated_on, error?}]}`.
- `pr__get_pr_comments`: read-only, fetched live from Bitbucket (comments change without new commits). Review comments with sanitized text; inline ones carry `file_id`, `path`, `line` when the PR is synced. Input: `include_resolved` (default true), `offset`, `limit` (default 50, max 200). Output: JSON `{total, complete, comments, next_offset}`.
- `pr__list_my_open_prs`: read-only. Open PRs authored by the child's Bitbucket account in `repository` (`workspace/repo`) or every repository in the child's `BITBUCKET_REPOSITORY`. Output: JSON `{total, prs:[{pr_url, title, …, local}]}`; pass a `pr_url` straight to the other tools.
- `pr__check_bitbucket_connection`: read-only. One Bitbucket request that verifies the child's credentials (and `ALLOWED_EMAILS`). No input. Output: `{"connected": true}`; otherwise an error naming the cause (HTTP 401 credentials, HTTP 403 missing `read:user`, unreachable). Returns no account data.

## Care

- To sync all open PRs through the Hub, call `pr__sync_my_open_prs` with the requested `repository`, then answer with a table of `prs`: PR (`#id` linked to `pr_url`), title, status, ticket, risk, and files. List `failed` rows with their `error`.
- To sync historical groups, the user runs `node bin/cli.js sync <open|draft|merged|declined|all>` in the child repository. This command scans every page for the selected states and filters to PRs authored by the configured account across `BITBUCKET_REPOSITORY`. `open` excludes drafts; `all` combines the four disjoint groups. An interrupted pagination run is marked incomplete and exits with an error code. Afterward, use `pr__list_synced_prs` to browse the cached PRs by status and read them with the other PR tools.
- Call `pr__list_synced_prs` to discover already synced PR URLs by status. Pass each returned `pr_url` to `pr__get_pr_context_pack` or the file tools.
- The file tools fail with `Error: PR is not synced yet…` until `pr__get_pr_context_pack` or `pr__sync_my_open_prs` has run for that PR.
- Never read, copy or decrypt `../mcp-pr-companion/ai-context/` with file tools or scripts; it is encrypted and readable only through these tools. `Error: No access: …` means the child's Bitbucket check failed (credentials, `ALLOWED_EMAILS`, repository access, or network). Tell the user; do not look for another way to read the data.
- Follow the Read Strategy: open the `Read next` ids, then `Optional` if needed. Do not page through every file of a large PR.
- Re-review: when the pack has "Changed since last sync (rev_…)", open only the listed file ids.
- Addressing review feedback: `pr__get_pr_comments` with `include_resolved: false`, then open each comment's `file_id`.
- When the pack's `Ticket` is not `N/A` and the `mcp-jira-sync` child is enabled, `jira__list_synced` shows whether that ticket's requirements are already in the local catalog. `jira__sync_ticket` writes files, so call it only when the user asks for the ticket.
- Keep paging with `next_offset` / `continue with offset N` instead of raising `max_chars` to the maximum.
- Paths, emails, UUIDs and secrets are sanitized by design. Never try to recover the real values, and do not ask the user for them.
- A pack that starts with `Note: could not check Bitbucket…` is cached data that may be stale. Say so when you rely on it.
- Each `pr__get_pr_context_pack` call makes at least one Bitbucket request. Avoid `refresh: true` in loops (Bitbucket rate-limits per user).
- `Error: Access denied … ALLOWED_EMAILS` (or `Error: No access: Access denied …` from the file tools) means the child is locked to other accounts. Tell the user; do not work around it.

## Hub integration

- Read-only smoke check: `pr__check_bitbucket_connection`; success means a non-error result whose JSON has `"connected": true`. If the payload changes, update `scripts/smoke.py` (`_is_connected`) and `tests/test_smoke.py`.
- Compatibility notes: preserve the ten child tool names, their read/write annotations, and the launch command `node bin/cli.js server`. The child's own rules (`../mcp-pr-companion/AGENTS.md`) list which docs to update when a tool changes.

## Diagnostics

- Child logs: `../mcp-pr-companion/Logs/mcp-server.log` (server and launcher; `[LAUNCHER]` lines explain startup failures). Hub events: `logs/mcp-hub.jsonl`.
- Child self-check: `node bin/cli.js check` in the child repo; `node bin/cli.js prepare` installs and builds.
- End-to-end: `uv run python scripts/smoke.py` (calls `pr__check_bitbucket_connection`).
- `No access: Bitbucket rejected the credentials (HTTP 401)`: the token in the child's `.env` is invalid. `Failed to fetch PR metadata: HTTP 403`: it lacks `read:pullrequest` / `read:repository`.
- `Unencrypted ai-context file`: old plaintext cache; the user runs `node bin/cli.js purge-plaintext` in the child repo.
