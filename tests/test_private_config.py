import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import private_config
from mcp_hub.config import SECRET_PLACEHOLDER


class PrivateConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.mcps = self.tmp / "mcps"
        self.mcps.mkdir()
        self.path = self.mcps / "child.json"
        self.path.write_text('{"credentials": {}}\n', encoding="utf-8")
        self.root_patch = patch.object(private_config, "PRIVATE_ROOT", self.tmp)
        self.root_patch.start()

    def tearDown(self) -> None:
        self.root_patch.stop()
        for path in self.tmp.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(self.tmp.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
        self.tmp.rmdir()

    def test_set_rejects_sensitive_parent_path(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            private_config.main(["set", "child", "credentials.value", json.dumps("test-secret-value")])
        self.assertIn(SECRET_PLACEHOLDER, str(caught.exception))
        self.assertNotIn("test-secret-value", self.path.read_text(encoding="utf-8"))

    def test_set_rejects_api_key_leaf(self) -> None:
        with self.assertRaises(SystemExit):
            private_config.main(["set", "child", "api_key", json.dumps("test-api-key-value")])
        self.assertNotIn("test-api-key-value", self.path.read_text(encoding="utf-8"))

    def test_keys_does_not_print_nested_secret_values(self) -> None:
        self.path.write_text(json.dumps({"credentials": {"value": "test-secret-value"}}), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            private_config.main(["keys", "child"])
        self.assertNotIn("test-secret-value", output.getvalue())
        self.assertIn("<set>", output.getvalue())

    def test_agent_status_reports_models_without_printing_api_keys(self) -> None:
        agents = self.tmp / "agents.json"
        agents.write_text(json.dumps({
            "version": 1,
            "agents": {
                "coordinator": {
                    "provider": "openai",
                    "model": "example-model",
                    "api_key": "test-only-secret-value",
                },
            },
        }), encoding="utf-8")
        from unittest.mock import patch
        output = io.StringIO()
        with patch.object(private_config, "PRIVATE_ROOT", self.tmp), contextlib.redirect_stdout(output):
            private_config.main(["agent-status"])

        self.assertIn("coordinator: provider=openai, model=example-model, state=configured", output.getvalue())
        self.assertNotIn("test-only-secret-value", output.getvalue())

    def test_project_status_reports_aliases_and_envs_without_local_roots(self) -> None:
        projects = self.tmp / "projects.json"
        projects.write_text(json.dumps({
            "version": 1,
            "projects": {"orders": {"default_env": "dev", "allowed_envs": ["dev", "stg"]}},
        }), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            private_config.main(["project-status"])
        self.assertIn("orders: default_env=dev, allowed_envs=dev,stg", output.getvalue())
        self.assertNotIn(str(self.tmp), output.getvalue())


if __name__ == "__main__":
    unittest.main()
