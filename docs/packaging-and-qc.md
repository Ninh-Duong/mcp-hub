# Source checkout bootstrap and package QC

`mcp-hub` is distributed as a source checkout. The client-facing stdio entrypoints remain `scripts/start.ps1` on Windows and `scripts/start.sh` on macOS/Linux; there is no separate binary, wheel, or ZIP to install.

## Support matrix

| Feature | Windows | macOS / Linux |
|---|---|---|
| Per-client stdio Hub (`start.ps1` / `start.sh`), child supervision, result cache, audit log | Yes | Yes |
| `scripts/smoke.py`, `scripts/package_qc.py` | Yes | Yes |
| Shared service (`hubctl.ps1`: Task Scheduler, `pythonw.exe`), stdio bridge, `http_headers.ps1` | Yes | No |
| Dashboard service controls (stop/restart run `hubctl.ps1`) | Yes | No (`503`) |
| Desktop status app (Qt; installer `mcp-hub-status-setup-<version>.exe`, or `python -m mcp_hub.desktop` from source) | Yes, per-user installer with Start Menu shortcut and an optional sign-in entry; frosted glass on Windows 10/11 through the acrylic accent | From source only: runs with a display, opaque (no glass), shows **Not installed** without the shared service; no installer, no sign-in entry, and Start Hub reports a failure |
| Investigation jobs (`hub__submit_issue` …; job blobs encrypted with DPAPI) | Yes | No: `hub__submit_issue` returns `investigation jobs require Windows DPAPI`, and agent status shows it |
| Preflight runtime install with winget | Yes | No: a missing runtime is reported; install it yourself |

Python 3.12 is required (`.python-version`, `requires-python = ">=3.12"`); the launchers use exactly that minor version and CI tests only 3.12.

## First launch

The launchers prepare the local runtime without prompting, so an AI client can start the Hub directly:

1. Find `uv` in the repo-local `.tools/` first, then on `PATH`; if missing, install the pinned uv version into the ignored repo-local `.tools/` directory (see below).
2. Read the required Python major/minor version from `.python-version`; install it through `uv` if it is unavailable.
3. Run `uv run --locked`, which creates or synchronizes the environment against `uv.lock` before executing the existing `mcp-hub` entrypoint.

The first launch needs network access to download missing tools, Python, and packages. It also needs write access to the checkout and uv-managed data/cache directories. Bootstrap status and errors go to stderr. If a download, install, or dependency sync fails, the launcher exits before the Hub starts. MCP clients continue to use the same server name, transport, command and arguments documented in [connecting-a-client.md](connecting-a-client.md).

### Pinned uv installer

When no uv is found, the launchers download the installer of one pinned uv release (`https://astral.sh/uv/<version>/install.ps1` or `install.sh`) to `.tools/`, compare its SHA-256 with the value stored in the script (`$uvVersion` / `$uvInstallerSha256` in `start.ps1`, `uv_version` / `uv_installer_sha256` in `start.sh`), and run it only on a match. A mismatch stops the bootstrap with `Bootstrap failed: ... does not match its pinned SHA-256; it was not run.` The pinned installer itself downloads that exact uv release. To move to a newer uv, update the version and both hashes together (download each installer and run `sha256sum`); `tests/test_bootstrap_scripts.py` checks that both launchers pin the same version.

### Dependency groups

`pyproject.toml` groups the runtime dependencies by purpose: core (MCP, HTTP service, validation), investigation providers (`openai`, `anthropic`, `google-genai`, imported only when a role makes its first request; a missing one is shown as "provider package not installed"), the desktop status app (`PySide6-Essentials`: QtCore, QtGui, and QtWidgets only, not the full PySide6), and child-shared packages (`httpx`, `python-dotenv`, `cryptography`) that `mcp-jira-sync` and `mcp-read-source` use from the Hub's venv. A status app run from source keeps Qt libraries open, so `hubctl` stops it before an environment sync and reopens it afterwards; the installed app has its own copy and is never stopped. The launchers sync all of them. The `dev` dependency group holds the QC tools (`ruff`, `coverage`); uv syncs it by default (`[tool.uv] default-groups`). The `build` group holds PyInstaller for the desktop installer and is synced only on request (`uv run --isolated --group build ...`).

### Desktop installer

`uv run --isolated --group build python scripts/build_desktop.py` builds `dist/mcp-hub-status-setup-<version>.exe` with PyInstaller and Inno Setup 6 (`winget install JRSoftware.InnoSetup` once). PyInstaller uses only the active Python and Windows system DLL paths so unrelated native DLLs from the calling shell cannot contaminate the bundle. It is not part of `package_qc.py`; `tests/test_bootstrap_scripts.py` checks the spec, the installer script, and the build commands offline. The installer shows the GPL-3.0 license (`LicenseFile` in `packaging/desktop/installer.iss`) and ships `THIRD-PARTY-NOTICES.md` next to the app. The app's PySide6-Fluent-Widgets resources are compiled into Python, so the spec adds no data files for them; a build with the Fluent controls grew the installer by about 2 MB.

