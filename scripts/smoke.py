"""Check that the Hub connects to every enabled child MCP.

  uv run python scripts/smoke.py

One line per child: PASS/FAIL plus the reason (from this run's Hub audit events). For children listed in CHECKS
it makes the configured read-only tool calls that reach the child's upstream service. Tool responses and error
texts are never printed.
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

from mcp_hub.config import HUB_ROOT, load_config

LOG = HUB_ROOT / "logs" / "mcp-hub.jsonl"


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


def _has_project_aliases(result: CallToolResult) -> bool:
    return any(
        isinstance(payload.get("configured_projects"), list)
        and all(isinstance(alias, str) for alias in payload["configured_projects"])
        for payload in _json_objects(result)
    )


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
}


async def _probe(server_ids: list[str]) -> tuple[set[str], dict[str, dict[str, bool]]]:
    """Return the connected servers and each configured check outcome, grouped by server and label."""
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_hub"], cwd=str(HUB_ROOT), env=dict(os.environ))
    with open(os.devnull, "w", encoding="utf-8") as hub_stderr:
        async with Client(stdio_client(params, errlog=hub_stderr)) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            connected = {prompt.name for prompt in (await client.list_prompts()).prompts}
            calls: dict[str, dict[str, bool]] = {}
            for server_id in server_ids:
                if server_id in connected:
                    for label, tool, args, check in CHECKS.get(server_id, ()):
                        if tool not in tools:
                            continue
                        result = await client.call_tool(tool, args)
                        calls.setdefault(server_id, {})[label] = not result.is_error and check(result)
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


def _report(
    server_id: str,
    connected: set[str],
    calls: dict[str, dict[str, bool]],
    events: list[dict],
) -> tuple[bool, str]:
    mine = [e for e in events if e.get("server") == server_id]
    for e in mine:
        if e["event"] in ("server.connect.failed", "server.list_tools.failed"):
            return False, f"not connected: {e['event']} ({e.get('error_type', '?')})"
    skipped = [str(e.get("tool")) for e in mine if e["event"] == "route.skipped"]
    count = next((e.get("tools_count") for e in mine if e["event"] == "server.connected"), None)
    if server_id not in connected:
        return False, f"no exposed tools: {', '.join(skipped)}" if skipped else "not connected (no Hub events; see logs)"
    detail = f"{count} tools"
    if skipped:
        return False, f"{detail}, tool not found: {', '.join(skipped)}"
    checks = CHECKS.get(server_id)
    if checks is None:
        return True, f"{detail}, connected (no read-only check in CHECKS)"
    results = calls.get(server_id, {})
    missing = next((tool for label, tool, _, _ in checks if label not in results), None)
    if missing:
        return False, f"{detail}, check tool not exposed: {missing}"
    tool = checks[0][1]
    if len(checks) == 1:
        ok = results[checks[0][0]]
        return ok, f"{detail}, {tool} {'ok' if ok else 'failed'}"
    status = " ".join(f"{label}={'ok' if results[label] else 'failed'}" for label, _, _, _ in checks)
    return all(results[label] for label, _, _, _ in checks), f"{detail}, {tool} {status}"


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

    passed = True
    width = max(map(len, enabled))
    for server_id in enabled:
        ok, reason = _report(server_id, connected, calls, events)
        passed = passed and ok
        print(f"{server_id:<{width}}  {'PASS' if ok else 'FAIL'}  {reason}")
    if not passed:
        print("Details: logs/mcp-hub.jsonl and the child's own logs.")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
