# Data Model: Gateway Hardening & Protocol Evolution

## 1. Settings Extensions (`src/mcp_hub/settings.py`)

- `ProtocolSettings`:
  - `validate_arguments: str = "warn"` — choices `("off", "warn", "enforce")`.
  - `tool_exposure: str = "all"` — choices `("all", "planned")`.
- `SecuritySettings`:
  - `tool_pin_mode: str = "warn"` — choices `("off", "warn", "enforce")`.
  - `redact_results: bool = True` — bool.
- `PolicySettings`:
  - `workflow_lock_timeout_seconds: float = 30.0` — finite positive float (`> 0`).

## 2. Server Transport & Runtime Config (`src/mcp_hub/config.py`)

- `ServerConfig`:
  - `transport: str = "stdio"` — `"stdio"` or `"streamable-http"`.
  - `url: str | None = None` — required for `"streamable-http"`; must use `https://` or loopback `http://` (`127.0.0.1`, `localhost`, `[::1]`).
  - `headers: dict[str, str] = field(default_factory=dict)` — string key/value pairs for `"streamable-http"`; sensitive header values included in `secret_values`.
  - `command: str = ""`, `args: tuple[str, ...] = ()`, `cwd: Path | None = None` — required for `"stdio"`; optional/unused for `"streamable-http"`.
  - Child JSON declarative Hub venv fields: `"useHubVenv": bool` (default `False`), `"pythonPath": str | None` (relative subfolder inside child `cwd`).

## 3. Protection Backend (`src/mcp_hub/security/dpapi.py`)

- `SecretProtector` protocol (`available: bool`, `protect(bytes) -> bytes`, `unprotect(bytes) -> bytes`):
  - `DPAPIProtector`: Windows user-scope CryptProtectData/CryptUnprotectData.
  - `FernetProtector`: POSIX fallback using `cryptography.fernet.Fernet` with key persisted atomically at `PRIVATE_ROOT / "job_store.key"` (mode `0o600`).

## 4. Pending Job Approvals (`src/mcp_hub/jobs/approvals.py`, `src/mcp_hub/jobs/runner.py`)

- `PendingApprovalView`:
  - `job_id: str`
  - `tool_name: str`
  - `agent: str | None`
  - `expires_at: str` (ISO-8601 UTC)
  - `argument_keys: list[str]` (parameter names only, never unredacted secrets)
- Admin decision transition:
  - `approve`: marks pending call approved via store, wakes live wait event or schedules `_run_approved_write` once.
  - `reject`: cancels pending write, records `approval.rejected`, settles waiting job cleanly to `partial` / `cancelled`.

## 5. Pin Enforcement State (`src/mcp_hub/tool_pins.py`)

- `PinScreeningMixin`:
  - `_pin_changes: dict[str, tuple[str, ...]]` — changed tool names per server.
  - `_suspicious_tools: dict[str, tuple[str, ...]]` — tools with hidden Unicode (`tag-characters`, `bidi-control`, `zero-width`) per server.
  - `_blocked_tools(server_id)` — union of changed + suspicious tools when `tool_pin_mode == "enforce"`, empty otherwise.

## 6. Plan Persistence Snapshot (`src/mcp_hub/planning/policy.py`)

- Persisted at `PRIVATE_ROOT / "plans.json"` (version `1`, TTL `7200` seconds, max `max_plans` entries):
  - Fields per entry: `plan_id`, `created_at` (epoch float), `servers`, `env`, `writes`, `connection_checks`, `steps`, `playbook_id`, `project`, `task`, `window` (`[since_iso, until_iso]` | `null`), `had_failure`, `execution` (optional client/mode/models), and `workflow` (`stage`, `context`, `decisions`, `refusals`, `outcome`).
  - Explicitly excluded: raw tool outputs, `FullResultStore` payloads, secrets, and `ResultCache` entries.

## 7. Namespaced MCP Resource URI (`src/mcp_hub/resources.py`)

- Format: `mcp-hub://<server_id>/<percent-encoded-original-uri>`
- Parsed strictly with `urllib.parse.urlsplit`; `server_id` validated against `self.clients` before calling `client.read_resource(original_uri)`.