**Releases.** `.github/workflows/desktop-release.yml` publishes the installer:
- **When it runs:** a push to `master` (a `plan` job on Linux reads `pyproject.toml` and checks that no Release `v<project version>` exists yet), a push of a tag `v<project version>` (the manual fallback, for example `v0.1.1`), or by hand. Nothing is built when the release already exists.
- **What it does, on `windows-latest`, when it builds:**
  1. runs `package_qc.py`;
  2. installs Inno Setup;
  3. builds with `build_desktop.py`, including its bundle self-check;
  4. writes the installer's SHA-256;
  5. builds the BitNet runtime with `scripts/build_bitnet_runtime.py`, which smoke-tests it and writes its manifest ([bitnet.md](bitnet.md#prebuilt-runtime));
  6. publishes a GitHub Release with the installer, the BitNet runtime, and their `.sha256` and manifest files (`gh release create`): for a push to `master` it creates the tag at the built commit (`--target`), for a tag push it verifies the tag (`--verify-tag`).
- **Run by hand:** it builds and checks without publishing.
- **Permissions:** only the build job gets `contents: write`; the `plan` job only reads.
- **One at a time:** a `concurrency` group queues runs, so two pushes in a row cannot publish the same version twice.

Numbering, the release steps, and how each part is upgraded are in [versioning.md](versioning.md). The build checks that the bundled package metadata matches `pyproject.toml` (the installed app reads the same metadata to compare its version with the latest Release). Raise the version in `pyproject.toml` before merging to `master` (the release is made then). The installer never installs over the same version, so users update only to a higher one. `build/` and `dist/` are ignored and never committed. Details: [desktop-app.md → Build the installer](desktop-app.md#build-the-installer).

## Offline package QC

After the initial dependency sync, run the standard QC command:

```sh
uv run --locked --python 3.12 python scripts/package_qc.py
```

It runs, in order, and stops at the first failure:

1. `python -m ruff check .` — lint: syntax errors (`E9`) and pyflakes (`F`: unused imports and variables, undefined names), configured in `pyproject.toml`.
2. `python -m coverage run -m unittest discover tests` — the unit tests, measured for line and branch coverage of `src/mcp_hub`.
3. `python -m coverage report --fail-under=85` — fails below 85 % (`COVERAGE_FAIL_UNDER`: the measured 88 % baseline rounded down to a multiple of 5). Raise it when coverage grows.
4. `python scripts/check_leaks.py` — machine paths, real emails, private path components.
5. `python scripts/check_code_standards.py` — English only, function length, every module imported by a test.
6. `python scripts/check_docs.py` — the docs match the code: links and anchors, the docs the Hub loads at runtime, audit events, environment variables and routes against [reference.md](reference.md), `hubctl` commands, settings keys against [settings.md](settings.md), and retired wording.
7. `python -m compileall -q src scripts tests`.

The unit tests run offline: child processes are fakes, upstream services, installers, and model SDKs are mocked, and no test writes to the checkout's `config/private/` or `logs/` (Hub subprocesses get temporary `MCP_HUB_PRIVATE_DIR` / `MCP_HUB_LOG_DIR`). `tests/test_stdio.py` always runs the Hub module over stdio with a fake child. Its launcher test, which runs `start.ps1` / `start.sh` in a fresh uv environment and therefore downloads every locked package (and uv itself when missing), runs only with `MCP_HUB_E2E=1`:

```powershell
$env:MCP_HUB_E2E = "1"; uv run --locked --python 3.12 python scripts/package_qc.py
```

QC does not call child MCPs or upstream services, so CI needs no private configuration or credentials. To add the live connection and read-only upstream checks, run:

```sh
uv run --locked --python 3.12 python scripts/package_qc.py --live-smoke
```

Use `--live-smoke` only when private Hub configuration and required upstream credentials are available (children without an account show `WARN`, which does not fail it). A missing private configuration is a local environment condition, not a failure of the offline package checks.

## Continuous integration

GitHub Actions (`.github/workflows/qc.yml`) runs package QC on Windows, macOS, and Ubuntu for pushes and pull requests, with a 20-minute job timeout. It installs Python 3.12, syncs strictly from the lockfile, and sets `MCP_HUB_E2E=1`, so CI also exercises the documented launch command end to end: listing tools and prompts and receiving Unicode tool output from a fake child, in a clean project environment. It needs no secrets or live services. On Ubuntu it first installs the system libraries Qt loads (`libegl1`, `libxkbcommon0`, `libfontconfig1`, `libdbus-1-3`), because the desktop window tests run on Qt's offscreen platform on every OS.

## Troubleshooting

- If the launcher reports that it cannot install `uv` or Python, check network access and write access to `.tools/` and uv's managed data directory. A checksum mismatch means the downloaded installer is not the pinned one; do not bypass it.
- If locked dependency synchronization fails, check access to the configured Python package index and retry the launcher or QC command.
- While the shared service runs on Windows it locks `.venv/Scripts/mcp-hub-service.exe`, so `uv sync` and plain `uv run` cannot rebuild the environment; use `uv run --no-sync ...` for QC, or stop the service first (`hubctl stop`).
- If an AI client reports a startup failure, inspect its stderr capture. MCP protocol output remains on stdout; bootstrap diagnostics are intentionally sent to stderr.
- For child connectivity and upstream checks, use [manual-setup.md → Check connections](manual-setup.md#check-connections) or run QC with `--live-smoke`.
