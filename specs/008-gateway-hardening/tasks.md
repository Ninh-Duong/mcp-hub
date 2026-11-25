# Tasks: Gateway Hardening & Protocol Evolution

**Input**: Design documents from `/specs/008-gateway-hardening/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/README.md`, `quickstart.md`

## Phase 1: Setup (Spec Kit Initialization)

- [X] T001 Create `specs/008-gateway-hardening/` (`spec.md`, `research.md`, `data-model.md`, `contracts/README.md`, `quickstart.md`, `plan.md`, `checklists/requirements.md`) and update `.specify/feature.json`
- [X] T002 Generate actionable dependency-ordered `specs/008-gateway-hardening/tasks.md`

---

## Phase 2: Foundational (Settings, Non-Blocking Audit & Declarative Config)

- [X] T003 [P] Add `enforce` choices for `protocol.validate_arguments` and `security.tool_pin_mode`, plus `protocol.tool_exposure`, `security.redact_results`, and `policy.workflow_lock_timeout_seconds` in `src/mcp_hub/settings.py`, `docs/settings.md`, and `tests/test_settings.py`
- [X] T004 [P] Offload `AuditLog` writes and 5 MB rotation off the asyncio event loop using stdlib `QueueHandler` and `QueueListener` in `src/mcp_hub/audit.py` and verify in `tests/test_audit.py`
- [X] T005 [P] Replace hardcoded child ID checks in `_apply_hub_venv` with declarative `"useHubVenv"` / `"pythonPath"` support in `src/mcp_hub/config.py`, `config/mcps/mcp-jira-sync.example.json`, `config/mcps/mcp-read-source.example.json`, and `tests/test_config.py`
- [X] T006 Run `/ponytail` source review on Phase 2 files and verify `uv run --no-sync python scripts/package_qc.py`

---

## Phase 3: User Story 1 - Human Job Approval & Cross-Platform Encryption (P1)

- [X] T007 [P] [US1] Add POSIX `FernetProtector` fallback (`0o600` key under `PRIVATE_ROOT`) alongside `DPAPIProtector` in `src/mcp_hub/security/dpapi.py` and `src/mcp_hub/jobs/store.py`
- [X] T008 [US1] Add admin `list_pending_approvals` and `resolve_pending_approval(approve|reject)` methods in `src/mcp_hub/jobs/approvals.py` and `src/mcp_hub/jobs/runner.py`
- [X] T009 [US1] Wire admin-only `/dashboard/api/jobs/pending` and `/dashboard/api/jobs/{job_id}/decision` in `src/mcp_hub/dashboard/api.py`, `src/mcp_hub/service.py`, and CLI subcommands (`pending-jobs`, `approve-job`, `reject-job`) in `scripts/hubctl.ps1`
- [X] T010 [P] [US1] Surface pending job approvals with Approve/Reject actions in `src/mcp_hub/desktop/` (`hub_api.py`, `hub_page.py`, `actions.py`, `locales/en.json`, `locales/vi.json`)
- [X] T011 [P] [US1] Add offline unit tests in `tests/test_dpapi.py`, `tests/test_job_platform.py`, `tests/test_job_approvals.py`, `tests/test_dashboard_api.py`, and `tests/test_desktop_actions.py`
- [X] T012 [US1] Run `/ponytail` source review on Phase 3 files and verify `uv run --no-sync python scripts/package_qc.py`

---

## Phase 4: User Story 2 - Enforce Pins, Pin Accept, Schema Enforce & Outbound Redaction (P1)

