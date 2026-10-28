from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class AgentConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        from mcp_hub.agents.config import AGENT_ROLES

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "agents.json"
        self.roles = AGENT_ROLES

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_loads_each_supported_provider_without_repr_leaking_key(self) -> None:
        from mcp_hub.agents.config import load_agent_configs

        provider_map = dict(zip(self.roles, ("openai", "gemini", "anthropic", "openai", "gemini")))
        self.path.write_text(json.dumps({
            "version": 1,
            "agents": {
                role: {"provider": provider, "model": "example-model", "api_key": "test-only-key-value"}
                for role, provider in provider_map.items()
            },
        }), encoding="utf-8")
        configs = load_agent_configs(path=self.path, private_root=self.root)

        self.assertEqual({item.provider for item in configs.values()}, {"openai", "gemini", "anthropic"})
        self.assertTrue(all(item.configured for item in configs.values()))
        self.assertNotIn("test-only-key-value", repr(configs["coordinator"]))

    def test_missing_key_or_model_leaves_only_that_role_unconfigured(self) -> None:
        from mcp_hub.agents.config import load_agent_configs

        self.path.write_text(json.dumps({
            "version": 1,
            "agents": {
                "coordinator": {"provider": "openai", "model": "example-model", "api_key": "<SET_BY_USER>"},
                "logs": {"provider": "anthropic", "model": "", "api_key": "configured-key"},
            },
        }), encoding="utf-8")
        configs = load_agent_configs(path=self.path, private_root=self.root)

        self.assertFalse(configs["coordinator"].configured)
        self.assertFalse(configs["logs"].configured)
        self.assertFalse(configs["source"].configured)

    def test_rejects_unknown_role_provider_and_path_outside_private_root(self) -> None:
        from mcp_hub.agents.config import AgentConfigError, load_agent_configs

        self.path.write_text(json.dumps({"version": 1, "agents": {"writer": {}}}), encoding="utf-8")
        with self.assertRaises(AgentConfigError):
            load_agent_configs(path=self.path, private_root=self.root)

        self.path.write_text(json.dumps({
            "version": 1,
            "agents": {"coordinator": {"provider": "other", "model": "model", "api_key": "key"}},
        }), encoding="utf-8")
        with self.assertRaises(AgentConfigError):
            load_agent_configs(path=self.path, private_root=self.root)

        with self.assertRaises(AgentConfigError):
            load_agent_configs(path=self.root.parent / "outside.json", private_root=self.root)


if __name__ == "__main__":
    unittest.main()
