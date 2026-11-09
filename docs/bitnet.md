# Local BitNet extension

The optional `bitnet` extension runs a local 1-bit quantized model (Microsoft BitNet b1.58, 2B parameters) through `llama-server` on loopback. It adds three local AI capabilities without sending prompt tokens outside the machine:

1. **Deterministic planning assistant:** When a client calls `hub__plan` or an issue investigation job is created, the planner inspects the natural-language task to detect the best playbook, the target environment, candidate child MCPs, and absolute time windows (`since` / `until`), filling missing fields before execution.
2. **Result condenser:** Automatically condenses oversized read-only child tool results (above `settings.bitnet.condenseMinChars`, default 6,000 chars) before returning them to AI clients or storing them in investigation jobs. It uses extractive line deduplication and local model summarization to preserve key lines, errors, and IDs while cutting token overhead. The original full output is saved in an in-memory LRU store and can be retrieved via `hub__get_full_result` when referenced by `result_id`.
3. **Offline draft summarizer:** In cross-system issue investigation jobs, if no external cloud model provider (OpenAI, Anthropic, Gemini) is configured, specialists for logs and SQL execute deterministic read-only queries, and the local BitNet model generates an offline draft summary (`Draft (local model):\n...`) marked as `partial`.

## Install from the desktop app

Users never run a command or install build tools. In the desktop status app, **⚙ Settings → Hub** shows **Install BitNet** while BitNet is not installed:

1. The app calls `POST /dashboard/api/bitnet/install` (admin token). The Hub answers `202` at once and installs on a worker thread (`bitnet/installer.py`); a second call while one runs starts nothing, and a call when BitNet is installed answers `409`.
2. The installer checks the platform (Windows x64) and the free space (`settings.bitnet.minFreeBytes`, 3 GiB), then reads the GitHub Release of this Hub version (`v<version>`, or the latest Release when this version was never released) from `settings.bitnet.releaseRepository`.
3. That Release carries `bitnet-manifest.json` and `bitnet-runtime-win-x64.zip`, built by CI (see [Prebuilt runtime](#prebuilt-runtime)). The manifest names the runtime's SHA-256 and size, and the model file, its pinned Hugging Face URL, SHA-256, and size.
4. Each file is streamed into `vendor/bitnet/` as `<name>.part`, hashed while it downloads, never allowed past its size, and kept only when its SHA-256 matches; a file already present with the right hash is not downloaded again. The zip is unpacked into `vendor/bitnet/runtime/` (paths that would leave the folder are refused). `install.json` is written last.
5. The Hub switches BitNet on in `hub.json`. If BitNet already runs in this Hub, the sidecar starts at once; otherwise the app shows **Restart Hub**, because the planner and condenser join the Hub only at start.

The app's dot for BitNet turns green only when llama-server answers `/health` ([desktop-app.md](desktop-app.md#bitnet-install)).

**Status.** `GET /dashboard/api/status` always has a `bitnet` block, also while the extension is off:

```json
{"bitnet": {"installed": false, "enabled": false, "state": "installing", "restarts": 0, "restart_needed": false,
            "install": {"running": true, "step": "model", "progress": 42, "error": null}}}
```

`state` is `not_installed` or `off` without the extension, `installing` during an install, and otherwise the sidecar's state. `step` is `prepare`, `runtime`, `model`, or `extract`; `progress` is a percent while a file downloads. `error` is a short code, never a path or URL: `platform`, `disk`, `network`, `release`, `checksum`, `extract`, `cancelled` (the Hub stopped), or `unexpected`.

Without the extension, `service.py` installs only this install state and route (`bitnet/setup.py`); no planner, condenser, or sidecar is added, so tool calls stay route → audit → child.

`python -m mcp_hub.preflight` (and `hubctl start`) only reports whether BitNet is installed; a start never downloads it.

## Enabling the extension

The extension is disabled by default. The install switches it on; afterwards it can be turned off or on in:
- The desktop status app under **⚙ Settings → Hub → BitNet (local AI)** (shown once BitNet is installed).
- Or via the dashboard settings API (`POST /dashboard/api/settings/bitnet`).

When enabled, the Hub starts the background supervisor (`Sidecar`), which ensures `llama-server.exe` is running on `127.0.0.1:8766`.

## Requirements

- **Operating System:** Windows 10/11 x64, with a CPU that has AVX2.
- **Disk Space:** about 1.3 GB in `vendor/bitnet/` (the runtime and the model); the install needs 3 GB free.
- **Memory:** 2–3 GB RAM while the server is active.
- **Network:** `api.github.com`, `github.com` (the Release), and `huggingface.co` (the model) during the install only.

## Installation path

The runtime and model live under `vendor/bitnet/` (untracked, excluded by `.gitignore`):
- `vendor/bitnet/runtime/llama-server.exe`: the prebuilt server with the DLLs it needs, the Visual C++ runtime included.
- `vendor/bitnet/models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf`: the model.
- `vendor/bitnet/install.json`: `{server, model, version}`, both paths relative to the Hub folder. The sidecar starts only when both files exist.

## Prebuilt runtime

`.github/workflows/desktop-release.yml` runs `scripts/build_bitnet_runtime.py` in the same job that builds the desktop installer:

- It checks out `microsoft/BitNet` at a pinned commit and downloads the model at a pinned Hugging Face revision, checking its SHA-256 (`BITNET_COMMIT`, `MODEL_REVISION`, `MODEL_SHA256`).
- It builds llama-server with that repository's `setup_env.py -q i2_s` (ClangCL on the Windows runner).
- It zips llama-server with every DLL it imports that Windows does not provide (read with `dumpbin /dependents`; the Visual C++ runtime is always shipped).
- It unpacks the zip into an empty folder, starts llama-server there with the model, and requires `/health` to answer 200 before it writes `bitnet-manifest.json` and the zip's `.sha256`.

The three files are attached to the Release with the app's installer (see [versioning.md](versioning.md#make-a-release)). The model is not attached: the manifest points to it on Hugging Face (MIT license). To move to a newer BitNet or model, change the pinned values and release a new version.

`scripts/install_bitnet.ps1` is the older build-from-source path for developers. It needs git, CMake, Clang, and uv, and it is not used by the app or the Hub.

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
| `bitnet` | `releaseRepository` | `"Ninh-Duong/mcp-hub"` | GitHub repository whose Release carries the prebuilt runtime and manifest. |
| `bitnet` | `downloadTimeoutSeconds` | `60` | Seconds without data before a download fails. |
| `bitnet` | `minFreeBytes` | `3221225472` | Free space needed before an install starts (3 GiB). |

See [settings.md](settings.md) for full setting definitions.

## Audit events

BitNet operations log structured audit events to `logs/mcp-hub.jsonl`:
- `bitnet.sidecar.state`: Sidecar lifecycle transitions (`off`, `starting`, `ready`, `restarting`, `unavailable`, `stopped`).
- `bitnet.install.started`, `bitnet.install.step`, `bitnet.install.finished`, `bitnet.install.failed`, `bitnet.install.enable_failed`: the install from the desktop app (step names and error codes only).
- `tool.result.condensed`: Result condenser activity (`kept_lines`, `total_lines`, `source`, `duration_ms`).
- `full_result.stored`, `full_result.retrieved`, `full_result.expired`: Full result store actions.

See [security-and-logging.md](security-and-logging.md#event-reference-logsmcp-hubjsonl) for logging details.

## Fallback behavior

BitNet is designed with zero-downtime graceful fallbacks:
- If BitNet is not installed, is installing, or the sidecar is stopped or unavailable, `hub__plan` falls back to deterministic rule matching.
- Result condensing falls back to extractive rule-based deduplication without calling the local model.
- If condensation fails or times out, the raw uncompressed result is returned untouched.
- If the local summarizer is unavailable, issue investigations fall back to the standard unconfigured message.

## Uninstalling

To uninstall BitNet:
1. Turn off the extension in the desktop app or dashboard settings.
2. Stop the Hub (`scripts/hubctl.ps1 stop`).
3. Delete the `vendor/bitnet/` folder.
