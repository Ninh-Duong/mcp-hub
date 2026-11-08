# Track C: sidecar, installer, desktop switch

Status: approved · Branch: `feat/bitnet-runtime` (worktree `../mcp-hub-c`, from `master` after track 0 is merged) · Parallel with A and B · Read [00-overview.md](00-overview.md) first.

Goal: the user can switch BitNet on in the desktop app. The first start after that installs the runtime into `vendor/bitnet/`, and a supervised `llama-server` runs on 127.0.0.1.

## Owns

- `src/mcp_hub/bitnet/sidecar.py` (new)
- `scripts/install_bitnet.ps1` (new), `src/mcp_hub/preflight.py`
- `src/mcp_hub/hub_settings.py`, `src/mcp_hub/dashboard/settings_api.py`
- `src/mcp_hub/desktop/hub_api.py`, `src/mcp_hub/desktop/hub_page.py`, `src/mcp_hub/desktop/app.py` (the switch handler only)
- `src/mcp_hub/dashboard/locales/en.json`, `src/mcp_hub/dashboard/locales/vi.json`
- `docs/desktop-app.md` (switch section), `docs/reference.md` (the new route only)
- the matching tests

Use, but do not change: `mcp_hub.extensions` (`BITNET`, `EXTENSIONS_KEY`, `saved_extensions`), `mcp_hub.settings.BitnetSettings`, `mcp_hub.bitnet.client.BitnetClient`.

## Layout written by the installer (never committed)

```text
vendor/bitnet/
  src/            git clone --recursive https://github.com/microsoft/BitNet
  venv/           uv venv for setup_env.py (never the Hub's .venv)
  models/<model>/ ggml-model-i2_s.gguf
  install.json    {"server": "<repo-relative path to llama-server.exe>", "model": "<repo-relative gguf path>", "version": 1}
```

## Tasks

### [ ] T301 `sidecar.py`, the llama-server supervisor
```python
class Sidecar:
    def __init__(self, root: Path, settings: BitnetSettings, audit: AuditLog, *,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen, client: BitnetClient None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None: ...
    state: str                          # "off" | "installing" | "starting" | "ready" | "restarting" | "unavailable" | "stopped"
    async def start(self) -> None: ...  # background task; returns immediately
    async def stop(self) -> None: ...   # terminate, wait 5 s, kill; state "stopped"
    def status_fields(self) -> dict[str, Any]:  # {"bitnet": {"state": state, "restarts": n}}
```
- `root` is the repo root. Read `vendor/bitnet/install.json` with `asyncio.to_thread`. If it is missing, the state is `unavailable` with `error_type="NotInstalled"`.
- Start the process with `asyncio.to_thread(popen, [server, "-m", model, "--host", "127.0.0.1", "--port", str(port), "-t", str(threads), "-c", "4096"], stdout=DEVNULL, stderr=<log file>, creationflags=CREATE_NO_WINDOW on Windows)`.
  - `threads` is `max(1, (os.cpu_count() or 2) // 2)` when the setting is 0.
  - Write stderr to `logs/children/bitnet.log`: use the same directory as the child logs, which `HubRuntime.child_log_dir` exposes; take it as a constructor argument `log_dir`.
- Poll `client.healthy()` every 0.5 s for up to 60 s, then set `ready`.
- A watch loop checks every 5 s. If the process exited or health fails 3 times in a row, restart it up to `max_restarts` times; after that the state is `unavailable`.
- Audit every state change as `bitnet.sidecar.state` with `state`, `restarts` and `error_type` only. Document the event in `docs/security-and-logging.md` and `docs/reference.md`; these two docs are shared with track 0, so append to them only.
- Tests (fake popen, fake client, fake sleep):
  - not installed → `unavailable`;
  - healthy → `ready`;
  - crash → restart → `ready`;
  - crash past `max_restarts` → `unavailable`;
  - `stop()` terminates and then kills;
  - nothing blocks the loop (all popen calls go through `to_thread`; assert with a mock).

### [ ] T302 Installer + preflight hook
- `scripts/install_bitnet.ps1 [-Model BitNet-b1.58-2B-4T]` is idempotent and uses repo-relative paths, resolved from `$PSScriptRoot\..`:
  1. If `vendor/bitnet/install.json` exists and both of its paths exist, print `bitnet: ready` and exit 0.
  2. Check the requirements and print what is missing:
     - `git`;
     - `cmake --version` ≥ 3.22;
     - `clang --version` ≥ 18, or VS 2022 with the ClangCL toolset found through `vswhere.exe -products * -requires Microsoft.VisualStudio.Component.VC.Llvm.Clang`.
     - If something is missing, try `winget install --id Kitware.CMake -e --silent`. For VS, print the exact `winget install Microsoft.VisualStudio.2022.BuildTools --override "--add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Llvm.Clang --add Microsoft.VisualStudio.Component.VC.Llvm.ClangToolset --passive"` command, run it, and re-check.
  3. Run `git clone --recursive --depth 1 https://github.com/microsoft/BitNet vendor/bitnet/src` (skip it if the folder exists).
  4. Run `uv venv vendor/bitnet/venv --python 3.11`, then `uv pip install --python vendor/bitnet/venv -r vendor/bitnet/src/requirements.txt`.
  5. Download the model: `vendor/bitnet/venv/Scripts/huggingface-cli download microsoft/BitNet-b1.58-2B-4T-gguf --local-dir vendor/bitnet/models/<Model>`.
  6. Build: run `vendor/bitnet/venv/Scripts/python setup_env.py -md <models dir> -q i2_s` from `vendor/bitnet/src`.
  7. Find `llama-server.exe` under `vendor/bitnet/src/build` (`Get-ChildItem -Recurse -Filter llama-server.exe Select-Object -First 1`), then write `install.json` with repo-relative paths.
  8. On any failure, print `bitnet: unavailable (<step>: <short reason>)` and exit 2. Never print environment variables or tokens.
