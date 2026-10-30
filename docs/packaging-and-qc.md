# Source checkout bootstrap and package QC

`mcp-hub` is distributed as a source checkout. The client-facing stdio entrypoints remain `scripts/start.ps1` on Windows and `scripts/start.sh` on macOS/Linux; there is no separate binary, wheel, or ZIP to install.

## Support matrix

| Feature | Windows | macOS / Linux |
|---|---|---|
| Per-client stdio Hub (`start.ps1` / `start.sh`), child supervision, result cache, audit log | Yes | Yes |
| `scripts/smoke.py`, `scripts/package_qc.py` | Yes | Yes |
| Shared service (`hubctl.ps1`: Task Scheduler, `pythonw.exe`), stdio bridge, `http_headers.ps1` | Yes | No |
| Dashboard service controls (stop/restart run `hubctl.ps1`) | Yes | No (`503`) |
| Investigation jobs (`hub__submit_issue` …; job blobs encrypted with DPAPI) | Yes | No: `hub__submit_issue` returns `investigation jobs require Windows DPAPI`, and agent status shows it |
| Preflight runtime install with winget | Yes | No: a missing runtime is reported; install it yourself |

Python 3.12 is required (`.python-version`, `requires-python = ">=3.12"`); the launchers use exactly that minor version and CI tests only 3.12.

## First launch

The launchers prepare the local runtime without prompting, so an AI client can start the Hub directly:

1. Find `uv` on `PATH` or in `.tools/`; if missing, install the pinned uv version into the ignored repo-local `.tools/` directory (see below).
2. Read the required Python major/minor version from `.python-version`; install it through `uv` if it is unavailable.
3. Run `uv run --locked`, which creates or synchronizes the environment against `uv.lock` before executing the existing `mcp-hub` entrypoint.

The first launch needs network access to download missing tools, Python, and packages. It also needs write access to the checkout and uv-managed data/cache directories. Bootstrap status and errors go to stderr. If a download, install, or dependency sync fails, the launcher exits before the Hub starts. MCP clients continue to use the same server name, transport, command and arguments documented in [connecting-a-client.md](connecting-a-client.md).

### Pinned uv installer

When no uv is found, the launchers download the installer of one pinned uv release (`https://astral.sh/uv/<version>/install.ps1` or `install.sh`) to `.tools/`, compare its SHA-256 with the value stored in the script (`$uvVersion` / `$uvInstallerSha256` in `start.ps1`, `uv_version` / `uv_installer_sha256` in `start.sh`), and run it only on a match. A mismatch stops the bootstrap with `Bootstrap failed: ... does not match its pinned SHA-256; it was not run.` The pinned installer itself downloads that exact uv release. To move to a newer uv, update the version and both hashes together (download each installer and run `sha256sum`); `tests/test_bootstrap_scripts.py` checks that both launchers pin the same version.

### Dependency groups

`pyproject.toml` groups the runtime dependencies by purpose: core (MCP, HTTP service, validation), investigation providers (`openai`, `anthropic`, `google-genai`, imported only when a role makes its first request; a missing one is shown as "provider package not installed"), and child-shared packages (`httpx`, `python-dotenv`, `cryptography`) that `mcp-jira-sync` and `mcp-read-source` use from the Hub's venv. The launchers sync all of them. The `dev` dependency group holds the QC tools (`ruff`, `coverage`); uv syncs it by default.

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
6. `python -m compileall -q src scripts tests`.

The unit tests run offline: child processes are fakes, upstream services, installers, and model SDKs are mocked, and no test writes to the checkout's `config/private/` or `logs/` (Hub subprocesses get temporary `MCP_HUB_PRIVATE_DIR` / `MCP_HUB_LOG_DIR`). `tests/test_stdio.py` always runs the Hub module over stdio with a fake child. Its launcher test, which runs `start.ps1` / `start.sh` in a fresh uv environment and therefore downloads every locked package (and uv itself when missing), runs only with `MCP_HUB_E2E=1`:

```powershell
$env:MCP_HUB_E2E = "1"; uv run --locked --python 3.12 python scripts/package_qc.py
```

QC does not call child MCPs or upstream services, so CI needs no private configuration or credentials. To add the live connection and read-only upstream checks, run:

```sh
uv run --locked --python 3.12 python scripts/package_qc.py --live-smoke
```

Use `--live-smoke` only when private Hub configuration and required upstream credentials are available. A missing private configuration is a local environment condition, not a failure of the offline package checks.

## Continuous integration

GitHub Actions (`.github/workflows/qc.yml`) runs package QC on Windows, macOS, and Ubuntu for pushes and pull requests, with a 20-minute job timeout. It installs Python 3.12, syncs strictly from the lockfile, and sets `MCP_HUB_E2E=1`, so CI also exercises the documented launch command end to end: listing tools and prompts and receiving Unicode tool output from a fake child, in a clean project environment. It needs no secrets or live services.

## Troubleshooting

- If the launcher reports that it cannot install `uv` or Python, check network access and write access to `.tools/` and uv's managed data directory. A checksum mismatch means the downloaded installer is not the pinned one; do not bypass it.
- If locked dependency synchronization fails, check access to the configured Python package index and retry the launcher or QC command.
- While the shared service runs on Windows it locks `.venv/Scripts/mcp-hub-service.exe`, so `uv sync` and plain `uv run` cannot rebuild the environment; use `uv run --no-sync ...` for QC, or stop the service first (`hubctl stop`).
- If an AI client reports a startup failure, inspect its stderr capture. MCP protocol output remains on stdout; bootstrap diagnostics are intentionally sent to stderr.
- For child connectivity and upstream checks, use [README → Check connections](../README.md#check-connections) or run QC with `--live-smoke`.
