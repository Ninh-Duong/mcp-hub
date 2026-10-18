"""Read-only end-to-end check of the Hub and every enabled child that has an entry in CHECKS.

Prints only PASS/FAIL lines; tool responses are never printed.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from mcp_hub.config import load_config

ROOT = Path(__file__).resolve().parents[1]


def _texts(result: CallToolResult) -> list[str]:
    return [block.text.strip() for block in result.content if isinstance(block, TextContent)]


# server ID -> (a read-only Hub tool to call, its arguments, success check). Add one line per child MCP.
CHECKS = {
    "jira-sync-ticket": ("jira__check_connection", {}, lambda r: any(t.startswith("✅") for t in _texts(r))),
    "mcp-server-logs": ("logs__list_openobserve_organizations", {}, lambda r: True),
}


async def smoke(server_ids: list[str]) -> bool:
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_hub"], cwd=str(ROOT), env=dict(os.environ))
    with open(os.devnull, "w", encoding="utf-8") as hub_stderr:
        async with Client(stdio_client(params, errlog=hub_stderr)) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            prompts = {prompt.name for prompt in (await client.list_prompts()).prompts}
            passed = True
            for server_id in server_ids:
                tool, args, check = CHECKS[server_id]
                ok = tool in tools and server_id in prompts
                if ok:
                    result = await client.call_tool(tool, args)
                    ok = not result.is_error and check(result)
                print(f"{server_id}=" + ("PASS" if ok else "FAIL"))
                passed = passed and ok
            return passed


def main() -> None:
    try:
        enabled = list(load_config().servers)
        for server_id in enabled:
            if server_id not in CHECKS:
                print(f"{server_id}=NO_CHECK (add it to CHECKS in scripts/smoke.py)")
        passed = asyncio.run(smoke([s for s in enabled if s in CHECKS]))
    except Exception as exc:
        print(f"smoke=FAIL ({type(exc).__name__})")
        passed = False
    if not passed:
        print("See logs/mcp-hub.jsonl and the child's own logs; response details are intentionally omitted.")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
