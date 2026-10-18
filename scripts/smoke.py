"""Check that the Hub connects to every enabled child MCP.

  uv run python scripts/smoke.py

One line per child: PASS/FAIL plus the reason (from this run's Hub audit events). For children listed in CHECKS
it also makes one read-only tool call that reaches the child's upstream service. Tool responses and error texts
are never printed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from mcp_hub.config import HUB_ROOT, load_config

LOG = HUB_ROOT / "logs" / "mcp-hub.jsonl"


def _texts(result: CallToolResult) -> list[str]:
    return [block.text.strip() for block in result.content if isinstance(block, TextContent)]


# server ID -> (a read-only Hub tool to call, its arguments, success check). Add one line per child MCP.
CHECKS = {
    "jira-sync-ticket": ("jira__check_connection", {}, lambda r: any(t.startswith("✅") for t in _texts(r))),
    "mcp-server-logs": ("logs__list_openobserve_organizations", {}, lambda r: True),
}


async def _probe(server_ids: list[str]) -> tuple[set[str], dict[str, bool]]:
    """Return the servers that have a Hub prompt (= connected with tools) and each CHECKS call's outcome."""
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_hub"], cwd=str(HUB_ROOT), env=dict(os.environ))
    with open(os.devnull, "w", encoding="utf-8") as hub_stderr:
        async with Client(stdio_client(params, errlog=hub_stderr)) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            connected = {prompt.name for prompt in (await client.list_prompts()).prompts}
            calls: dict[str, bool] = {}
            for server_id in server_ids:
                if server_id in CHECKS and server_id in connected and CHECKS[server_id][0] in tools:
                    tool, args, check = CHECKS[server_id]
                    result = await client.call_tool(tool, args)
                    calls[server_id] = not result.is_error and check(result)
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


def _report(server_id: str, connected: set[str], calls: dict[str, bool], events: list[dict]) -> tuple[bool, str]:
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
    if server_id not in CHECKS:
        return True, f"{detail}, connected (no read-only check in CHECKS)"
    tool = CHECKS[server_id][0]
    if server_id not in calls:
        return False, f"{detail}, check tool not exposed: {tool}"
    return calls[server_id], f"{detail}, {tool} {'ok' if calls[server_id] else 'failed'}"


def main() -> None:
    try:
        enabled = list(load_config().servers)
    except Exception as exc:
        print(f"smoke=FAIL ({type(exc).__name__}: Hub config is invalid; see logs/mcp-hub.jsonl)")
        raise SystemExit(1)

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
