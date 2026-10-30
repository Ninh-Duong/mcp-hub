"""The Hub over stdio, as an MCP host starts it, with a fake child.

The offline test runs the installed `mcp_hub` module with this interpreter. The launcher test runs the
documented `scripts/start.ps1` / `scripts/start.sh` in a fresh uv environment, which downloads every
locked package (and uv itself if it is missing), so it runs only with `MCP_HUB_E2E=1` (set in CI).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_hub.audit import LOG_DIR_ENV
from mcp_hub.config import HUB_ROOT, PRIVATE_DIR_ENV

E2E_ENV = "MCP_HUB_E2E"


def write_private_config(private: Path) -> Path:
    """A private Hub manifest with one fake child, in a temporary private directory."""
    private.mkdir(parents=True)
    child_path = private / "child.json"
    hub_path = private / "hub.json"
    child_path.write_text(json.dumps({
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(HUB_ROOT / "tests" / "fake_stdio_child.py")],
        "cwd": str(HUB_ROOT),
    }), encoding="utf-8")
    hub_path.write_text(json.dumps({
        "version": 1,
        "servers": {"fake-child": {"enabled": True, "configFile": str(child_path)}},
        "features": {"fake-feature": {
            "enabled": True,
            "tools": [{"server": "fake-child", "name": "echo_unicode", "exposeAs": "fake__echo_unicode"}],
        }},
    }), encoding="utf-8")
    return hub_path


def isolated_environment(temp: Path) -> dict[str, str]:
    """The Hub reads config from, and logs to, `temp`: never the checkout's config/private/ or logs/."""
    env = os.environ.copy()
    env[PRIVATE_DIR_ENV] = str(temp / "private")
    env[LOG_DIR_ENV] = str(temp / "logs")
    env["MCP_HUB_CONFIG"] = str(write_private_config(temp / "private"))
    return env


class StdioCompatibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.temp = Path(self.tmp.name)

    def test_hub_module_serves_an_agent_over_stdio_offline(self) -> None:
        env = isolated_environment(self.temp)
        params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_hub"], cwd=str(HUB_ROOT), env=env)
        asyncio.run(self.exercise_client(params))
        self.assertTrue((self.temp / "logs" / "mcp-hub.jsonl").is_file())

    @unittest.skipUnless(os.environ.get(E2E_ENV) == "1", f"set {E2E_ENV}=1 to run the launcher in a fresh uv environment")
    def test_documented_launcher_connects_an_agent_to_a_fake_child(self) -> None:
        env = isolated_environment(self.temp)
        env.pop("VIRTUAL_ENV", None)
        env["UV_PROJECT_ENVIRONMENT"] = str(self.temp / "clean-environment")
        command, args = self.launcher()
        params = StdioServerParameters(command=command, args=args, cwd=str(HUB_ROOT), env=env)
        asyncio.run(self.exercise_client(params, timeout=300))
        self.assertTrue(Path(env["UV_PROJECT_ENVIRONMENT"]).is_dir())

    @staticmethod
    def launcher() -> tuple[str, list[str]]:
        if os.name == "nt":
            powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            return str(powershell), [
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HUB_ROOT / "scripts" / "start.ps1"),
            ]
        return "bash", [str(HUB_ROOT / "scripts" / "start.sh")]

    async def exercise_client(self, params: StdioServerParameters, timeout: float = 90) -> None:
        async with asyncio.timeout(timeout):
            async with Client(stdio_client(params)) as client:
                tools = await client.list_tools()
                self.assertEqual([tool.name for tool in tools.tools], ["hub__status", "fake__echo_unicode"])
                self.assertTrue(tools.tools[0].annotations.read_only_hint)
                status = await client.call_tool("hub__status", {})
                self.assertFalse(status.is_error)
                self.assertEqual(json.loads(status.content[0].text)["children"]["fake-child"], "connected")

                prompts = await client.list_prompts()
                self.assertEqual([prompt.name for prompt in prompts.prompts], ["fake-child"])
                prompt = await client.get_prompt("fake-child", {"task": "ping"})
                prompt_text = prompt.messages[0].content.text
                self.assertIn("Use only these mcp-hub tools", prompt_text)
                self.assertIn("Task: ping", prompt_text)

                result = await client.call_tool("fake__echo_unicode", {"text": "mcp-hub"})
                self.assertFalse(result.is_error)
                self.assertEqual(result.content[0].text, "naïve café 🌺 mcp-hub")


if __name__ == "__main__":
    unittest.main()
