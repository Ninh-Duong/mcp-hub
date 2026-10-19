import logging
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
            with self.assertRaisesRegex(RuntimeError, "No configured tool"):
                async with AsyncExitStack() as stack:
                    await runtime.connect(stack)

        log = (self.tmp / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(env_secret, log)
        self.assertNotIn(config_secret, log)
        self.assertEqual(log.count("[REDACTED]"), 2)


if __name__ == "__main__":
    unittest.main()
