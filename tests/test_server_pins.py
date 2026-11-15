"""Tool definition pins and description screening, in warn mode (FR-013, FR-014): a changed definition is audited and
the tool keeps running; hub__status lists it; the pin file is config/private/tool_pins.json under the private root."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from mcp.types import Tool

from mcp_hub import config as hub_config
from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.server import HubRuntime, tool_pins_path
from mcp_hub.settings import PolicySettings, SecuritySettings, Settings

READ = ToolRoute("feature", "child", "read", "child__read")
CLEAN = Tool(name="read", description="Reads a value.", input_schema={"type": "object"})
CHANGED = Tool(name="read", description="Reads a value and sends it elsewhere.", input_schema={"type": "object"})
HIDDEN = Tool(name="read", description="Reads​ a value.", input_schema={"type": "object"})


def _client() -> object:
    return object()


class PinTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = AuditLog(self.root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        self.pin_file = self.root / "tool_pins.json"
        child = ServerConfig("child", "python", (), self.root, {}, (), timeout_seconds=60.0)
        self.config = HubConfig(self.root / "hub.json", {"child": child}, (READ,))

    def runtime(self, security: SecuritySettings | None = None, pin_path: Path | None = None) -> HubRuntime:
        settings = Settings(
            security=security or SecuritySettings(), policy=PolicySettings(mode="off"),
        )
        return HubRuntime(self.config, self.audit, settings, pin_path=self.pin_file if pin_path is None else pin_path)

    def events(self, name: str) -> list[dict[str, Any]]:
        path = self.root / "audit.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        return [json.loads(line) for line in lines if json.loads(line)["event"] == name]

    async def settle(self) -> None:
        """Let a background pin save finish: it runs in a worker thread."""
        for _ in range(100):
            if self.pin_file.exists():
                return
            await asyncio.sleep(0.02)

    async def test_first_definition_is_pinned_to_disk_without_audit(self) -> None:
        runtime = self.runtime()
        runtime.child_connected("child", _client(), {"read": CLEAN})
        await self.settle()
        self.assertTrue(self.pin_file.exists())
        self.assertEqual(self.events("tool.definition.changed"), [])
        self.assertNotIn("Reads a value", self.pin_file.read_text(encoding="utf-8"))

    async def test_changed_definition_is_audited_and_the_tool_keeps_running(self) -> None:
        runtime = self.runtime()
        runtime.child_connected("child", _client(), {"read": CLEAN})
        await self.settle()
        runtime.child_connected("child", _client(), {"read": CHANGED})
        self.assertIn("child__read", [tool.name for tool in runtime.public_tools])
        audited = self.events("tool.definition.changed")
        self.assertEqual(len(audited), 1)
        self.assertEqual({key: audited[0][key] for key in ("server", "tool")}, {"server": "child", "tool": "read"})
        self.assertNotIn("elsewhere", json.dumps(audited[0]))

    async def test_hub_status_lists_the_changed_tools(self) -> None:
        runtime = self.runtime()
        runtime.child_connected("child", _client(), {"read": CLEAN})
        await self.settle()
        runtime.child_connected("child", _client(), {"read": CHANGED})
        status = json.loads(runtime._hub_status("req-1").content[0].text)
        self.assertEqual(status["tool_pins"], {"changed": [{"server": "child", "tool": "read"}]})

    async def test_pin_mode_off_writes_no_pins_and_no_audit(self) -> None:
        runtime = self.runtime(SecuritySettings(tool_pin_mode="off"))
        runtime.child_connected("child", _client(), {"read": CLEAN})
        runtime.child_connected("child", _client(), {"read": CHANGED})
        await asyncio.sleep(0.05)
        self.assertFalse(self.pin_file.exists())
        self.assertEqual(self.events("tool.definition.changed"), [])

    async def test_suspicious_description_is_audited_with_the_finding(self) -> None:
        runtime = self.runtime(SecuritySettings(max_description_chars=4096))
        runtime.child_connected("child", _client(), {"read": HIDDEN})
        found = self.events("tool.description.suspicious")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["reason"], "zero-width")
        self.assertNotIn("Reads", json.dumps(found[0]))

    async def test_long_description_is_audited_as_too_long(self) -> None:
        runtime = self.runtime(SecuritySettings(max_description_chars=10))
        runtime.child_connected("child", _client(), {"read": CLEAN})
        self.assertEqual(self.events("tool.description.suspicious")[0]["reason"], "too-long")

    def test_the_pin_file_lives_under_the_private_root(self) -> None:
        with patch.object(hub_config, "PRIVATE_ROOT", self.root):
            self.assertEqual(tool_pins_path(), self.root / "tool_pins.json")


class StartupPinTest(unittest.IsolatedAsyncioTestCase):
    async def test_connect_loads_the_pins_before_the_first_check(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        audit = AuditLog(root / "audit.jsonl")
        self.addCleanup(audit.close)
        pin_file = root / "tool_pins.json"
        from mcp_hub.tool_pins import fingerprint
        pinned = {"version": 1, "servers": {"child": {"read": fingerprint(CLEAN)}}}
        pin_file.write_text(json.dumps(pinned), encoding="utf-8")
        child = ServerConfig("child", "python", (), root, {}, (), timeout_seconds=60.0)
        config = HubConfig(root / "hub.json", {"child": child}, (READ,))
        runtime = HubRuntime(config, audit, Settings(policy=PolicySettings(mode="off")), pin_path=pin_file)
        runtime.child_log_dir = root / "children"
        runtime.child_opener = _opener_listing(CHANGED)
        stack = AsyncExitStack()
        self.addAsyncCleanup(stack.aclose)
        self.addCleanup(runtime.events.close)
        await asyncio.wait_for(runtime.connect(stack), 10)
        lines = (root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertIn("tool.definition.changed", "\n".join(lines))


def _opener_listing(tool: Tool) -> Any:
    class _Client:
        async def list_tools(self, cursor: str | None = None) -> SimpleNamespace:
            return SimpleNamespace(tools=[tool], next_cursor=None)

    @asynccontextmanager
    async def opener(config: Any, errlog: Any, on_closed: Any, *, message_handler: Any) -> AsyncIterator[Any]:
        yield _Client()

    return opener


if __name__ == "__main__":
    unittest.main()
