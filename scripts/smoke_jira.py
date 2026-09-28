from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "private" / "hub.json"
EXPECTED_TOOLS = {
    "jira__check_connection",
    "jira__list_synced",
    "jira__sync_ticket",
}


def _child_env() -> dict[str, str]:
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE") if name in os.environ}
    env["MCP_HUB_CONFIG"] = str(CONFIG)
    return env


async def smoke() -> bool:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_hub"],
        cwd=str(ROOT),
        env=_child_env(),
    )
    child_stderr = open(os.devnull, "w", encoding="utf-8")
    try:
        async with Client(stdio_client(params, errlog=child_stderr)) as client:
            result = await client.list_tools()
            names = {tool.name for tool in result.tools}
            print("hub_tools=" + ",".join(sorted(names)))
            if not EXPECTED_TOOLS.issubset(names):
                print("jira_tool_discovery=FAIL")
                return False
            print("jira_tool_discovery=PASS")

            connection = await client.call_tool("jira__check_connection", {})
            texts = [block.text.strip() for block in connection.content if isinstance(block, TextContent)]
            passed = not connection.is_error and any(text.startswith("✅") for text in texts)
            print("jira_read_only_connection=" + ("PASS" if passed else "FAIL"))
            if not passed:
                print("See logs/mcp-hub.jsonl and the Jira MCP's logs/app.log; response details are intentionally omitted.")
            return passed
    finally:
        child_stderr.close()


def main() -> None:
    if not CONFIG.is_file():
        print("smoke=FAIL (missing private Hub config)")
        raise SystemExit(1)
    try:
        passed = asyncio.run(smoke())
    except Exception as exc:
        print(f"smoke=FAIL ({type(exc).__name__})")
        print("See logs/mcp-hub.jsonl and the Jira MCP's logs/app.log; details are intentionally omitted.")
        raise SystemExit(1) from None
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
