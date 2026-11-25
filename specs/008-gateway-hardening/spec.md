# Feature Specification: Gateway Hardening & Protocol Evolution

**Feature Branch**: `008-gateway-hardening`
**Created**: `2026-11-26`
**Status**: `Approved for implementation`
**Input**: Address the core security, correctness, context-efficiency, protocol-coverage, and operational weaknesses identified across `mcp-hub` relative to top open-source MCP gateways, executed across phased Spec Kit increments with a `/ponytail` simplification audit after each phase.

## User Scenarios & Testing

### User Story 1 - Complete human job approvals and cross-platform job encryption (Priority: P1)

Operators running investigation jobs can inspect and approve or reject writes waiting in `awaiting_approval` directly through the local admin channel (`mcp-hub Status` desktop app and `hubctl`), while AI clients holding only the MCP client token cannot self-approve. Investigation job encryption also works on macOS and Linux using a local user-only key alongside Windows DPAPI.

- **Independent Test**: Submit an investigation job that reaches `awaiting_approval`; confirm the MCP client token receives `403` on admin approval routes, the admin token lists the pending write and approves or rejects it cleanly, and `JobStore` encrypts and decrypts job blobs when Windows DPAPI is unavailable.
- **Acceptance Scenarios**:
  1. **Given** a job waiting in `awaiting_approval`, **When** an admin client queries pending approvals, **Then** the Hub returns `job_id`, `tool_name`, `agent`, `expires_at`, and redacted argument keys without exposing any approval code over `/mcp`.
  2. **Given** a pending approval, **When** an admin client posts `{"action": "approve"}` or `{"action": "reject"}`, **Then** the stored write runs once (or cancels cleanly) and the MCP client token is refused with `403`.
  3. **Given** a non-Windows platform where DPAPI is unavailable, **When** a job is submitted and retrieved, **Then** `JobStore` protects blobs using a user-restricted local Fernet key (`0o600`) under the private directory instead of refusing jobs.

### User Story 2 - Enforce tool pins, pin acceptance, argument validation, and outbound secret redaction (Priority: P1)

Operators can enable strict `"enforce"` modes for tool definition pins (`security.toolPinMode`) and argument schema checks (`protocol.validateArguments`), explicitly accept updated child tool definitions after legitimate upgrades, and rely on outbound redaction (`security.redactResults`) so known child secrets and credential tokens never reach the AI client if leaked in child output.

- **Independent Test**: Mutate a fake child's tool definition or description with hidden Unicode under `toolPinMode="enforce"` and verify the tool is blocked from routing until accepted via the admin endpoint; send invalid arguments under `validateArguments="enforce"` and verify refusal before child invocation; return a known child secret inside a `CallToolResult` and verify redaction before caching or delivery.
- **Acceptance Scenarios**:
  1. **Given** `security.toolPinMode == "enforce"`, **When** a child tool's fingerprint changes or its description contains hidden Unicode (`tag-characters`, `bidi-control`, `zero-width`), **Then** the Hub omits that tool from exposed routes (`route.skipped`) and refuses calls to it.
  2. **Given** changed tool pins, **When** an admin calls the pin-accept route (or `hubctl accept-pins`), **Then** stored pins update atomically, blocked routes reopen, and `notifications/tools/list_changed` is published.
  3. **Given** `protocol.validateArguments == "enforce"`, **When** a client sends arguments violating the child's JSON schema, **Then** the Hub refuses the call with `tool.call.denied` (`reason="invalid_arguments"`) before invoking the child.
  4. **Given** `security.redactResults == True`, **When** a child returns text containing a configured child secret, private key, URL credentials, Bearer token, or known token shape, **Then** the returned text is redacted before caching and delivery without truncating normal content.

### User Story 3 - Progressive tool exposure and concurrent workflow lock queueing (Priority: P1)

Operators can switch `protocol.toolExposure` to `"planned"` so AI clients initially load only Hub orchestration tools and automatically receive the planned child tools via `notifications/tools/list_changed` after `hub__plan` succeeds. Within an active workflow plan, parallel read calls emitted in the same assistant turn queue cleanly behind the workflow lock within a bounded timeout instead of failing immediately.

- **Independent Test**: Start `HubRuntime` with `toolExposure="planned"` and verify `list_tools` exposes only `hub__*` tools until `hub__plan` activates specific child servers and triggers `tools/list_changed`; invoke concurrent direct read calls against one workflow plan and verify both succeed without spurious `Policy:` lock refusals.
- **Acceptance Scenarios**:
  1. **Given** `protocol.toolExposure == "planned"` and no active plans, **When** a client calls `tools/list`, **Then** only Hub coordination tools (`hub__*`) are returned.
  2. **Given** `protocol.toolExposure == "planned"`, **When** `hub__plan` creates a plan naming child server `S`, **Then** `tools/list` includes `S`'s routes and `notifications/tools/list_changed` is scheduled.
  3. **Given** an enforced workflow plan, **When** two read calls arrive concurrently, **Then** the second call waits for `workflow.lock` up to `policy.workflowLockTimeoutSeconds` and executes instead of immediately failing.

