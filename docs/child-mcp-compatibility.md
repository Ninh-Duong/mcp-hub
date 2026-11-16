# Child MCP compatibility contract

This is the Hub's source of truth for changing a child MCP while keeping it usable through `mcp-hub`. Read it with any agent instructions in the child repository, then use that child's contract in [mcps/](mcps/) for its current tools and configuration. This document describes shared requirements; it does not replace child-specific instructions.

## Process and transport

- The Hub launches each child as a separate local process over MCP `stdio`, using `command`, an argument list, and the configured child `cwd`. Do not require a shell command string or assume the Hub's working directory.
- Keep stdout exclusively for MCP protocol messages. Write diagnostics to stderr or the child's own log file. The Hub keeps child stderr in `logs/children/<server-id>.log` (size-capped), which only the user reads; never write secrets there.
- Keep startup predictable and tool calls bounded. `timeoutSeconds` is a positive finite number of seconds for each routed tool call; it is not the client application's Hub startup timeout.
- Keep transport as `stdio`. The Hub allowlists exact child tool names and exposes the configured aliases, normally `<prefix>__<tool>`.

## Tools and behavior

- Keep tool names and input schemas stable when possible. If a name or required input changes, update the Hub allowlist and the child contract in the same change.
- Return valid MCP results with documented input and output shapes. Mark failures with the MCP error flag; do not rely on message prefixes to signal failure.
- Set tool annotations to match behavior: read-only tools must not write, and every tool that writes must declare that accurately and document what and where it writes.
- Keep a read-only health/check tool that verifies the required upstream connection without changing data. Register it in `scripts/smoke.py` and make its success check validate the actual success result.
- Start, complete the MCP handshake, and list every tool when the account is not filled in yet. Do not exit on missing credentials: each tool that needs them returns an error result saying they are not configured (the Hub smoke check shows that as `WARN`, a start failure as `FAIL`). Setup downloads and checks children without any account.
- Never put credentials or private configuration values in tool descriptions, tool results, errors, or logs. Return upstream data only when it is part of the requested tool behavior.
- **`isError`.** Set the MCP error flag on failures. The Hub's failure check also reads an error prefix in the first text block (`_ERROR_PREFIXES`), so a result that starts with `Error:` counts as failed too. A result with `isError` is never condensed.
- **`outputSchema`.** A tool that declares an `outputSchema` must return `structuredContent` that validates against it. The Hub forwards such a result unchanged: it is never condensed, and batch packing skips it. The Hub also does not check the result against the schema.
- **`notifications/tools/list_changed`.** A child that adds, removes, or changes a tool may send this notification. The Hub relists that child's tools on it (no reconnect), rebuilds its routes, and announces the change to clients once per debounce window. A child whose tool definition changes without a notification is seen at its next reconnect. Changing a definition is audited (`tool.definition.changed`, warn mode) and the tool keeps running.
- **Arguments.** The Hub checks each call's arguments against the input schema the child published (warn mode, `tool.arguments.invalid`) and still runs the call, so a schema the child publishes should match what it accepts.

## Configuration and secrets