- [X] T013 [P] [US2] Implement `toolPinMode="enforce"` route blocking (changed pins + hidden Unicode descriptions) and `HubRuntime.accept_pins` in `src/mcp_hub/tool_pins.py`, `src/mcp_hub/audit_events.py`, and `src/mcp_hub/server.py`
- [X] T014 [P] [US2] Implement `validateArguments="enforce"` call rejection and `security.redactResults` outbound `CallToolResult` secret redaction in `src/mcp_hub/results/shape.py` and `src/mcp_hub/server.py`
- [X] T015 [US2] Add admin route `POST /dashboard/api/security/pins/accept` in `src/mcp_hub/dashboard/api.py`, `hubctl accept-pins` in `scripts/hubctl.ps1`, and Desktop Status pin-accept support in `src/mcp_hub/desktop/`
- [X] T016 [P] [US2] Add offline unit tests in `tests/test_tool_pins.py`, `tests/test_server_pins.py`, `tests/test_server_arguments.py`, `tests/test_server_calls.py`, and `tests/test_dashboard_api.py`
- [X] T017 [US2] Run `/ponytail` source review on Phase 4 files and verify `uv run --no-sync python scripts/package_qc.py`

---

## Phase 5: User Story 3 - Progressive Tool Exposure & Concurrent Workflow Lock Queue (P1)

- [X] T018 [P] [US3] Implement `PlanBook.active_servers()` and `protocol.toolExposure="planned"` tool filtering plus `notifications/tools/list_changed` announcements in `src/mcp_hub/planning/policy.py`, `src/mcp_hub/planning/hub_plan.py`, and `src/mcp_hub/server.py`
- [X] T019 [P] [US3] Replace immediate `workflow.lock.locked()` refusal with timeout-bounded `async with workflow.lock:` queueing (`workflow_lock_timeout_seconds`) in `src/mcp_hub/planning/workflow_runtime.py`
- [X] T020 [P] [US3] Add offline unit tests in `tests/test_server_order.py`, `tests/test_policy.py`, and `tests/test_workflow_runtime.py`
- [X] T021 [US3] Run `/ponytail` source review on Phase 5 files and verify `uv run --no-sync python scripts/package_qc.py`

---

## Phase 6: User Story 4 - Remote `streamable-http` Child MCPs & Namespaced MCP Resources (P2)

- [X] T022 [P] [US4] Support `"transport": "streamable-http"` (`https://` or loopback `http://`, sensitive header redaction) in `src/mcp_hub/config.py` and `open_http_child_client` in `src/mcp_hub/children.py`
- [X] T023 [P] [US4] Implement namespaced MCP resource proxying (`mcp-hub://<server-id>/<uri>`) in `src/mcp_hub/resources.py`, `src/mcp_hub/server.py`, and `src/mcp_hub/bridge_proxy.py`
- [X] T024 [P] [US4] Add offline unit tests in `tests/test_config.py`, `tests/test_children.py`, `tests/test_resources.py`, and `tests/test_bridge.py`
- [X] T025 [US4] Run `/ponytail` source review on Phase 6 files and verify `uv run --no-sync python scripts/package_qc.py`

---

## Phase 7: User Story 5 - Live Child Hot-Toggle, Plan Persistence & Polish (P2)

- [X] T026 [P] [US5] Implement `HubRuntime.reload_server` in `src/mcp_hub/child_lifecycle.py` and wire live hot-toggle in `src/mcp_hub/dashboard/settings_api.py` and `src/mcp_hub/dashboard/api.py`
- [X] T027 [P] [US5] Implement atomic, TTL-bounded metadata-only `PlanBook` persistence (`PRIVATE_ROOT / "plans.json"`) in `src/mcp_hub/planning/policy.py`, `src/mcp_hub/planning/hub_plan.py`, and `src/mcp_hub/server.py`
- [X] T028 [P] [US5] Add offline unit tests in `tests/test_child_lifecycle.py`, `tests/test_settings_api.py`, and `tests/test_policy.py`
- [X] T029 Update owning docs (`docs/architecture.md`, `docs/reference.md`, `docs/settings.md`, `docs/security-and-logging.md`, `docs/desktop-app.md`, `docs/adding-a-child-mcp.md`, `docs/usage-policy.md`, `docs/operations/shared-agent-hub.md`) and regenerate `tests/contract_baseline/` via `uv run --no-sync python -m tests.surface_capture`
- [X] T030 Final cross-phase `/ponytail` simplification audit, run `uv run --no-sync python scripts/package_qc.py` and `uv run --no-sync python scripts/smoke.py`, and prompt user on version bump per `docs/versioning.md`