### User Story 4 - Remote `streamable-http` child MCPs and namespaced MCP resources (Priority: P2)

Operators can configure child MCP servers over remote `streamable-http` (restricted to `https://` or loopback `http://127.0.0.1` / `localhost`) in addition to local `stdio`, with sensitive HTTP headers redacted automatically. Clients over HTTP and `mcp-hub-bridge` can list and read child MCP resources and resource templates under `mcp-hub://<server-id>/...` namespaces.

- **Independent Test**: Connect `ChildSupervisor` to a mock `streamable-http` child ASGI endpoint and verify tool listing, calls, and reconnect handling; list and read resources from connected children through both `HubRuntime` and `bridge_proxy`.
- **Acceptance Scenarios**:
  1. **Given** a child config with `"transport": "streamable-http"` and an `https://` or loopback URL, **When** the Hub starts, **Then** `ChildSupervisor` connects via `streamable_http_client`, redacts sensitive headers, and routes calls identically to `stdio` children.
  2. **Given** a non-loopback `http://` URL in child config, **When** config loads, **Then** `ConfigError` rejects the insecure endpoint.
  3. **Given** connected children exposing MCP resources or templates, **When** a client calls `resources/list`, `resources/templates/list`, or `resources/read`, **Then** the Hub and stdio bridge proxy namespaced `mcp-hub://<server-id>/...` URIs to the owning child and emit metadata-only audit events.

### User Story 5 - Live child hot-toggle and restart-safe plan persistence (Priority: P2)

Toggling a child MCP on or off via the local settings API applies immediately to the running `HubRuntime` without restarting the Hub, stopping or starting that child's `ChildSupervisor` and announcing updated tools. Safe `PlanBook` metadata (IDs, scopes, workflow stage/context, never raw tool outputs or secrets) persists atomically so active plans survive Hub restarts or updates within their TTL.

- **Independent Test**: Toggle a child server off and back on via `/dashboard/api/settings/servers/{server_id}` and verify `ChildSupervisor` stops/starts and routes update without service restart; create a plan, reconstruct `HubRuntime` against the same private directory, and continue calling tools with the persisted `hub_plan_id`.
- **Acceptance Scenarios**:
  1. **Given** a running Hub with child `A` enabled, **When** an admin toggles `A` off via settings API, **Then** `A`'s supervisor stops, its routes and cached results are removed, and `tools/list_changed` is published.
  2. **Given** an active plan in `PlanBook`, **When** `HubRuntime` restarts with the same private directory before the plan TTL expires, **Then** the plan remains usable without `Unknown or expired hub_plan_id`.

### Edge Cases

- **Unreadable or corrupt `plans.json`**: Start cleanly with an empty `PlanBook` and log a warning without blocking Hub startup.
- **Audit log rotation under load**: Writing audit lines must never block the asyncio event loop; closing `AuditLog` flushes queued lines before releasing file handles.
- **Pending approval races**: Approving or rejecting an already-expired, cancelled, or missing job returns a clean negative status without double-executing any write.
- **Resource URI injection**: Malformed or unknown `mcp-hub://` server IDs in `resources/read` raise `INVALID_PARAMS` without calling any child.
- **Workflow lock timeout**: If a workflow call genuinely exceeds `policy.workflowLockTimeoutSeconds` waiting on `workflow.lock`, the Hub refuses cleanly with a `Policy:` message rather than hanging forever.

## Requirements

### Functional Requirements

