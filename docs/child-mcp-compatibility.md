# Child MCP compatibility contract

This is the Hub's source of truth for changing a child MCP while keeping it usable through `mcp-hub`. Read it with any agent instructions in the child repository, then use that child's contract in [mcps/](mcps/) for its current tools and configuration. This document describes shared requirements; it does not replace child-specific instructions.

## Process and transport

- The Hub launches each child as a separate local process over MCP `stdio`, using `command`, an argument list, and the configured child `cwd`. Do not require a shell command string or assume the Hub's working directory.
- Keep stdout exclusively for MCP protocol messages. Write diagnostics to the child's own log file; the Hub currently discards child stderr.
- Keep startup predictable and tool calls bounded. `timeoutSeconds` is a positive finite number of seconds for each routed tool call; it is not the client application's Hub startup timeout.
- Keep transport as `stdio`. The Hub allowlists exact child tool names and exposes the configured aliases, normally `<prefix>__<tool>`.

## Tools and behavior

- Keep tool names and input schemas stable when possible. If a name or required input changes, update the Hub allowlist and the child contract in the same change.
- Return valid MCP results with documented input and output shapes. Mark failures with the MCP error flag; do not rely on message prefixes to signal failure.
- Set tool annotations to match behavior: read-only tools must not write, and every tool that writes must declare that accurately and document what and where it writes.
- Keep a read-only health/check tool that verifies the required upstream connection without changing data. Register it in `scripts/smoke.py` and make its success check validate the actual success result.
- Never put credentials or private configuration values in tool descriptions, tool results, errors, or logs. Return upstream data only when it is part of the requested tool behavior.

## Configuration and secrets

- Document required environment variable names and private config key paths, never their values. Hub templates leave secrets as empty strings (see [Configuration sources](#configuration-sources)); use `<SET_BY_USER>` only for a value the child cannot run without and has no local fallback for.
- The child receives only the MCP SDK's safe defaults, names in `inheritEnv`, and values in its configured `env`. Do not assume access to the Hub's private files or the user's entire environment.
- Keep local account files ignored by Git. Do not add credentials to source, fixtures, examples, test output, or the child contract.
- Child errors may be forwarded to the agent or included in Hub error logs. Avoid including secret values in errors; Hub log redaction is defense in depth, not permission to emit secrets.

## Configuration sources

Every child runs both standalone and under the Hub, with one rule for where its configuration comes from:

1. **Standalone**: the child reads its own local files (`.env`, `config/*.env`), as documented in its repository.
2. **Under the Hub**: the child config's `env.CENTRAL_CONFIG_PATH` points to that same private file, `config/private/mcps/<server-id>.json`, written relative to the child repo (e.g. `../mcp-hub/config/private/mcps/<server-id>.json`). The child reads only its own section of it (`openobserve`/`seq`, `jira`, `bitbucket`).
3. **Priority, per value**: variables already in the child's environment (the config's `env`) > the Hub section > the child's local files > defaults. An empty string counts as unset, so a section left empty keeps the local files in use.
4. A relative `CENTRAL_CONFIG_PATH` resolves against the child repo root, not the process working directory.
5. A missing or unreadable Hub file is logged by the child as a warning, and the local files are used. A child never writes the Hub file; its own login/setup commands keep writing its local files.
6. Hub templates leave account values as empty strings. `<SET_BY_USER>` would make the Hub skip the child as `credentials required` even when its local files hold an account.
7. The child's read-only check (and its self-check command) reports the source of each value group as `env`, `hub`, `local` or `unset`: names only, never values or the Hub file path.

Section key names and the source report of each child are in its contract in [mcps/](mcps/).

## Tests and documentation to update

- Follow the child repository's test instructions. Cover tool schemas, success and failure results, and side effects without calling a real upstream service from unit tests.
- Keep the Hub contract `docs/mcps/<server-id>.md` current: runtime and start command, config names, tool aliases, input/output behavior, read/write effects, read-only check, and diagnostics.
- Update the child config example when launch or configuration requirements change. Update `scripts/smoke.py` and its unit tests when the health tool or success payload changes.
- Update `docs/adding-a-child-mcp.md` only when shared Hub onboarding steps change; update `docs/client-rules.md` or Hub prompt instructions when agent behavior changes.
- After changing Hub files, run the definition-of-done commands in [AGENTS.md](../AGENTS.md). For child-only changes, also run that child repository's required checks and then the Hub smoke check when local private configuration and the upstream service are available.
