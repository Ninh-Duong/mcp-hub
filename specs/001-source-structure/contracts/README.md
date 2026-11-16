# Contracts: Behavior That Must Not Change

This refactor adds no new interface. Its contract is the set of existing external surfaces that must stay identical (FR-010). Each surface is captured as a baseline before Phase 1 and compared after every phase.

| Surface | Source of truth | Baseline file (checked in, guarded by `tests/test_surface_contract.py`) | Pass rule |
|---|---|---|---|
| MCP tools: name, description, input schema | `create_mcp_server` with the fake child from `tests/fake_stdio_child.py` | `tests/contract_baseline/tools.json` | Byte-identical sorted JSON |
| HTTP routes: path, methods | `http.create_http_app` | `tests/contract_baseline/routes.json` | Identical set |
| Audit event names | `audit_events.py` and `docs/security-and-logging.md` | `tests/contract_baseline/audit_events.json` | Identical set |
| Settings keys and defaults | `settings.py` | `tests/contract_baseline/settings.json` | Every old key present with the same default; new keys allowed only where research.md R7 says |
| Command-line entry points | `pyproject.toml` `[project.scripts]` and the `-m` modules used by `scripts/*.ps1` | `tests/contract_baseline/entry_points.json` | Identical set |
| Call-path latency | `scripts/bench_call_path.py` | `tests/contract_baseline/bench.json` | Not more than 5 percent slower (SC-007) |

## Rules for the baselines

- The baseline is generated once, before any code moves, by `python -m tests.surface_capture` (the surfaces) and by the manual benchmark (latency). Both are committed in Phase 2 (tasks T003–T006).
- Baseline files contain no values from `config/private/`, no tokens, and no machine paths. The fake child and fixture config provide every value (AGENTS.md "Privacy and paths"); `scripts/check_leaks.py` scans them.
- A phase is not committed unless its contract diff is empty, or the diff is explained in the commit message and in the affected doc. A deliberate change regenerates the baseline in the same commit. Any change to a surface is a behavior change and is out of scope for this feature (spec Assumptions).
- Latency comparisons use the same machine and the same number of iterations as the baseline run.

## Not in scope

- New tools, routes, or settings keys beyond those explicitly listed in research.md R7.
- Changes to child MCP servers (spec Assumptions).
