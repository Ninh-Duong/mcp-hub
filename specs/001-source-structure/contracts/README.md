# Contracts: Behavior That Must Not Change

This refactor adds no new interface. Its contract is the set of existing external surfaces that must stay identical (FR-010). Each surface is captured as a baseline before Phase 1 and compared after every phase.

| Surface | Source of truth | Baseline file (created in Phase 0.5) | Pass rule |
|---|---|---|---|
| MCP tools: name, description, input schema | `create_mcp_server` with the fake child from `tests/fake_stdio_child.py` | `contracts/baseline/tools.json` | Byte-identical sorted JSON |
| HTTP routes: path, methods | `http.create_http_app` | `contracts/baseline/routes.json` | Identical set |
| Audit event names | `audit_events.py` and `docs/security-and-logging.md` | `contracts/baseline/audit_events.json` | Identical set |
| Settings keys and defaults | `settings.py` | `contracts/baseline/settings.json` | Every old key present with the same default; new keys allowed only where research.md R7 says |
| Command-line entry points | `pyproject.toml` `[project.scripts]` and the `-m` modules used by `scripts/*.ps1` | `contracts/baseline/entry_points.json` | Identical set |
| Call-path latency | `scripts/bench_call_path.py` | `contracts/baseline/bench.json` | Not more than 5 percent slower (SC-007) |

## Rules for the baselines

- The baseline is generated once, before any code moves, on the refactor branch at the commit that starts Phase 1.
- Baseline files contain no values from `config/private/`, no tokens, and no machine paths. The fake child and fixture config provide every value (AGENTS.md "Privacy and paths").
- A phase is not committed unless its contract diff is empty, or the diff is explained in the commit message and in the affected doc. Any change to a surface is a behavior change and is out of scope for this feature (spec Assumptions).
- Latency comparisons use the same machine and the same number of iterations as the baseline run.

## Not in scope

- New tools, routes, or settings keys beyond those explicitly listed in research.md R7.
- Changes to child MCP servers (spec Assumptions).
