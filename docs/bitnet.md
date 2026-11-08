# Local BitNet extension

The optional `bitnet` extension runs a local 1-bit quantized model (Microsoft BitNet b1.58, 2B parameters) through `llama-server` on loopback. It adds three local AI capabilities without sending prompt tokens outside the machine:

1. **Deterministic planning assistant:** When a client calls `hub__plan` or an issue investigation job is created, the planner inspects the natural-language task to detect the best playbook, the target environment, candidate child MCPs, and absolute time windows (`since` / `until`), filling missing fields before execution.
2. **Result condenser:** Automatically condenses oversized read-only child tool results (above `settings.bitnet.condenseMinChars`, default 6,000 chars) before returning them to AI clients or storing them in investigation jobs. It uses extractive line deduplication and local model summarization to preserve key lines, errors, and IDs while cutting token overhead. The original full output is saved in an in-memory LRU store and can be retrieved via `hub__get_full_result` when referenced by `result_id`.
3. **Offline draft summarizer:** In cross-system issue investigation jobs, if no external cloud model provider (OpenAI, Anthropic, Gemini) is configured, specialists for logs and SQL execute deterministic read-only queries, and the local BitNet model generates an offline draft summary (`Draft (local model):\n...`) marked as `partial`.

## Enabling the extension

The extension is disabled by default. You can enable it in:
- The desktop status app under **⚙ Settings → Hub → Local BitNet AI**.
- Or via the dashboard settings API (`POST /dashboard/api/settings/bitnet`).

When enabled, the Hub starts the background supervisor (`Sidecar`), which ensures `llama-server.exe` is running on `127.0.0.1:8766`.

## Requirements

- **Operating System:** Windows 10/11 x64.
- **Build Tools:** Visual Studio 2022 Build Tools with Clang (C++ Clang Compiler for Windows) or standalone Clang >= 18.
- **CMake:** cmake >= 3.22 (available in `PATH`).
- **Disk Space:** ~1.5 GB in `vendor/bitnet/` (build artifacts and the downloaded model weights).
- **Memory:** 2–3 GB RAM while the server is active.

## Installation path

The runtime and model live under:
- `vendor/bitnet/` (untracked, excluded by `.gitignore`).
- `vendor/bitnet/bin/llama-server.exe`: compiled server binary.
- `vendor/bitnet/models/BitNet-b1.58-2B-4T/`: model GGUF file.

The installer script `scripts/install_bitnet.ps1` builds the server and downloads the model weights idempotently. During preflight (`python -m mcp_hub.preflight --install`), if `bitnet` is enabled and binaries are missing, preflight calls the installer automatically.

## Settings

Settings are configured under the `settings.bitnet` block in `config/private/hub.json`:

| Group | Key | Default | Description |
|---|---|---|---|
| `bitnet` | `port` | `8766` | llama-server HTTP port on `127.0.0.1`. |
| `bitnet` | `threads` | `0` | CPU threads (0 = auto: half of CPU cores). |
| `bitnet` | `model` | `"BitNet-b1.58-2B-4T"` | Model directory name. |
| `bitnet` | `maxRestarts` | `3` | Max restarts before marked unavailable. |
| `bitnet` | `planTimeoutSeconds` | `3` | Timeout for planning assistant hints. |
| `bitnet` | `condenseTimeoutSeconds` | `8` | Timeout for result condensation. |
| `bitnet` | `condenseMinChars` | `6000` | Minimum character length to trigger condensation. |
| `bitnet` | `fullResultTtlSeconds` | `1800` | Lifetime in seconds for full result retrieval. |
| `bitnet` | `fullResultMaxEntries` | `50` | Maximum full results cached in memory. |

See [settings.md](settings.md) for full setting definitions.

## Audit events

BitNet operations log structured audit events to `logs/mcp-hub.jsonl`:
- `bitnet.sidecar.state`: Sidecar lifecycle transitions (`off`, `installing`, `starting`, `ready`, `restarting`, `unavailable`, `stopped`).
- `tool.result.condensed`: Result condenser activity (`kept_lines`, `total_lines`, `source`, `duration_ms`).
- `full_result.stored`, `full_result.retrieved`, `full_result.expired`: Full result store actions.

See [security-and-logging.md](security-and-logging.md#event-reference-logsmcp-hubjsonl) for logging details.

## Fallback behavior

BitNet is designed with zero-downtime graceful fallbacks:
- If the sidecar is stopped, installing, or unavailable, `hub__plan` falls back to deterministic rule matching.
- Result condensing falls back to extractive rule-based deduplication without calling the local model.
- If condensation fails or times out, the raw uncompressed result is returned untouched.
- If the local summarizer is unavailable, issue investigations fall back to the standard unconfigured message.

## Uninstalling

To uninstall BitNet:
1. Turn off the extension in the desktop app or dashboard settings.
2. Stop the Hub (`scripts/hubctl.ps1 stop`).
3. Delete the `vendor/bitnet/` folder.
