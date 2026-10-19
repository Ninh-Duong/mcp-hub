import json
import shutil
import tempfile
import unittest
from pathlib import Path

from mcp_hub.config import HUB_ROOT, PRIVATE_ROOT, ConfigError, load_config


class LoadConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(dir=PRIVATE_ROOT))
        (self.tmp / "child" / "bin").mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def load_config(self, server: object, *, version: object = 1, features: object | None = None, servers: object | None = None):
        rel = self.tmp.relative_to(HUB_ROOT).as_posix()
        (self.tmp / "child.json").write_text(json.dumps(server), encoding="utf-8")
        hub = {
            "version": version,
            "servers": servers if servers is not None else {"child": {"enabled": True, "configFile": f"{rel}/child.json"}},
            "features": features if features is not None else {"f": {"enabled": True, "tools": [{"server": "child", "name": "t"}]}},
        }
        (self.tmp / "hub.json").write_text(json.dumps(hub), encoding="utf-8")
        return load_config(self.tmp / "hub.json")

    def load(self, server: object, **kwargs):
        return self.load_config(server, **kwargs).servers["child"]

    def server(self, **extra) -> dict:
        rel = self.tmp.relative_to(HUB_ROOT).as_posix()
        return {"transport": "stdio", "command": "bin/run.exe", "cwd": f"{rel}/child", **extra}

    def test_relative_cwd_and_command(self) -> None:
        child = self.load(self.server())
        self.assertEqual(child.cwd, (self.tmp / "child").resolve())
        self.assertEqual(Path(child.command), (self.tmp / "child" / "bin" / "run.exe").resolve())

    def test_bare_command_stays_on_path(self) -> None:
        self.assertEqual(self.load(self.server(command="python")).command, "python")

    def test_unfilled_placeholder_is_rejected_without_value(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            self.load(self.server(env={"API_TOKEN": "<SET_BY_USER>"}))
        self.assertIn("unfilled", str(caught.exception))

    def test_boolean_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "version"):
            self.load(self.server(), version=True)

    def test_non_integer_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "version"):
            self.load(self.server(), version=1.0)

    def test_missing_or_unsupported_version_is_rejected(self) -> None:
        for version in (None, "1", 2):
            with self.subTest(version=version), self.assertRaisesRegex(ConfigError, "version"):
                self.load(self.server(), version=version)

    def test_api_key_value_is_collected_for_redaction(self) -> None:
        child = self.load(self.server(api_key="test-api-key-value"))
        self.assertIn("test-api-key-value", child.secret_values)

    def test_nested_sensitive_parent_collects_all_string_values(self) -> None:
        child = self.load(self.server(credentials={"username": "nested-user-value", "items": ["nested-token-value"]}))
        self.assertIn("nested-user-value", child.secret_values)
        self.assertIn("nested-token-value", child.secret_values)

    def test_invalid_timeout_values_are_rejected(self) -> None:
        for timeout in (True, 0, -1, float("nan"), float("inf"), float("-inf"), 10**1000):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ConfigError, "timeoutSeconds"):
                self.load(self.server(timeoutSeconds=timeout))

    def test_valid_finite_timeout_is_preserved(self) -> None:
        self.assertEqual(self.load(self.server(timeoutSeconds=12.5)).timeout_seconds, 12.5)

    def test_invalid_child_config_fields_are_rejected(self) -> None:
        rel = self.tmp.relative_to(HUB_ROOT).as_posix()
        cases = (
            (self.server(transport="http"), "transport"),
            (self.server(command=" "), "command"),
            (self.server(args="server.py"), "args"),
            (self.server(args=["server.py", 1]), "args"),
            (self.server(cwd=" "), "cwd"),
            (self.server(cwd=f"{rel}/missing"), "cwd"),
            (self.server(env=[]), "env"),
            (self.server(env={"TOKEN": 1}), "env"),
            (self.server(inheritEnv="PATH"), "inheritEnv"),
            (self.server(inheritEnv=["PATH", 1]), "inheritEnv"),
        )
        for child_config, label in cases:
            with self.subTest(label=label, child_config=child_config), self.assertRaisesRegex(ConfigError, label):
                self.load(child_config)

    def test_invalid_hub_and_server_config_shapes_are_rejected(self) -> None:
        rel = self.tmp.relative_to(HUB_ROOT).as_posix()
        server_entry = {"enabled": True, "configFile": f"{rel}/child.json"}
        cases = (
            (self.server(), 1, [], "servers must be an object"),
            (self.server(), 1, {"": server_entry}, "Server IDs must be non-empty"),
            (self.server(), 1, {"child": []}, "server child must be an object"),
            (self.server(), 1, {"child": {"enabled": True, "configFile": "README.md"}}, "inside config/private"),
            (self.server(), 1, {"child": {"enabled": True, "configFile": f"{rel}/missing.json"}}, "does not exist"),
            ([], 1, None, "server child config must be an object"),
        )
        for child_config, version, server_values, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(ConfigError, error):
                self.load(child_config, version=version, servers=server_values)

    def test_invalid_feature_shapes_and_required_route_names_are_rejected(self) -> None:
        cases = (
            ([], "features must be an object"),
            ({"f": []}, "feature f must be an object"),
            ({"f": {"enabled": True, "tools": [{"server": " ", "name": "t"}]}}, "server must be a non-empty string"),
            ({"f": {"enabled": True, "tools": [{"server": "child", "name": " "}]}}, "tool name must be a non-empty string"),
        )
        for features, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(ConfigError, error):
                self.load(self.server(), features=features)

    def test_exposed_name_length_limit_accepts_128_and_rejects_129(self) -> None:
        allowed = "a" * 128
        config = self.load_config(self.server(), features={"f": {"enabled": True, "tools": [
            {"server": "child", "name": "t", "exposeAs": allowed},
        ]}})
        self.assertEqual(config.routes[0].exposed_name, allowed)

        with self.assertRaisesRegex(ConfigError, "Invalid exposed tool name"):
            self.load(self.server(), features={"f": {"enabled": True, "tools": [
                {"server": "child", "name": "t", "exposeAs": "a" * 129},
            ]}})

    def test_config_file_must_be_inside_private_root(self) -> None:
        with self.assertRaisesRegex(ConfigError, "inside config/private"):
            load_config(HUB_ROOT / "config" / "hub.example.json")

    def test_invalid_hub_json_and_non_object_root_are_rejected(self) -> None:
        path = self.tmp / "hub.json"
        path.write_text("not-json", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "Could not read Hub config"):
            load_config(path)

        path.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "Hub config must be an object"):
            load_config(path)

    def test_invalid_tool_routes_are_rejected(self) -> None:
        cases = (
            ({"f": {"enabled": True, "tools": "t"}}, "tools must be a list"),
            ({"f": {"enabled": True, "tools": ["t"]}}, "must be an object"),
            ({"f": {"enabled": True, "tools": [{"server": "unknown", "name": "t"}]}}, "disabled or unknown server"),
            ({"f": {"enabled": True, "tools": [{"server": "child", "name": "t", "exposeAs": "bad name"}]}}, "Invalid exposed tool name"),
            (
                {"f": {"enabled": True, "tools": [
                    {"server": "child", "name": "t", "exposeAs": "same"},
                    {"server": "child", "name": "other", "exposeAs": "same"},
                ]}},
                "Duplicate exposed tool name",
            ),
            ({"f": {"enabled": True, "tools": []}}, "Enable at least one feature"),
        )
        for features, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(ConfigError, error):
                self.load(self.server(), features=features)


if __name__ == "__main__":
    unittest.main()
