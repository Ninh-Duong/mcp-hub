# mcp-hub

Use your AI client to review tickets, investigate bugs, review pull requests, inspect SQL Server data, and find code through one MCP connection. mcp-hub connects the client to your Jira, logs, Bitbucket, SQL Server, and registered source repositories.

**Agents: read [AGENTS.md](AGENTS.md) before working in this repository.**

## Get started

1. Clone this repository, then double-click **Setup.cmd** on Windows. Setup installs the Hub, its default child tools and the **mcp-hub Status** app. It copies a connection prompt for your AI client.
2. Configure your accounts yourself in the child repositories listed by Setup. Register your source project aliases and, for log investigations, their environments and log targets. See [configuration after setup](docs/parent-child-architecture.md#post-setup-credential-configuration) and [project setup](docs/operations/shared-agent-hub.md#2-configure-projects-and-agents). Do not paste credentials into chat.
3. Paste the copied connection prompt into your AI client and follow its instructions. For another client, follow [connect an AI client](docs/connecting-a-client.md).
4. Open **mcp-hub Status** from the Start Menu. Start the Hub if stopped. Reload the MCP connection in your client after a Hub restart.

For macOS, Linux or manual setup, see [manual setup](docs/manual-setup.md).

## Use it in your AI client

Write **Use mcp-hub** followed by your request. With the default settings, the AI asks which playbook to use before starting each new task. Choose one, provide any missing details, and let it follow the steps.

| You want to | Choose | What to provide |
|---|---|---|
| Explain a failed request | `rcid-bug` | Project alias, environment, rcid/request ID and time window |
| Investigate a Jira bug | `jira-ticket-bug` | Project alias, environment and issue key or URL; confirm the log time when needed |
| Review a ticket before implementation | `ticket-review` | Issue key; confirm BE/database/optional FE source aliases and any related tickets before comparison |
| Investigate a service's errors | `service-errors` | Project alias, environment, service and time window |
| Review a Bitbucket PR | `pr-review` | PR URL, or choose a PR from the local list |
| Find or understand code | `source-search` | Registered project alias and search text, or a relative file path |
| Review local git changes / new feature | `change-review` | Registered BE/DB/optional FE source aliases, relative `changed_paths`, and optional Jira `issue_key` |

Use your configured project aliases in the examples below. Specify a timezone for log times. If you omit the time window, the AI asks rather than guessing.

### Investigate one request

```text
Use mcp-hub to explain why request <REQUEST_ID> failed.
Project: <PROJECT_ALIAS>. Environment: stg.
Time: 2026-11-22 14:00 to 14:30, UTC+07:00.
```

### Investigate a Jira bug

```text
Use mcp-hub to investigate bug EX-123.
Project: <PROJECT_ALIAS>. Environment: stg.
Read the ticket first, then help me identify the cause.
```

### Investigate a service

```text
Use mcp-hub to investigate errors in <SERVICE>.
Project: <PROJECT_ALIAS>. Environment: stg.
Time: 2026-11-22 14:00 to 15:00, UTC+07:00.
```

### Review a ticket before implementation

```text
Use mcp-hub to review ticket EX-123 for missing or unclear requirements.
Ask me which BE, database and optional FE sources and related tickets to compare before reading them.
Separate confirmations for product, BE, FE and QC, and identify API/SP changes and related feature impact.
```

Source selection uses registered project aliases. If you name folders, confirm their registered aliases first. The DB source repository supplies SP/schema files; this review does not query a live database or change the ticket/code.

### Review a pull request

```text
Use mcp-hub to review <PR_URL>.
Focus on bugs and regressions. Cite the changed files and lines.
```

### Find code

```text
Use mcp-hub to find "retry" in project <PROJECT_ALIAS>, limited to *.py.
Show the relevant locations and explain the implementation.
```

### Review local git changes

```text
Use mcp-hub to review my local git changes for shared-caller impact, duplicate helpers, and potential bugs.
BE source: <BE_ALIAS>. Database source: <DB_ALIAS>.
Changed paths: src/orders/service.py, db/sp_update_order.sql.
```

You can also use `/mcp-hub <server-id> <request>` when your client has the command installed. Jira/PR summaries and other narrow reads can use a custom plan; the playbooks above follow their specific workflows.

## Why the AI asks again

Jira sync includes images and other attachments in its encrypted cache by default, within the configured per-file size limit. File names and availability are reported; interpreting PDF/Office content depends on the client's document support.

- **Choose a playbook:** each new task starts with your choice.
- **Provide missing details:** for example, a log time, service name or PR URL. Supply only what is missing.
- **Allow a cache refresh:** Jira sync and many PR readers can update the encrypted local cache. The AI asks before calling a write-capable tool. Declining means it must stop or explain the limitation.
- **Change the search scope:** a different environment, time window, project or request needs your confirmation and a new scoped plan.

The answer should include evidence and any missing information. A limited search is not an exact count of the whole repository. A PR review with no findings is not proof that the PR is safe. Access failures must be reported; the AI must not use another route to bypass them.

## When something does not work

| What you see | What to do |
|---|---|
| Hub stopped | Click **Start Hub** in mcp-hub Status. |
| No tools in the AI client | Reload its MCP connection. If still missing, follow [client connection](docs/connecting-a-client.md). |
| Missing account or project settings | Complete the settings in the owning child repository, then restart the Hub. |
| Upstream connection, access or timeout error | Ask the AI for the request ID and failing tool. Check the account/network/access for that system; use [diagnostics](docs/security-and-logging.md). |
| Workflow asks for input or denies a step | Provide the missing detail or follow the allowed next step. Ask for a new plan if your scope changed. |
| Installation files or a default child are missing | Run **Setup.cmd** again. |

Use **Check updates**, **Update** and **Retry** in mcp-hub Status for upgrades. See [updates and versions](docs/versioning.md).

## More information

- [Playbooks and their steps](docs/playbooks/README.md)
- [Workflow rules and permissions](docs/usage-policy.md)
- [Child tools and contracts](docs/mcps/README.md)
- [Architecture](docs/architecture.md), [settings](docs/settings.md), and [configuration templates](config/README.md)
- [Development and quality checks](docs/packaging-and-qc.md)

## License

The Hub core is MIT; see [LICENSE](LICENSE). The desktop app and installer are GPL-3.0-only; see [desktop license](src/mcp_hub/desktop/LICENSE). Dependency licenses are listed in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
