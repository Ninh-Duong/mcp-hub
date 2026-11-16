# Data Model: Source Structure Refactor

This feature has no runtime data. Its "entities" are the planning and structural records that the phases and checks operate on. They match the Key Entities in spec.md.

## Refactor Phase

| Field | Type | Rule |
|---|---|---|
| `id` | string | `P0`..`P5`, plus the sub-step letters used in tasks.md |
| `goal` | string | One sentence, behavior-preserving |
| `allowed_paths` | list of paths | Only files in the phase may change. Parallel agents must not share a path. |
| `gate` | list of commands | Must pass before the phase commit: `package_qc.py`, `smoke.py`, and the contract diff |
| `docs` | list of paths | Docs updated in the same commit (FR-020) |
| `commit` | string | One commit per phase on `refactor/source-structure` (FR-021) |

Transitions: `planned` → `in progress` → `gated` (QC and smoke green, contract diff empty) → `committed`. A phase that fails its gate returns to `in progress`; it is never committed red.

## Module Boundary

One row per allowed import direction. The import-graph test (R10) checks these rules.

| Layer | Modules | May import | Must not import |
|---|---|---|---|
| Leaf | `jsonio`, `clients`, `tool_meta`, `progress`, `textnorm`, `jobs/timeutil`, `settings` | standard library, MCP SDK (`progress` only) | any other `mcp_hub` module |
| Results | `results/*` | leaves | `planning/*`, `server`, `agents/*` |
| Planning | `planning/*` | leaves, `results/*` | `agents/*`, `server` |
| Core | `server`, `child_lifecycle`, `instructions`, `batch`, `tool_pins` | leaves, `results`, `planning` | `activity_ui`, `bitnet`, `dashboard`, `jobs`, `agents` |
| Extensions | `activity_ui`, `bitnet`, `dashboard`, `jobs`, `agents` | core and leaves | (core must never import these) |
| Desktop | `desktop/*` | standard library, PySide6 only, and nothing from `mcp_hub` outside `desktop` | everything else in `mcp_hub` |
| Composition | `service`, `http` | all of the above | (top of the chain) |

Validation: a module imported across a forbidden edge fails the import-graph test. A private name (leading underscore) imported from another module also fails it.

## Settings Key (moved tunable)

| Field | Type | Rule |
|---|---|---|
| `name` | string | Same name style as the existing `settings.py` keys. New keys are added; existing keys are never renamed or removed (spec Edge Cases). |
| `default` | number or string | Must equal the previous hard-coded value (FR-016) |
| `owner` | class in `settings.py` | One of the existing settings classes, or a new `ActivitySettings` if none fits |
| `documented` | bool | Must be `true` in `docs/settings.md` (checked by `check_docs.py`) |

## Shared Helper

| Helper | Replaces | Callers after the refactor |
|---|---|---|
| `jsonio.read_json` | five file readers listed in research.md R3 | `config`, `hub_settings`, `projects`, `service_config`, `update_state`, `tool_pins`, `agents/config`, `bitnet/sidecar`, `child_requirements`, `extensions` |
| `jsonio.write_json_atomic` | six atomic-write sites | `hub_settings`, `tool_pins`, `update_state`, `service_config` (mode `0600`), `bitnet/installer` if JSON |
| `textnorm.fold` | two identical copies | `results/condense`, `planning/merge`, `bitnet/rules` |
| `progress` relay and key set | two copies | `server`, `bridge_proxy` |
| `clients.normalize_client_name` | one definition, but imported from `server` | `bridge`, `server`, `activity_ui/client_sessions` |
| `tool_meta.is_read_only_tool` | one definition, imported from `server` | `server`, `agents/specialists`, `jobs/policy`, `jobs/runner` |
| `desktop/stream.StreamController` | two SSE loops | `desktop/usage`, `desktop/processing` |
| `tests/helpers.fakes` | copies in up to six test files | all test modules that need a fake |
