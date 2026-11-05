---
id: pr-review
when: The user gives a Bitbucket pull request URL and wants a review, a summary, or an answer about its changes.
servers: [mcp-pr-companion]
tools: [pr__list_synced_prs, pr__get_pr_sync_status, pr__get_pr_context_pack, pr__get_pr_file_changes,
        pr__get_pr_file_context, pr__search_pr_files, pr__get_pr_comments]
max_limit: 200
---
## Goal

Review from the local PR cache, and read only the files and hunks the question needs.

## Steps

1. **Start with the context.** Call `pr__get_pr_context_pack` with `pr_url`. It syncs only when the PR changed, and its Read Strategy says which files matter.
   - Pass `refresh: true` only when the user asks for fresh data.
   - If the user gave no URL, call `pr__list_synced_prs` once and ask which PR to use.
2. **Follow the Read Strategy.**
   - Read the diffs of the files it names with `pr__get_pr_file_changes`.
   - Read surrounding code with `pr__get_pr_file_context` only where a hunk is unclear.
   - Use `pr__search_pr_files` to find where a changed symbol is used, instead of reading whole files.
3. **Read review threads** with `pr__get_pr_comments` only when the question is about feedback or open threads.

## Answer

- Findings ordered by severity, each with its `file:line` and a suggested change.
- What you did not read, if the PR is large.

## Never

- Read every changed file when the Read Strategy names the relevant ones.
- Refresh the PR or call `pr__sync_my_open_prs` without the user asking.
