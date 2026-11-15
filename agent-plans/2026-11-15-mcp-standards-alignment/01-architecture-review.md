# Architecture review (reference, not a task list)

Written for people reading the plan. The tasks are in the track cards.

## The layers of an MCP gateway with a harness

| Layer | What it does | Hub today | Plan |
|---|---|---|---|
| L1 Transport | HTTP and stdio, protocol eras, notification relay | Loopback Streamable HTTP, stdio bridge; bridge drops instructions, progress and list changes | Fix the bridge (Track C); Hub side in Phase 2 |
| L2 Security | Auth, origin checks, tool trust | Three credentials, cookie-approved writes, Host and Origin checked twice; no definition pins | Pins in warn mode (Track E) |
| L3 Protocol mediation | Naming, result shape, list changes, cancellation, progress | Condensing drops structured content; no list-changed; `allOf` schemas break `with_plan_id` | Tracks A, B, D; wiring in Phase 2 |
| L4 Registry | Tool catalogue, read or write classes, profiles | Config-driven; read or write from the child's hint | Deferred |
| L5 Child lifecycle | Spawn, supervise, isolate, sandbox | Per-child supervisors, backoff, fail-fast; startup waits for slow children | Startup wait (Phase 2); sandbox deferred |
| L6 Policy | Plans, allowlist, repeat rules, rate limits | Plan-first at the server; plan ids 48-bit; no rate limit | Plan ids 128-bit, repeat window (Track D); rate limit deferred |
| L7 Results | Cache, condensing, paging | Single-flight cache; full-result store bounded by count only | Byte cap (Track A) |
| L8 Harness | Agent loop, context, approvals, long jobs | Investigation jobs with approval and evidence IDs; no context budget; no prompt cache | Track F |
| L9 Observability | Audit, traces, metrics | Metadata-only audit with redaction; no traces or metrics | Deferred |
| L10 Operations | Install, update, service, platforms | Windows-only; one installer | Deferred |
| L11 Quality | Tests, lint, conformance | 953 tests, coverage gate 85, leak and docs checks | Track Q |

## Sources used

- MCP specification 2026-07-28 and 2025-11-25 (tools, transports, authorization, security best practices).
- Python SDK `mcp` 2.2.0 installed in the Hub's environment (subscriptions, caching, client message handler, OpenTelemetry middleware).
- Reference gateways and proxies: FastMCP proxy and transforms, Docker MCP Gateway, sparfenyuk/mcp-proxy, IBM ContextForge, agentgateway, MetaMCP, Lasso, Snyk agent-scan (formerly mcp-scan).
- Harness references: Claude Code subagents and hooks, OpenAI Agents SDK, OpenHands condenser, LangGraph interrupts, Spec Kit task format, Aider architect mode, SWE-agent interface design.

Where a source could not be confirmed, the plan does not depend on it.
