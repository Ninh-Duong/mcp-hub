"""Check that the Hub connects to every enabled child MCP.

  uv run python scripts/smoke.py

One line per child, in order, then one for the Hub itself: PASS, WARN or FAIL plus the reason (from this run's Hub
audit events). A child that fails does not stop the others. For children listed in CHECKS it makes the configured
read-only tool calls that reach the child's upstream service. WARN means the user has not filled in that child's
account or project folders yet (done in the child's own repository); it does not fail the run. Tool responses and
error texts are never printed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable
from datetime import datetime, timezone

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from mcp_hub.audit import default_audit_path
from mcp_hub.config import HUB_ROOT, load_config

LOG = default_audit_path()
_STATUS = {True: "ok", False: "failed", None: "unset"}
HUB_ID = "mcp-hub"


def _texts(result: CallToolResult) -> list[str]:
    return [block.text.strip() for block in result.content if isinstance(block, TextContent)]


def _json_objects(result: CallToolResult) -> list[dict]:
    """JSON-object text blocks of a successful result; [] for an error result."""
    if result.is_error:
        return []
    objects = []
    for text in _texts(result):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            objects.append(payload)
    return objects


def _has_organizations(result: CallToolResult) -> bool:
    return any(isinstance(payload.get("organizations"), list) for payload in _json_objects(result))


def _is_connected(result: CallToolResult) -> bool:
    return any(payload.get("connected") is True for payload in _json_objects(result))


def _unconfigured_envs(result: CallToolResult) -> set[str]:
    """OpenObserve environments that `logs__list_log_providers` reports without an account."""
    return {
        item["env"]
        for payload in _json_objects(result)
        for item in payload.get("openobserve_environments") or []
        if isinstance(item, dict) and item.get("configured") is False and isinstance(item.get("env"), str)
    }


def _has_project_aliases(result: CallToolResult) -> bool:
    return any(
        isinstance(payload.get("configured_projects"), list)
        and all(isinstance(alias, str) for alias in payload["configured_projects"])
        for payload in _json_objects(result)
    )


# What a child answers when the user has not filled in its account yet: a warning, not a failure.
_NOT_SET_MARKERS = ("not configured", "no bitbucket credentials")


def _not_set(result: CallToolResult) -> bool:
    """True when the child says its account is unset or no source project is registered yet."""
    if any(marker in text.lower() for text in _texts(result) for marker in _NOT_SET_MARKERS):
        return True
    return any(payload.get("configured_projects") == [] for payload in _json_objects(result))


# server ID -> (label, read-only Hub tool, arguments, success check). Add one or more checks per child.
Check = tuple[str, str, dict[str, str], Callable[[CallToolResult], bool]]
CHECKS = {
    "mcp-jira-sync": (("default", "jira__check_connection", {}, lambda r: any(t.startswith("✅") for t in _texts(r))),),
    "mcp-server-logs": tuple(
        (env, "logs__list_openobserve_organizations", {"env": env}, _has_organizations)
        for env in ("dev", "stg", "prod")
    ),
    "mcp-pr-companion": (("bitbucket", "pr__check_bitbucket_connection", {}, _is_connected),),
    "mcp-read-source": (("projects", "source__check_connection", {}, _has_project_aliases),),
    "mcp-sqlserver": (("sqlserver", "sql__check_connection", {}, _is_connected),),
}


async def _check_server(client: Client, tools: set[str], server_id: str) -> dict[str, bool | None]:
    """Run one child's checks (True ok, False failed, None unset); an exception fails only this child."""
    outcomes: dict[str, bool | None] = {}
    try:
        unset = set()
        if server_id == "mcp-server-logs" and "logs__list_log_providers" in tools:
            unset = _unconfigured_envs(await client.call_tool("logs__list_log_providers", {}))
        for label, tool, args, check in CHECKS.get(server_id, ()):
            if tool not in tools:
                continue
            if label in unset:
                outcomes[label] = None
                continue
            result = await client.call_tool(tool, args)
            outcomes[label] = None if _not_set(result) else (not result.is_error and check(result))
    except Exception:
        for label, *_ in CHECKS.get(server_id, ()):
            outcomes.setdefault(label, False)
    return outcomes


async def _hub_status_ok(client: Client, tools: set[str]) -> bool:
    if "hub__status" not in tools:
        return False
    try:
        result = await client.call_tool("hub__status", {})
    except Exception:
        return False
    return any(isinstance(payload.get("connected"), int) and "children" in payload for payload in _json_objects(result))


