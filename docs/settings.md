# Hub settings

The values an operator may want to change live in one optional `settings` object in the Hub config (`config/private/hub.json`, or the file `MCP_HUB_CONFIG` names). Every default is defined once, in `src/mcp_hub/settings.py`. The Hub reads the block at start and passes each value to the object that uses it, so a change applies at the next Hub start (`hubctl restart`).

```json
{
  "version": 1,
  "settings": {
    "http": {"port": 8765},
    "jobs": {"maxActive": 3, "maxJobSeconds": 1200},
    "logs": {"maxBytes": 10485760}
  }
}
```

- **Every group and key is optional.** A missing one keeps its default.
- **Unknown groups or keys are refused.** So is a value of the wrong type or out of range. The Hub does not start; `config.load.failed` in `logs/mcp-hub.jsonl` names the key, so a typo never silently keeps a default.
- Keys are camelCase, like the rest of the Hub config.
- **Whole numbers:** a key whose default is a whole number takes only whole numbers.
- **Other numbers:** every other numeric key takes any finite number.
- **Text and lists:** `policy.mode` takes one of the listed words; `policy.healthCheckTools` takes a list of tool names.
- All values must be positive, except where the table gives another range.

| Group | Key | Default | What it changes |
|---|---|---|---|
| `http` | `port` | `8765` | The shared service's port on `127.0.0.1` (1024 to 65535; the host is always `127.0.0.1`). The service writes it to `service.json` and the connection file, so the bridge, `hubctl`, and the desktop app follow it. **HTTP clients configured with `http://127.0.0.1:8765/mcp` must be updated by hand.** |
| `http` | `shutdownDrainSeconds` | `20` | How long a stopping Hub waits for open requests before it closes them. |
| `children` | `reconnectInitialSeconds` | `1` | The first delay before a dropped child MCP is started again; it doubles after each failed attempt. |
| `children` | `reconnectMaxSeconds` | `60` | The longest delay between reconnect attempts. A session that stayed up this long resets the delay. |
| `logs` | `maxBytes` | `5242880` (5 MiB) | Size at which `logs/mcp-hub.jsonl` rotates, and the cap of each child's stderr log. |
| `logs` | `backups` | `3` | Rotated audit logs kept (`.1` to `.3`); `0` or more. |
| `cache` | `maxEntries` | `256` | Read-only results the Hub keeps in memory for their `cacheSeconds`. |
| `cache` | `maxBytes` | `4194304` (4 MiB) | Total size of those cached results. |
| `dashboard` | `pairingSeconds` | `120` | How long a pairing link from `hubctl open` or the desktop app stays valid (it works once). |
| `dashboard` | `sessionHours` | `12` | How long a paired browser session lasts. |
| `jobs` | `maxActive` | `2` | Investigations that run at the same time. |
| `jobs` | `maxQueued` | `20` | Investigations that may wait for a slot; more are refused with a clear error. |
| `jobs` | `maxJobSeconds` | `900` | The time window of one investigation; the notes name it (for example "15-minute"). |
| `jobs` | `maxChildCalls` | `30` | Child tool calls one investigation may make. |
| `jobs` | `retentionDays` | `30` | Days finished jobs and their encrypted evidence are kept. |
| `jobs` | `approvalCodeMinutes` | `10` | Lifetime of a one-time write-approval code. |
| `jobs` | `maxWindowHours` | `168` | Longest `since` to `until` span accepted in a plan or investigation job (in hours). |
| `batch` | `maxCalls` | `8` | Most calls in one `hub__batch` (at least 2); the tool's schema follows it. |
| `batch` | `maxPerChild` | `4` | Most calls of one batch that one child serves at the same time. |
| `batch` | `timeoutSeconds` | `120` | Time the whole batch may take; a call still running is cancelled and reported. |
| `policy` | `mode` | `"enforce"` | How the Hub applies a client's `hub__plan`: `enforce` refuses calls that break it, `warn` runs them with a warning, `off` needs no plan. See [usage-policy.md](usage-policy.md). |
| `policy` | `maxPlans` | `256` | Plans kept in memory; the least recently used one is dropped, and a call naming it must plan again. |
| `policy` | `healthCheckTools` | `jira__check_connection`, `pr__check_bitbucket_connection`, `source__check_connection`, `sql__check_connection` | Tool names treated as connection checks: allowed only after a failed call of the plan, or with `connection_checks: true`. A list of strings. |
| `bitnet` | `port` | `8766` | llama-server port on `127.0.0.1` (`1024` to `65535`). |
| `bitnet` | `threads` | `0` | CPU threads for llama-server; `0` means auto (half the CPU count, at least 1). |
| `bitnet` | `model` | `"BitNet-b1.58-2B-4T"` | Model folder name under `vendor/bitnet/models/`. |
| `bitnet` | `maxRestarts` | `3` | Sidecar restarts before marked unavailable. |
| `bitnet` | `planTimeoutSeconds` | `3` | Time budget in seconds for `PlanAssistant.hints`. |
| `bitnet` | `condenseTimeoutSeconds` | `8` | Time budget in seconds for `ResultCondenser.condense`. |
| `bitnet` | `condenseMinChars` | `6000` | Minimum tool result text length to condense. |
| `bitnet` | `fullResultTtlSeconds` | `1800` | Lifetime in seconds of a stored original result. |
| `bitnet` | `fullResultMaxEntries` | `50` | Maximum number of original results kept in memory (LRU). |
| `bitnet` | `releaseRepository` | `"Ninh-Duong/mcp-hub"` | GitHub repository (`owner/name`) whose Release carries the prebuilt BitNet runtime and model manifest that the desktop app installs. |
| `bitnet` | `downloadTimeoutSeconds` | `60` | Seconds without data before a BitNet download fails. |
| `bitnet` | `minFreeBytes` | `3221225472` | Free disk space in bytes needed under `vendor/` before a BitNet install starts (3 GiB). |

## What stays in code

Values that are part of a contract, not a preference, stay as named constants next to the code that uses them. They are documented there.

- Protocol and security limits: the 64-character token, the 128-character denied-name cap, redaction margins, and the result size of `hub__batch`.
- The dashboard's page sizes.
- The activity UI's internal queue sizes.

The desktop app has its own preferences (`%APPDATA%\mcp-hub-status\desktop.json`, see [desktop-app.md](desktop-app.md)). It never reads the Hub config.

`scripts/service_launcher.pyw` runs before the Hub environment is loaded, so it keeps its own 5 MiB cap for `logs/mcp-hub-service.err`.