- **FR-001**: Extend `settings.py` and `docs/settings.md` with `security.toolPinMode` (`off|warn|enforce`), `protocol.validateArguments` (`off|warn|enforce`), `security.redactResults` (bool, default `True`), `protocol.toolExposure` (`all|planned`, default `all`), and `policy.workflowLockTimeoutSeconds` (float > 0, default `30.0`).
- **FR-002**: Offload `AuditLog` disk writes and rotation in `src/mcp_hub/audit.py` using stdlib `QueueHandler` + `QueueListener` so event-loop callers never wait on disk I/O or Windows file rotation, while preserving deterministic flush on `close()`.
- **FR-003**: Replace hardcoded child ID checks in `config._apply_hub_venv` with a declarative `"useHubVenv"` / `"pythonPath"` child config mechanism while keeping backward compatibility for existing private configs of `mcp-jira-sync` and `mcp-read-source`.
- **FR-004**: Add a POSIX user-key `FernetProtector` fallback in `src/mcp_hub/security/dpapi.py` (mode `0o600` key file under `PRIVATE_ROOT`) so `JobStore` encrypts job data on macOS/Linux while keeping Windows DPAPI on Windows.
- **FR-005**: Expose admin-only routes `/dashboard/api/jobs/pending` (`GET`) and `/dashboard/api/jobs/{job_id}/decision` (`POST`), matching `hubctl` commands, and Desktop Status UI approval actions (bilingual `en.json`/`vi.json`), strictly rejecting the MCP client bearer token (`403`).
- **FR-006**: Enforce `security.toolPinMode == "enforce"` (blocking changed/suspicious tools from exposed routes) and expose `HubRuntime.accept_pins`, admin route `/dashboard/api/security/pins/accept`, `hubctl accept-pins`, and Desktop Status pin-accept support.
- **FR-007**: Enforce `protocol.validateArguments == "enforce"` (refusing invalid arguments before invoking the child) and apply outbound secret redaction to `CallToolResult` text blocks when `security.redactResults` is enabled.
- **FR-008**: Implement `protocol.toolExposure == "planned"` on `HubRuntime` backed by `PlanBook.active_servers()`, publishing `notifications/tools/list_changed` when new plans expand active servers.
- **FR-009**: Replace immediate `workflow.lock.locked()` refusal in `WorkflowRuntimeMixin` with a timeout-bounded `async with workflow.lock:` queue (`policy.workflowLockTimeoutSeconds`).
- **FR-010**: Support `"transport": "streamable-http"` in `ServerConfig` and `ChildSupervisor` (`https://` or loopback `http://` only, sensitive header redaction, `trust_env=False`).
- **FR-011**: Proxy namespaced MCP `resources/list`, `resources/templates/list`, and `resources/read` (`mcp-hub://<server-id>/<encoded-uri>`) through `HubRuntime` (`src/mcp_hub/resources.py`) and `bridge_proxy.py` with metadata-only audit events.
- **FR-012**: Implement live child hot-toggle (`HubRuntime.reload_server`) triggered by `SettingsHandlers.server` so toggling a child stops/starts its `ChildSupervisor`, updates routes/cache, and announces `tools/list_changed` without restarting the Hub.
- **FR-013**: Persist bounded, metadata-only `PlanBook` snapshots atomically to `PRIVATE_ROOT / "plans.json"` (TTL-bounded, zero secrets or raw results) so active plans survive Hub restarts.
- **FR-014**: Synchronize all owning docs, regenerate `tests/contract_baseline/`, pass `scripts/package_qc.py` (>=85% coverage, `<=50` lines/function, zero leaks, 100% English repo files), and run a `/ponytail` review pass at the end of every phase.

## Success Criteria

- **SC-001**: Pending investigation writes can be inspected and approved/rejected end-to-end via admin API, `hubctl`, and Desktop Status while `/mcp` client tokens are blocked with `403`.
- **SC-002**: Investigation jobs encrypt and decrypt offline across Windows (DPAPI) and POSIX (Fernet fallback).
- **SC-003**: Under `toolPinMode="enforce"` and `validateArguments="enforce"`, mutated/poisoned tools and invalid arguments are blocked; pin acceptance restores routing cleanly; outbound secrets in child tool output are redacted before reaching clients.
- **SC-004**: Under `toolExposure="planned"`, initial `tools/list` exposes only Hub tools (~80%+ fewer tools than 5 connected children) and expands dynamically upon `hub__plan`; concurrent workflow reads succeed without lock-contention refusals.
- **SC-005**: Mock `streamable-http` children connect, reconnect, and execute through `ChildSupervisor`; namespaced resources resolve across both HTTP Hub and stdio bridge.
- **SC-006**: Toggling a child updates live routes without restarting the Hub; active `hub_plan_id` plans remain valid across `HubRuntime` restarts.
- **SC-007**: Every phase passes `/ponytail` simplification review and `uv run --no-sync python scripts/package_qc.py` with >=85% coverage and zero lint/doc/leak/standard violations.

## Assumptions

- Existing default setting values (`toolPinMode="warn"`, `validateArguments="warn"`, `toolExposure="all"`) remain backward-compatible so existing deployments and baseline tests work without surprise breakage; `redactResults` defaults to `True` because it targets only configured child secrets and high-confidence credential shapes.
- No new third-party dependencies are added; stdlib (`logging.handlers`, `urllib.parse`, `queue`) and locked packages (`mcp`, `httpx2`, `cryptography`, `jsonschema`) cover every requirement.
