# Implementation Plan: Gateway Hardening & Protocol Evolution

**Branch**: `008-gateway-hardening` | **Date**: `2026-11-26` | **Spec**: [spec.md](spec.md)

## Summary

Harden `mcp-hub`'s security, correctness, context efficiency, MCP protocol coverage, and zero-restart operations across five independent User Story increments (US1–US5), with a foundational infrastructure phase first and a mandatory `/ponytail` simplification + `package_qc.py` verification gate at the close of every phase.

## Technical Context

- **Language/Version**: Python 3.12+ (locked via `uv.lock`).
- **Primary Dependencies**: `mcp>=2.0,<3`, `httpx2>=2.0`, `starlette>=0.40`, `uvicorn>=0.30`, `jsonschema>=4.0`, `cryptography>=42`, `PySide6-Essentials>=6.8`, `pyside6-fluent-widgets>=1.11.3`. Zero new dependencies added.
- **Storage**: SQLite (`jobs.sqlite3`, encrypted via Windows DPAPI or POSIX Fernet), atomic JSON files (`hub.json`, `tool_pins.json`, `plans.json`), rotating JSONL audit log (`logs/mcp-hub.jsonl`).
- **Testing**: Offline `unittest` under `coverage` (>=85% branch+line gate), `ruff`, `check_leaks.py`, `check_code_standards.py`, `check_docs.py`, `test_module_boundaries.py`, `test_surface_contract.py`, `compileall`, and `scripts/smoke.py`.
- **Target Platform**: Windows primary (shared service + Desktop Status app + `hubctl.ps1`), macOS & Linux CI-verified parity for core Hub, stdio bridge, and encrypted Investigation Jobs.
- **Constraints**: One-way module imports, functions `<=50` lines, 100% English repo artifacts (bilingual `en.json`/`vi.json` for Desktop Status), non-blocking event loop on tool-call path, backward-compatible defaults.

## Constitution Check

- **One-way layering (`tests/test_module_boundaries.py`)**: PASS — `resources.py` sits as a runtime mixin beside `batch.py` and `tool_pins.py`; `security/dpapi.py` stays free of Hub runtime imports; `desktop/` stays isolated over HTTP.
- **Small single-purpose functions (`<=50` lines)**: PASS — every phase ends with a `/ponytail` pass and `check_code_standards.py`.
- **Offline tests & zero private checkout mutation**: PASS — every test patches `PRIVATE_ROOT` and passes temp paths for `AuditLog`, `JobStore`, and `PinStore`.
- **Docs & baselines synchronized**: PASS — owning docs and `tests/contract_baseline/` updated in the same change.

## Project Structure

```text
specs/008-gateway-hardening/
├── spec.md
├── research.md
├── data-model.md
├── contracts/README.md
├── quickstart.md
├── plan.md
├── tasks.md
└── checklists/requirements.md

src/mcp_hub/
├── settings.py               # New tunables & enforce/planned choices
├── audit.py                  # QueueHandler + QueueListener non-blocking writes
├── audit_events.py           # New audit event constants
├── config.py                 # Declarative useHubVenv + streamable-http ServerConfig
├── children.py               # open_http_child_client alongside open_stdio_client
├── child_lifecycle.py        # Live reload_server hot-toggle
├── tool_pins.py              # Enforce mode blocking + accept_pins
├── resources.py              # [NEW] Namespaced MCP resources mixin
├── server.py                 # Tool exposure filter, enforce args, outbound redaction, resources
├── bridge_proxy.py           # Forward resources/* over stdio bridge
├── results/shape.py          # Outbound secret redaction for CallToolResult
├── planning/policy.py        # active_servers() + metadata-only PlanBook persistence
├── planning/hub_plan.py      # Announce expanded tools under planned exposure
├── planning/workflow_runtime.py # Bounded async lock queue for parallel calls
├── security/dpapi.py         # Windows DPAPI + POSIX Fernet fallback
├── jobs/approvals.py         # Admin list/approve/reject methods
├── jobs/runner.py            # Expose admin approval actions
├── dashboard/api.py          # Pending job approval & pin-accept admin routes
├── dashboard/settings_api.py # Trigger runtime.reload_server on server switch
└── desktop/                  # Pending approval & pin-accept UI (en.json + vi.json)
```

## Complexity Tracking

No constitution violations; every change reuses existing Hub primitives or Python stdlib without introducing extra layers or dependencies.
