import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_hub import config
from mcp_hub.config import HUB_ROOT, ConfigError, PrepareSpec, RuntimeRequirement, load_config


class LoadConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        # A temporary checkout, so the real config/private/ is never written.
        self.root = Path(tempfile.mkdtemp()).resolve()
        private_root = self.root / "config" / "private"
        self.tmp = private_root / "case"
        (self.tmp / "child" / "bin").mkdir(parents=True)
        self.patches = [patch.object(config, "HUB_ROOT", self.root), patch.object(config, "PRIVATE_ROOT", private_root)]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        shutil.rmtree(self.root)

    def load_config(self, server: object, *, version: object = 1, features: object | None = None, servers: object | None = None):
        rel = self.tmp.relative_to(self.root).as_posix()
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
        rel = self.tmp.relative_to(self.root).as_posix()
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

    def test_key_suffix_value_is_collected_for_redaction(self) -> None:
        child = self.load(self.server(jira={"ai_context_key": "test-context-key-value", "max_asset_size_mb": 15}))
        self.assertIn("test-context-key-value", child.secret_values)

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

    def test_connect_timeout_defaults_and_is_validated(self) -> None:
        self.assertEqual(self.load(self.server()).connect_timeout_seconds, 120.0)
        self.assertEqual(self.load(self.server(connectTimeoutSeconds=7.5)).connect_timeout_seconds, 7.5)
        for timeout in (True, 0, -1, float("nan"), float("inf"), "30"):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ConfigError, "connectTimeoutSeconds"):
                self.load(self.server(connectTimeoutSeconds=timeout))

    def test_invalid_child_config_fields_are_rejected(self) -> None:
        rel = self.tmp.relative_to(self.root).as_posix()
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
        rel = self.tmp.relative_to(self.root).as_posix()
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

    def load_with_prepare(self, prepare: object):
        rel = self.tmp.relative_to(self.root).as_posix()
        servers = {"child": {"enabled": True, "configFile": f"{rel}/child.json", "prepare": prepare}}
        return self.load(self.server(), servers=servers)

    def test_prepare_is_absent_by_default(self) -> None:
        self.assertIsNone(self.load(self.server()).prepare)

    def test_valid_prepare_is_parsed(self) -> None:
        child = self.load_with_prepare({
            "command": ["node", "start.mjs", "--preflight"],
            "runtime": {"name": "node", "minVersion": "22.1", "winget": "OpenJS.NodeJS.LTS"},
            "timeoutSeconds": 120,
        })
        self.assertEqual(child.prepare, PrepareSpec(
            command=("node", "start.mjs", "--preflight"),
            runtime=RuntimeRequirement("node", (22, 1), "OpenJS.NodeJS.LTS"),
            timeout_seconds=120.0,
        ))

    def test_prepare_without_runtime_uses_default_timeout(self) -> None:
        child = self.load_with_prepare({"command": ["make"]})
        self.assertEqual(child.prepare, PrepareSpec(command=("make",)))
        self.assertEqual(child.prepare.timeout_seconds, 900.0)

    def test_invalid_prepare_command_is_rejected(self) -> None:
        for prepare in ([], {}, {"command": []}, {"command": "node start.mjs"}, {"command": ["node", 1]}, {"command": ["node", " "]}):
            with self.subTest(prepare=prepare), self.assertRaisesRegex(ConfigError, "server child prepare"):
                self.load_with_prepare(prepare)

    def test_invalid_prepare_runtime_is_rejected(self) -> None:
        cases = (
            ({"name": "node", "minVersion": "22"}, "minVersion"),
            ({"name": "node", "minVersion": "v22.0.0"}, "minVersion"),
            ({"name": "node", "minVersion": 22}, "minVersion"),
            ({"name": "node", "minVersion": "22.0.0.1"}, "minVersion"),
            ({"name": " ", "minVersion": "22.0.0"}, "runtime name"),
            ({"name": "node", "minVersion": "22.0.0", "winget": ""}, "runtime winget"),
            ("node", "runtime must be an object"),
        )
        for runtime, error in cases:
            with self.subTest(runtime=runtime), self.assertRaisesRegex(ConfigError, error):
                self.load_with_prepare({"command": ["node"], "runtime": runtime})

    def test_invalid_prepare_timeout_is_rejected(self) -> None:
        for timeout in (True, 0, -5, "60", float("nan"), float("inf"), 10**1000):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ConfigError, "prepare timeoutSeconds"):
                self.load_with_prepare({"command": ["node"], "timeoutSeconds": timeout})

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

    def test_hub_tool_prefix_is_reserved(self) -> None:
        for name in ("hub__status", "hub__approve_job"):
            with self.subTest(name=name), self.assertRaisesRegex(ConfigError, "reserved"):
                self.load(self.server(), features={"f": {"enabled": True, "tools": [
                    {"server": "child", "name": "t", "exposeAs": name},
                ]}})

    def load_route(self, **tool):
        features = {"f": {"enabled": True, "tools": [{"server": "child", "name": "t", **tool}]}}
        return self.load_config(self.server(), features=features).routes[0]

    def test_cache_seconds_defaults_to_zero(self) -> None:
        self.assertEqual(self.load_route().cache_seconds, 0.0)

    def test_valid_cache_seconds_are_parsed(self) -> None:
        for value, expected in ((0, 0.0), (60, 60.0), (2.5, 2.5)):
            with self.subTest(value=value):
                route = self.load_route(cacheSeconds=value)
                self.assertEqual(route.cache_seconds, expected)
                self.assertIsInstance(route.cache_seconds, float)

    def test_invalid_cache_seconds_are_rejected_with_feature_and_tool(self) -> None:
        for value in (-1, True, False, "60", None, float("inf"), float("nan"), 10**400):
            with self.subTest(value=value), self.assertRaisesRegex(ConfigError, "feature f tool t cacheSeconds"):
                self.load_route(cacheSeconds=value)


class DefaultPrepareTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self.tmp.name).resolve()
        self.root = self.parent / "mcp-hub"
        (self.root / "config" / "mcps").mkdir(parents=True)
        (self.root / "config" / "private").mkdir(parents=True)
        source = HUB_ROOT / "config"
        shutil.copy2(source / "hub.example.json", self.root / "config" / "hub.example.json")
        for path in (source / "mcps").glob("mcp-*.example.json"):
            shutil.copy2(path, self.root / "config" / "mcps" / path.name)
        for name in ("mcp-server-logs", "mcp-pr-companion", "mcp-read-source"):
            (self.parent / name).mkdir()
        self.patches = [
            patch.object(config, "HUB_ROOT", self.root),
            patch.object(config, "PRIVATE_ROOT", self.root / "config" / "private"),
            patch.object(config, "_DEFAULT_CONFIG", self.root / "config" / "private" / "hub.json"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_example_prepare_defaults_reach_server_config(self) -> None:
        servers = load_config().servers
        self.assertEqual(servers["mcp-server-logs"].prepare, PrepareSpec(
            command=("node", "start.mjs", "--preflight"),
            runtime=RuntimeRequirement("node", (22, 0, 0), "OpenJS.NodeJS.LTS"),
        ))
        self.assertEqual(servers["mcp-pr-companion"].prepare, PrepareSpec(
            command=("node", "bin/cli.js", "prepare"),
            runtime=RuntimeRequirement("node", (20, 12, 0), "OpenJS.NodeJS.LTS"),
        ))
        self.assertIsNone(servers["mcp-read-source"].prepare)

    def test_private_prepare_replaces_the_default(self) -> None:
        private = {"version": 1, "servers": {"mcp-server-logs": {"prepare": {"command": ["npm", "ci"]}}}, "features": {}}
        (self.root / "config" / "private" / "hub.json").write_text(json.dumps(private), encoding="utf-8")
        self.assertEqual(load_config().servers["mcp-server-logs"].prepare, PrepareSpec(command=("npm", "ci")))


if __name__ == "__main__":
    unittest.main()