- `preflight.py`:
  - Add `check_bitnet(install: bool, *, runner=run_hidden, saved=saved_extensions) -> CheckResult None`.
  - It returns `None` when `BITNET` is not in `saved()`.
  - Without `--install`, it only reports whether `install.json` exists.
  - With `--install`, it runs `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_bitnet.ps1 -Model <settings.bitnet.model>` through `runner` with a timeout of 3600 s, and maps exit 0 to ok and anything else to not-ok, with the last output line as the detail.
  - Append the result in `run_preflight`. A not-ok result must **not** change the exit code that the start path treats as fatal. Read how `main` computes its return code and keep BitNet failures non-fatal.
  - Load the model name with `load_settings()` inside `main`, not at import.
- Tests (`tests/test_preflight.py`, fake runner):
  - extension off → `None`;
  - install ok;
  - install exit 2 → not ok, and preflight's exit code is unchanged;
  - no `--install` → report only.
- `docs/bitnet.md` is written by track D. Here, add one line to `docs/packaging-and-qc.md` or `docs/manual-setup.md`, wherever preflight steps are listed: "When the BitNet extension is on, preflight also runs `scripts/install_bitnet.ps1`."

### [ ] T303 Settings write path
- `hub_settings.py`:
  - Add `def set_extension(name: str, enabled: bool, config_path=None) -> None`. It adds or removes one name in the `extensions` list, keeps the others, keeps the order, and removes the key when the list becomes empty. It raises `ConfigError("Unknown extension")` when the name is not in `KNOWN_EXTENSIONS`.
  - `set_activity_ui(enabled)` becomes `set_extension(ACTIVITY_UI, enabled)`.
  - `HubSettings` gets `bitnet: bool`, set in `read_settings` from `saved_extensions`.
- `dashboard/settings_api.py`:
  - `settings_payload` adds `"bitnet": settings.bitnet`;
  - new route `POST /dashboard/api/settings/bitnet` with body `{"enabled": bool}`, which calls `set_extension(BITNET, enabled)` through `_write`.
  - Document the route in `docs/reference.md`.
- Tests:
  - `set_extension` keeps `activity-ui` when it adds `bitnet`;
  - removing the last entry removes the key;
  - an unknown name raises;
  - the route gives 200 with the payload containing `bitnet`, 400 for a bad body, and 409 with `MCP_HUB_CONFIG` set.

### [ ] T304 Desktop switch
- `desktop/hub_api.py`: `set_bitnet(enabled)` posts to `/dashboard/api/settings/bitnet`, and the parsed settings get `bitnet: bool` (default `False` when the key is missing, for older Hubs).
- `desktop/hub_page.py`: add a row built like the tracking row:
  - label key `desktop.settings.hub.bitnet`;
  - hint key `desktop.settings.hub.bitnetHint`;
  - a status caption from the Hub status field `bitnet.state`, with keys `desktop.bitnet.state.<off|installing|starting|ready|restarting|unavailable|stopped>`;
  - the row is disabled when the settings are not `editable`.
- `desktop/app.py`: `set_bitnet(enabled)` mirrors `set_tracking`. After a change, show the existing "restart the Hub to apply" message (reuse its key if there is one; grep the tracking flow).
- Locales: add every new key to both `en.json` and `vi.json`, with real Vietnamese text in `vi.json`.
  - en `desktop.settings.hub.bitnet`: "BitNet (local AI)"
  - en `desktop.settings.hub.bitnetHint`: "Plans requests and condenses large results on this PC. The first start installs about 1.5 GB into vendor/bitnet."
- Follow `docs/desktop-app.md`, including its design keywords. Update its Settings → Hub section.
- Tests: follow the existing desktop tests that cover the tracking row and its API call (`tests/test_desktop*.py`). Cover the switch call, the disabled state, the status caption mapping, and a payload without `bitnet` meaning off.

## Acceptance for track C
- Switching on in the app writes `"extensions": [..., "bitnet"]` and keeps `activity-ui`.
- `preflight --install` with the extension on installs, or reports `unavailable` without failing the start path.
- The sidecar reaches `ready` against a fake server in tests.

## Blocked

- (none)

## Progress log

- 2026-11-08 Claude (Opus): track written. Next: T301.
