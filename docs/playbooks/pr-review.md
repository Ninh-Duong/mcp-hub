---
id: pr-review
when: The user gives a Bitbucket pull request URL and wants a correctness review of its changes.
servers: [mcp-pr-companion]
tools: [pr__list_synced_prs, pr__get_pr_sync_status, pr__get_pr_context_pack, pr__get_pr_file_changes,
        pr__get_pr_file_context, pr__search_pr_files, pr__get_pr_comments, pr__get_pr_comment]
max_limit: 200
---
## Goal

Review one Bitbucket pull request for concrete correctness and maintenance findings.

## Steps and tool inputs

1. If pr_url is absent, `pr__list_synced_prs(repository, status, offset, limit)` lists local PRs. Ask the user to select a URL, bind it using provide_input, then report pr_selected. Do not discover or sync unrelated PRs automatically.
2. Ask permission before the first write-capable PR tool. Context, file, diff, freshness and comment readers may sync encrypted cache; declare each needed tool in writes using continue_from. Do not call pr__get_pr_sync_status as a read-only probe. Unknown freshness is unknown, not fresh.
3. `pr__get_pr_context_pack(pr_url, refresh=false)` obtains context and reading strategy; report context_read. refresh=true requires an explicit user request and new plan.
4. `pr__search_pr_files(pr_url, path/language/status filters, limit)` selects relevant files; report files_selected. Never read every file by default.
5. `pr__get_pr_file_changes(pr_url, file_id, offset, max_chars)` reads bounded diffs. `pr__get_pr_file_context` for the same file_id follows only when surrounding context is needed. Page until the relevant evidence is available; a truncated diff is not a complete review.
6. Read `pr__get_pr_comments(pr_url, offset, limit)` after diffs when existing review discussion matters. Use `pr__get_pr_comment(pr_url, comment_id, snapshot_id, offset, max_chars)` for a full body; keep snapshot_id on continuation pages.
7. Keep the revision when the child reports source_commit/revision. A changed revision blocks the workflow; do not combine evidence across revisions. Report review_complete when review evidence is sufficient.

## Answer and stop conditions

Return findings by severity with file:line, impact and suggested fix, plus review coverage/limitations. No findings is not proof of safety. Stop on access errors or changed revision. Never approve, post comments, merge or bulk-sync as part of this playbook.

## Workflow definition

```workflow
{
  "entry": "select_pr",
  "entry_choices": {
    "pr_url": "context"
  },
  "inputs": [],
  "nodes": {
    "select_pr": {
      "tools": [
        "pr__list_synced_prs"
      ],
      "requires": [],
      "reports": {
        "pr_selected": "context"
      }
    },
    "context": {
      "tools": [
        "pr__get_pr_context_pack"
      ],
      "requires": [
        "pr_url"
      ],
      "reports": {
        "context_read": "files"
      }
    },
    "files": {
      "tools": [
        "pr__search_pr_files"
      ],
      "requires": [
        "pr_url"
      ],
      "reports": {
        "files_selected": "review"
      }
    },
    "review": {
      "tools": [
        "pr__get_pr_file_changes",
        "pr__get_pr_file_context",
        "pr__get_pr_comments",
        "pr__get_pr_comment"
      ],
      "requires": [
        "pr_url"
      ],
      "reports": {
        "review_complete": "ready"
      },
      "after": {
        "pr__get_pr_file_context": "pr__get_pr_file_changes",
        "pr__get_pr_comments": "pr__get_pr_file_changes",
        "pr__get_pr_comment": "pr__get_pr_comments"
      }
    }
  }
}
```
