# Source checkout bootstrap and package QC

`mcp-hub` is distributed as a source checkout. The client-facing stdio entrypoints remain `scripts/start.ps1` on Windows and `scripts/start.sh` on macOS/Linux; there is no separate binary, wheel, or ZIP to install.

## First launch

The launchers prepare the local runtime without prompting, so an AI client can start the Hub directly:

1. Find `uv` on `PATH` or in `.tools/`; if missing, install it into the ignored repo-local `.tools/` directory.
2. Read the required Python major/minor version from `.python-version`; install it through `uv` if it is unavailable.
3. Run `uv run --locked`, which creates or synchronizes the environment against `uv.lock` before executing the existing `mcp-hub` entrypoint.

The first launch needs network access to download missing tools, Python, and packages. It also needs write access to the checkout and uv-managed data/cache directories. Bootstrap status and errors go to stderr. If a download, install, or dependency sync fails, the launcher exits before the Hub starts. MCP clients continue to use the same server name, transport, command and arguments documented in [connecting-a-client.md](connecting-a-client.md).

## Offline package QC

After the initial dependency sync, run the standard QC command:

```sh
uv run --locked --python 3.12 python scripts/package_qc.py
```

It runs, in order:

- `python -m unittest discover tests`
- `python scripts/check_leaks.py`
- `python -m compileall -q src scripts tests`

The QC command stops at the first failure. It does not call child MCPs or upstream services, so CI needs no private configuration or credentials. To add the live connection and read-only upstream checks, run:

```sh
uv run --locked --python 3.12 python scripts/package_qc.py --live-smoke
```

Use `--live-smoke` only when private Hub configuration and required upstream credentials are available. A missing private configuration is a local environment condition, not a failure of the offline package checks.

## Continuous integration

GitHub Actions runs the offline QC command on Windows, macOS, and Ubuntu for pushes and pull requests. It installs Python 3.12, syncs strictly from the lockfile, then runs package QC. This also exercises the documented launch command through an MCP stdio integration test with a fake child, including listing tools/prompts and receiving Unicode tool output. The test uses a clean project environment to confirm that locked dependencies are prepared before the server starts; it does not require secrets or live services.

## Troubleshooting

- If the launcher reports that it cannot install `uv` or Python, check network access and write access to `.tools/` and uv's managed data directory.
- If locked dependency synchronization fails, check access to the configured Python package index and retry the launcher or QC command.
- If an AI client reports a startup failure, inspect its stderr capture. MCP protocol output remains on stdout; bootstrap diagnostics are intentionally sent to stderr.
- For child connectivity and upstream checks, use [README → Check connections](../README.md#check-connections) or run QC with `--live-smoke`.