async def _probe(server_ids: list[str]) -> tuple[set[str], dict[str, dict[str, bool | None]]]:
    """Return the connected servers and each configured check outcome, grouped by server and label.

    The Hub's own `hub__status` outcome is grouped under HUB_ID.
    """
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_hub"], cwd=str(HUB_ROOT), env=dict(os.environ))
    with open(os.devnull, "w", encoding="utf-8") as hub_stderr:
        async with Client(stdio_client(params, errlog=hub_stderr)) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            connected = {prompt.name for prompt in (await client.list_prompts()).prompts}
            calls: dict[str, dict[str, bool | None]] = {}
            for server_id in server_ids:
                outcomes = await _check_server(client, tools, server_id) if server_id in connected else {}
                if outcomes:
                    calls[server_id] = outcomes
            calls[HUB_ID] = {"status": await _hub_status_ok(client, tools)}
            return connected, calls


def _events_since(started: str) -> list[dict]:
    # ponytail: misses events if the log rotates mid-run; rerun if a line says "no Hub events".
    if not LOG.is_file():
        return []
    events = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("timestamp", "") >= started:
            events.append(record)
    return events


def _state(outcomes: list[bool | None]) -> str:
    """FAIL when any check failed; WARN when none passed because every account is unset; else PASS."""
    if False in outcomes:
        return "FAIL"
    return "PASS" if True in outcomes else "WARN"


def _report(
    server_id: str,
    connected: set[str],
    calls: dict[str, dict[str, bool | None]],
    events: list[dict],
) -> tuple[str, str]:
    """Return (PASS | WARN | FAIL, reason) for one child."""
    mine = [e for e in events if e.get("server") == server_id]
    for e in mine:
        if e["event"] in ("server.connect.failed", "server.list_tools.failed"):
            return "FAIL", f"not connected: {e['event']} ({e.get('error_type', '?')})"
    skipped = [str(e.get("tool")) for e in mine if e["event"] == "route.skipped"]
    count = next((e.get("tools_count") for e in mine if e["event"] == "server.connected"), None)
    if server_id not in connected:
        return "FAIL", f"no exposed tools: {', '.join(skipped)}" if skipped else "not connected (no Hub events; see logs)"
    detail = f"{count} tools"
    if skipped:
        return "FAIL", f"{detail}, tool not found: {', '.join(skipped)}"
    checks = CHECKS.get(server_id)
    if checks is None:
        return "PASS", f"{detail}, connected (no read-only check in CHECKS)"
    results = calls.get(server_id, {})
    missing = next((tool for label, tool, _, _ in checks if label not in results), None)
    if missing:
        return "FAIL", f"{detail}, check tool not exposed: {missing}"
    tool = checks[0][1]
    outcomes = [results[label] for label, _, _, _ in checks]
    if len(checks) == 1:
        return _state(outcomes), f"{detail}, {tool} {_STATUS[outcomes[0]]}"
    # None = no account for that environment: shown as unset, not a failure, but at least one must pass.
    status = " ".join(f"{label}={_STATUS[results[label]]}" for label, _, _, _ in checks)
    return _state(outcomes), f"{detail}, {tool} {status}"


def _hub_report(connected: set[str], calls: dict[str, dict[str, bool | None]], enabled: list[str]) -> tuple[str, str]:
    """The Hub's own verdict: it started, answers `hub__status`, and reports its children."""
    if not calls.get(HUB_ID, {}).get("status"):
        return "FAIL", "hub__status is missing or malformed (see logs/mcp-hub.jsonl)"
    return "PASS", f"hub__status ok, {len(connected & set(enabled))}/{len(enabled)} children connected"


def _print_summary(lines: list[tuple[str, str, str]], enabled: list[str]) -> None:
    width = max(map(len, [*enabled, HUB_ID]))
    for name, state, reason in lines:
        print(f"{name:<{width}}  {state}  {reason}")
    warned = [name for name, state, _ in lines if state == "WARN"]
    for name in warned:
        print(f"WARN {name}: fill in its account or project folders in ../{name} (see docs/mcps/{name}.md).")
    if warned:
        print("Account values are typed only in each child's own repository, never into an AI chat.")
    if any(state == "FAIL" for _, state, _ in lines):
        print("Details: logs/mcp-hub.jsonl and the child's own logs.")


def main() -> None:
    try:
        enabled = list(load_config().servers)
    except Exception as exc:
        print(f"smoke=FAIL ({type(exc).__name__}: Hub config is invalid; see logs/mcp-hub.jsonl)")
        raise SystemExit(1)

    if not enabled:
        print("No enabled child MCPs; Hub exposes hub__status.")
        raise SystemExit(0)

    started = datetime.now(timezone.utc).isoformat()
    try:
        connected, calls = asyncio.run(_probe(enabled))
    except Exception:
        connected, calls = set(), {}  # Hub exited (e.g. no child connected); the events below say why.
    events = _events_since(started)

    lines = [(server_id, *_report(server_id, connected, calls, events)) for server_id in enabled]
    lines.append((HUB_ID, *_hub_report(connected, calls, enabled)))
    _print_summary(lines, enabled)
    raise SystemExit(1 if any(state == "FAIL" for _, state, _ in lines) else 0)


if __name__ == "__main__":
    main()
