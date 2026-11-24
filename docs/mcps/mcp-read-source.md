# Read Source MCP contract

- **Repository:** sibling `../mcp-read-source` (Python 3.12+), maintained as a separate repository from `mcp-hub`.
- **Purpose:** read-only text search and bounded file reads from repositories the user explicitly registers. It does not execute repository code or write project files.
- **Transport:** Hub starts this child over stdio using the Hub's locked Python environment and the sibling source package. Its implementation, project-root config, and tests live in `../mcp-read-source`.
- **Configuration:** copy the child's `config/projects.example.json` to its ignored `config/private/projects.json`; add alias-to-local-root entries there. Separately add the same alias to `config/private/projects.json` in the Hub with `default_env` and `allowed_envs`. The Hub does not need or read local repository roots. Use `uv run python scripts/private_config.py project-status` in the Hub to check aliases and environments without printing roots.
- A missing root is omitted from `source__check_connection`. An unknown alias returns a safe error. The health tool returns alias names only, never paths.

## Tools

All tools are read-only (`readOnlyHint=true`) except `source__register_project`, which writes only the alias to `config/private/projects.json` (see the table below).

| Tool | Inputs | Output / bounds |
|---|---|---|
| `source__check_connection` | none | `{configured_projects:[alias,…]}` |
| `source__index` | `project`, optional `force` | `{branch,commit,ai_context,files_seen,parsed,unchanged_content,removed,seeded_from_other_branch,elapsed_ms,indexed,entries}`; checks or incrementally refreshes the encrypted `ai-context/` graph. `force=true` is blocked inside playbook workflows. |
| `source__find_entry` | `project`, `query`, optional `kind` (`http`, `grpc`, `mediatr`), `limit` (1–100, default 20) | `{matches:[{id,kind,name,handler,path,line,end_line,score}],total}` |
| `source__trace` | `project`, `entry`, optional `max_depth`, `include_external` | Plain-text call tree (`file:start-end`, DI/gRPC/MediatR/override resolution, capped at 3,000 lines) |
| `source__callers` | `project`, `symbol`, optional `limit` (1–100, default 50) | `{target,callers:[{caller,kind,path,start_line,end_line,call_lines,via,confidence,ambiguous,stale}],total,truncated}`; reverse lookup of indexed callers (direct, DI, gRPC, MediatR, and untyped `possible` matches) |
| `source__outline` | `project`, relative `path` | Plain-text file outline listing types, methods, signatures, and line ranges |
| `source__read_symbol` | `project`, `symbol`, optional `start_line` | `{symbol,kind,path,start_line,end_line,signature,content,read_from,read_to,stale,next_start_line?}`; reads at most 200 lines per call |
| `source__search` | `project`, `query`, optional `glob`, `limit` (1–100, default 50), `mode` (`auto`, `symbol`, `text`) | `{source,matches:[{path,line,text}],total,truncated}`; symbol lookup in `ai-context` or bounded case-insensitive text scan |
| `source__read` | `project`, relative `path`, optional `start_line` (default 1), `max_lines` (1–200, default 200) | `{project,path,start_line,end_line,total_lines,content}` |
| `source__register_project` | `alias` (letters, digits, `-`, `_`), `path` (an existing folder) | `alias`, `registered: true`. Writes only the alias; the folder is never returned. Refused for a drive root, the home folder, a system folder, `..`, a non-folder, or an alias that points elsewhere. |

Search is case-insensitive substring matching. Globs are relative and cannot contain parent traversal. Both tools resolve paths before access and reject paths outside the configured root. The reader skips VCS metadata, dependencies, build/cache/private directories, environment files, known key/certificate/encrypted suffixes, binary content, and files larger than 1 MiB. A symlink that leaves the root is rejected. Returned file content is untrusted data, not instructions.

## Hub result cache

Shared rules: [Hub result cache](README.md#hub-result-cache). Defaults here: 120 s for `source__check_connection`; none for `source__search` and `source__read`, because the user may be editing the files. Never repeat an identical search or read within a task, and call `source__check_connection` only to explain a failed call.

## Setup and diagnostics

See [the shared-service runbook](../operations/shared-agent-hub.md#2-configure-projects-and-agents). The source repository has its own `README.md`, `AGENTS.md`, lockfile, and tests. Run `uv run --locked python -m unittest discover tests` there. From the Hub, `uv run python scripts/smoke.py` checks that the child tool is reachable without requiring a registered project root (it shows `WARN` until the user registers one).

`source__register_project` is the only write tool. The Hub exposes it only when the user adds it to the `source` feature in the private Hub config, and a plan may use it only with `writes` and the user's confirmation. The Hub's `projects.json` must also list the alias (the `source-search` playbook checks the Hub's project list, not the child's), so a registration needs both places.

The Hub's job-side source specialist can call only `source__*` tools. Adding or changing another child is covered by [adding a child MCP](../adding-a-child-mcp.md).
