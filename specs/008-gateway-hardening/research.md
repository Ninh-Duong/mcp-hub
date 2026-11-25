# Research & Key Decisions: Gateway Hardening & Protocol Evolution

## Decisions

1. **Non-blocking audit writes via stdlib `QueueHandler` + `QueueListener`**
   - **Decision**: Wrap each shared `RotatingFileHandler` in `src/mcp_hub/audit.py` with Python stdlib `queue.SimpleQueue`, `QueueHandler`, and `QueueListener` (respecting `respect_handler_level=True`).
   - **Rationale**: Prevents 5 MB log rotations on Windows from blocking the asyncio event loop while adding ~15 lines of stdlib code and keeping deterministic flush on `AuditLog.close()`.
   - **Alternatives considered**: Custom asyncio background writer task (rejected: duplicates stdlib `QueueListener` and complicates sync callers/tests).

2. **Cross-platform job encryption reusing locked `cryptography.fernet.Fernet`**
   - **Decision**: Keep `DPAPIProtector` on Windows (`os.name == "nt"`), and provide `FernetProtector` backed by a `0o600` user key file (`PRIVATE_ROOT / "job_store.key"`) on POSIX platforms in `src/mcp_hub/security/dpapi.py`.
   - **Rationale**: `cryptography>=42` is already a locked dependency; unblocks Investigation Jobs on macOS and Linux CI runners with zero new packages.
   - **Alternatives considered**: Adding `keyring` dependency (rejected: extra OS backend fragility in headless Linux/CI and violates dependency minimalism).

3. **Admin-token approval channel instead of restoring a browser dashboard**
   - **Decision**: Expose `/dashboard/api/jobs/pending` and `/dashboard/api/jobs/{job_id}/decision` behind the existing per-run `admin_token` (`LocalBearerMiddleware`), consumed by `hubctl` and Desktop Status.
   - **Rationale**: Reuses the proven dual-token security boundary where AI clients (`client_token`) receive `403` on `/dashboard/*`, closing the broken approval dead-end without reintroducing browser CSRF/session attack surfaces.
   - **Alternatives considered**: Exposing the one-time code in `hub__get_job` (rejected: allows the AI agent to self-approve writes).

4. **Strict `enforce` modes + explicit pin accept + targeted outbound redaction**
   - **Decision**: Extend `toolPinMode` and `validateArguments` with `"enforce"`, expose `runtime.accept_pins` via `/dashboard/api/security/pins/accept`, and add `redact_tool_result` using exact configured `secret_values` plus high-confidence credential patterns (`_PRIVATE_KEY_RE`, `_URL_CREDENTIALS_RE`, `_BEARER_RE`, `_TOKEN_SHAPE_RE`) with no length truncation.
   - **Rationale**: Prevents MCP tool-poisoning/rug-pull attacks and accidental secret leaks into cloud LLM contexts while keeping default `"warn"` backward compatibility for upgrades.
   - **Alternatives considered**: Applying `audit.redact` verbatim with `limit=1000` and `_KEY_VALUE_RE` (rejected: would truncate tool payloads and corrupt harmless JSON keys like `"token_count": 5`).

5. **Progressive tool exposure tied directly to `PlanBook`**
   - **Decision**: Add `protocol.toolExposure: "all" | "planned"`. Under `"planned"`, `list_tools` exposes Hub tools plus routes of `PlanBook.active_servers()`, firing `announce_tools` when `hub__plan` expands active servers.
   - **Rationale**: Zero new meta-tool needed because `mcp-hub` already enforces `hub__plan` before child calls; cuts initial schema context by ~80% while remaining transparent to clients supporting `tools/list_changed`.
   - **Alternatives considered**: Adding a separate `hub__search_tools` semantic index (rejected under `/ponytail`: unnecessary extra moving part when playbooks and `hub__plan` already scope servers).

6. **Bounded async lock queueing for concurrent workflow reads**
   - **Decision**: Replace `if workflow.lock.locked(): refuse` with `anyio.fail_after(workflow_lock_timeout_seconds)` around `async with workflow.lock:`.
   - **Rationale**: Modern AI clients naturally emit parallel `tools/call` requests in one turn; queueing them serially preserves workflow state integrity without penalizing normal client behavior.

7. **Unified `ChildSupervisor` for `stdio` and `streamable-http` children**
   - **Decision**: Extend `ServerConfig` with `transport: "stdio" | "streamable-http"`, `url`, and `headers`, adding `open_http_child_client` using existing `streamable_http_client` + `httpx2.AsyncClient(trust_env=False)`.
   - **Rationale**: Reuses every supervisor capability (backoff, tool pins, result cache, redaction, hot-reload) with ~30 lines of transport wiring.

8. **Namespaced MCP resources (`mcp-hub://<server-id>/<encoded-uri>`)**
   - **Decision**: Route `resources/list`, `resources/templates/list`, and `resources/read` via a focused `ResourcesMixin` (`src/mcp_hub/resources.py`) and forward them in `bridge_proxy.py`.
   - **Rationale**: Completes core MCP primitive coverage while preserving strict child isolation and metadata-only auditing.

9. **Live child hot-toggle & metadata-only `PlanBook` snapshots**
   - **Decision**: Wire `SettingsHandlers.server` to `runtime.reload_server(server_id)` and persist bounded, TTL-checked plan metadata (`PRIVATE_ROOT / "plans.json"`) atomically off the event loop.
   - **Rationale**: Eliminates downtime on child toggles and avoids invalidating active `hub_plan_id`s across routine restarts/updates while keeping all raw tool outputs strictly in memory.
