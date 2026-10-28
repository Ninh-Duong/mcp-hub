import logging
import json
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from mcp.types import CallToolRequestParams

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.server import HubRuntime


class InheritedEnvironmentRedactionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.logger = logging.getLogger("mcp_hub.audit")
        self.original_handlers = list(self.logger.handlers)
        for handler in self.original_handlers:
            self.logger.removeHandler(handler)
        self.original_propagate = self.logger.propagate

    def tearDown(self) -> None:
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            handler.close()
        for handler in self.original_handlers:
            self.logger.addHandler(handler)
        self.logger.propagate = self.original_propagate
        for path in self.tmp.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(self.tmp.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
        self.tmp.rmdir()

    async def test_inherited_environment_value_is_redacted_from_child_error(self) -> None:
        env_secret = "test-inherited-token-8274"
        config_secret = "test-nested-credential-6391"
        child = ServerConfig(
            server_id="child",
            command="python",
            args=(),
            cwd=self.tmp,
            env={},
            inherit_env=("MCP_HUB_TEST_API_KEY",),
            secret_values=(config_secret,),
        )
        config = HubConfig(
            config_path=self.tmp / "hub.json",
            servers={"child": child},
            routes=(ToolRoute("feature", "child", "tool", "child__tool"),),
        )
        audit = AuditLog(self.tmp / "audit.jsonl")
        runtime = HubRuntime(config, audit)
        runtime._connect_server = AsyncMock(side_effect=ValueError(f"child echoed {env_secret} and {config_secret}"))

        with patch.dict("os.environ", {"MCP_HUB_TEST_API_KEY": env_secret}):
            async with AsyncExitStack() as stack:
                await runtime.connect(stack)
        self.assertEqual([tool.name for tool in runtime.public_tools], ["hub__status"])

        log = (self.tmp / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(env_secret, log)
        self.assertNotIn(config_secret, log)
        self.assertEqual(log.count("[REDACTED]"), 2)

    async def test_status_reports_detected_and_unavailable_children(self) -> None:
        config = HubConfig(self.tmp / "hub.json", {}, (), ("mcp-jira-sync",), {"mcp-jira-sync": "credentials required"})
        runtime = HubRuntime(config, AuditLog(self.tmp / "audit.jsonl"))
        async with AsyncExitStack() as stack:
            await runtime.connect(stack)
        result = await runtime.call_tool(SimpleNamespace(request_id="status"), CallToolRequestParams(name="hub__status", arguments={}))
        self.assertEqual(json.loads(result.content[0].text), {
            "detected": 1, "connected": 0, "children": {"mcp-jira-sync": "credentials required"},
        })
        self.assertIn('"event": "tool.call.succeeded"', (self.tmp / "audit.jsonl").read_text(encoding="utf-8"))


class RedactPatternTest(unittest.TestCase):
    def test_common_credential_shapes_are_redacted(self) -> None:
        from mcp_hub.audit import redact

        samples = {
            "token=plain-value-1": "plain-value-1",
            '{\\"password\\":\\"escaped-value-2\\"}': "escaped-value-2",
            "Cookie: session-value-3": "session-value-3",
            "https://user:url-pass-4@example.com/x": "url-pass-4",
            "key ATATT3xFfGF0abcdefghijklmnopqrstuv": "ATATT3xFfGF0abcdefghijklmnopqrstuv",
            "jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4": "SflKxwRJSMeKKF2QT4",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEpkey5\n-----END RSA PRIVATE KEY-----": "MIIEpkey5",
        }
        for text, secret in samples.items():
            with self.subTest(text=text[:12]):
                self.assertNotIn(secret, redact(text))
        self.assertEqual(redact("max_tokens=100 token_count=5"), "max_tokens=100 token_count=5")


if __name__ == "__main__":
    unittest.main()
