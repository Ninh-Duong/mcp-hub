# Quickstart & Validation: Gateway Hardening & Protocol Evolution

## 1. Per-Phase Validation Gate

After completing every implementation phase and its `/ponytail` simplification review pass:

```powershell
uv run --no-sync python scripts/package_qc.py
```

## 2. Feature Walkthrough Checks

1. **Pending Job Approval (US1)**:
   - Run `hubctl pending-jobs` or open **mcp-hub Status** when a job awaits write approval; approve or reject via `hubctl approve-job -JobId <id>` or the Desktop Status button.
2. **Enforce Pins & Schema + Redaction (US2)**:
   - Set `"security": {"toolPinMode": "enforce", "redactResults": true}` and `"protocol": {"validateArguments": "enforce"}` in `config/private/hub.json`.
   - After updating a child MCP, run `hubctl accept-pins` (or click **Accept pins** in Desktop Status).
3. **Progressive Tool Exposure (US3)**:
   - Set `"protocol": {"toolExposure": "planned"}` in `config/private/hub.json`; verify initial `tools/list` contains only `hub__*` tools and expands automatically after `hub__plan`.
4. **Remote `streamable-http` Child & Resources (US4)**:
   - Configure a child JSON with `"transport": "streamable-http"` and `"url": "https://..."` (or loopback `http://127.0.0.1:...`), then verify `tools/list` and `resources/list`.
5. **Live Hot-Toggle (US5)**:
   - Toggle a child MCP in **mcp-hub Status → Settings → MCPs** and verify status changes without restarting the Hub.

## 3. Final Release Gate

```powershell
uv run --no-sync python -m tests.surface_capture
uv run --no-sync python scripts/package_qc.py
uv run --no-sync python scripts/smoke.py
```