- Document required environment variable names and private config key paths, never their values. Hub templates leave secrets as empty strings (see [Configuration sources](#configuration-sources)); use `<SET_BY_USER>` only for a value the child cannot run without and has no local fallback for.
- The child receives only the MCP SDK's safe defaults, names in `inheritEnv`, and values in its configured `env`. Do not assume access to the Hub's private files or the user's entire environment.
- Keep local account files ignored by Git. Do not add credentials to source, fixtures, examples, test output, or the child contract.
- Child errors may be forwarded to the agent or included in Hub error logs. Avoid including secret values in errors; Hub log redaction is defense in depth, not permission to emit secrets.

## Configuration sources

Every child runs both standalone and under the Hub, with one rule for where its configuration comes from:

1. **Standalone**: the child reads its own local files (`.env`, `config/*.env`), as documented in its repository.
2. **Under the Hub**: the child config's `env.CENTRAL_CONFIG_PATH` points to that same private file, `config/private/mcps/<server-id>.json`, written relative to the child repo (e.g. `../mcp-hub/config/private/mcps/<server-id>.json`). The child reads only its own section of it (`openobserve`/`seq`, `jira`, `bitbucket`, `mssql`).
3. **Priority, per value**: variables already in the child's environment (the config's `env`) > the Hub section > the child's local files > defaults. An empty string counts as unset, so a section left empty keeps the local files in use.
4. A relative `CENTRAL_CONFIG_PATH` resolves against the child repo root, not the process working directory.
5. A missing or unreadable Hub file is logged by the child as a warning, and the local files are used. A child never writes the Hub file; its own login/setup commands keep writing its local files.
6. Hub templates leave account values as empty strings. `<SET_BY_USER>` would make the Hub skip the child as `credentials required` even when its local files hold an account.
7. The child's read-only check (and its self-check command) reports the source of each value group as `env`, `hub`, `local` or `unset` (a child may add `default` for a group that only has built-in defaults): names only, never values or the Hub file path.

Section key names and the source report of each child are in its contract in [mcps/](mcps/).

## Declaring required settings

A child can list the settings it needs in `hub-requirements.json` at the root of its own repository. The Hub reads the file from the child's folder (its `cwd`, or the sibling `../<server-id>`), and the desktop app shows any gap; the child still starts.

```json
{
  "version": 1,
  "groups": [
    {
      "label": "OpenObserve or Seq",
      "id": "log_account",
      "anyOf": [
        ["openobserve.default_env", "openobserve.dev.url", "openobserve.dev.token"],
        ["seq.server_url", "seq.api_key"]
      ]
    }
  ]
}
```

- Each group is satisfied when one of its `anyOf` alternatives has a filled value for every key. Keys are dotted paths into the child's private file (`config/private/mcps/<server-id>.json`).
- A value is empty when it is missing, `null`, blank, or `<SET_BY_USER>`. Numbers and booleans always count as filled.
- The file holds key names only. Never put values in it.
- `id` is optional: a lowercase name (`^[a-z][a-z0-9_]{0,63}$`) that the child's `config_status` tool reports on (see [Reporting configuration sources](#reporting-configuration-sources)). A malformed `id` makes the file `invalid`.
- No file means no requirements. A file that cannot be read or does not follow this format is reported as `invalid`, and the Hub still starts the child.
- The Hub audits `child_config_incomplete` (server ID and key names) when a child starts with a gap, and reports the gap in `config_incomplete` on `GET /dashboard/api/status`. The desktop app shows the same gap in the **Config** settings tab and in the Child MCPs card. A gap does not stop the child, because it may still read the value from its own local files.

## Reporting configuration sources

The Hub file is only one of a child's configuration sources ([Configuration sources](#configuration-sources)). So that the desktop app does not report a gap the child fills from its own local files, a child can offer a `config_status` tool:

- No input. Read-only, idempotent, no network or database call (`readOnlyHint: true`, `openWorldHint: false`).
- It returns one text block (or structured content) `{"groups": {"<group-id>": "env|hub|local|default|unset"}}`, one entry per `id` in its `hub-requirements.json`. Source words only, never values, paths, or emails.
- After each connect, the Hub calls it once in the background, waiting at most `children.configStatusTimeoutSeconds` ([settings.md](settings.md)), and keeps the answer until the next connect.
- A group the child reports as `env`, `hub`, `local`, or `default` is no longer a gap in `config_incomplete` on `GET /dashboard/api/status` or in `missing` on `GET /dashboard/api/settings`. `unset`, an error, a timeout, or another shape keeps the gap read from the Hub file.
- The Hub audits each answer as `child_config_status` (group IDs and source words, or `error_type`).
- `config_status` is for the Hub only: do not add it to a feature allowlist in `config/hub.example.json`, so agents never see it.

`mcp-server-logs` (`log_account`), `mcp-pr-companion` (`bitbucket`), and `mcp-sqlserver` (`mssql_login`) offer it.

## Tests and documentation to update

- Follow the child repository's test instructions. Cover tool schemas, success and failure results, and side effects without calling a real upstream service from unit tests.
- Keep the Hub contract `docs/mcps/<server-id>.md` current: runtime and start command, config names, tool aliases, input/output behavior, read/write effects, read-only check, and diagnostics.
- Update the child config example when launch or configuration requirements change. Update `scripts/smoke.py` and its unit tests when the health tool or success payload changes.
- Update `docs/adding-a-child-mcp.md` only when shared Hub onboarding steps change; update `docs/client-rules.md` or Hub prompt instructions when agent behavior changes.
- After changing Hub files, run the definition-of-done commands in [AGENTS.md](../AGENTS.md). For child-only changes, also run that child repository's required checks and then the Hub smoke check when local private configuration and the upstream service are available.
