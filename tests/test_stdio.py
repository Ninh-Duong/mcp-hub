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

from mcp_hub.config import HUB_ROOT, PRIVATE_ROOT


class StdioCompatibilityTest(unittest.TestCase):
    def test_documented_launcher_connects_an_agent_to_a_fake_child(self) -> None:
        PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=PRIVATE_ROOT) as temp_dir:
            temp = Path(temp_dir)
            child_path = temp / "child.json"
            hub_path = temp / "hub.json"
            child_path.write_text(json.dumps({
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(HUB_ROOT / "tests" / "fake_stdio_child.py")],
                "cwd": str(HUB_ROOT),
            }), encoding="utf-8")
            hub_path.write_text(json.dumps({
                "version": 1,
                "servers": {"fake-child": {
                    "enabled": True,
                    "configFile": child_path.relative_to(HUB_ROOT).as_posix(),
                }},
                "features": {"fake-feature": {
                    "enabled": True,
                    "tools": [{"server": "fake-child", "name": "echo_unicode", "exposeAs": "fake__echo_unicode"}],
                }},
            }), encoding="utf-8")

            command, args = self.launcher()
            env = os.environ.copy()
            env.pop("VIRTUAL_ENV", None)
            env["MCP_HUB_CONFIG"] = str(hub_path)
            env["UV_PROJECT_ENVIRONMENT"] = str(temp / "clean-environment")
            params = StdioServerParameters(command=command, args=args, cwd=str(HUB_ROOT), env=env)
            asyncio.run(self.exercise_client(params))
            self.assertTrue(Path(env["UV_PROJECT_ENVIRONMENT"]).is_dir())

    @staticmethod
    def launcher() -> tuple[str, list[str]]:
        if os.name == "nt":
            return "powershell", [
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HUB_ROOT / "scripts" / "start.ps1"),
            ]
        return "bash", [str(HUB_ROOT / "scripts" / "start.sh")]

    async def exercise_client(self, params: StdioServerParameters) -> None:
        async with asyncio.timeout(90):
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
                self.assertEqual(result.content[0].text, "Xin chào 🌺 mcp-hub")


if __name__ == "__main__":
    unittest.main()
