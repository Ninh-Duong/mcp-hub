import json
import math
import unittest

from mcp_hub.config import HUB_ROOT


class ConfigExamplesTest(unittest.TestCase):
    def test_every_example_config_is_valid_json_object(self) -> None:
        paths = sorted((HUB_ROOT / "config").rglob("*.example.json"))
        self.assertGreaterEqual(len(paths), 2)
        for path in paths:
            with self.subTest(path=path.relative_to(HUB_ROOT)):
                self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)

    def test_every_child_config_example_has_valid_launch_fields(self) -> None:
        paths = sorted((HUB_ROOT / "config" / "mcps").glob("*.example.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                config = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(config.get("transport"), "stdio")
                self.assertIsInstance(config.get("command"), str)
                self.assertTrue(config["command"].strip())
                self.assertIsInstance(config.get("cwd"), str)
                self.assertTrue(config["cwd"].strip())
                self.assertIsInstance(config.get("args", []), list)
                self.assertTrue(all(isinstance(arg, str) for arg in config.get("args", [])))
                self.assertIsInstance(config.get("env", {}), dict)
                self.assertTrue(all(isinstance(key, str) and isinstance(value, str) for key, value in config.get("env", {}).items()))
                self.assertIsInstance(config.get("inheritEnv", []), list)
                self.assertTrue(all(isinstance(name, str) for name in config.get("inheritEnv", [])))
                timeout = config.get("timeoutSeconds", 60)
                self.assertIsInstance(timeout, (int, float))
                self.assertNotIsInstance(timeout, bool)
                self.assertTrue(math.isfinite(timeout) and timeout > 0)

    def test_hub_example_has_only_disabled_routes_to_known_servers(self) -> None:
        config = json.loads((HUB_ROOT / "config" / "hub.example.json").read_text(encoding="utf-8"))
        self.assertEqual(type(config.get("version")), int)
        self.assertEqual(config["version"], 1)
        self.assertIsInstance(config.get("servers"), dict)
        self.assertIsInstance(config.get("features"), dict)
        self.assertTrue(all(item.get("enabled") is False for item in config["servers"].values()))
        for feature in config["features"].values():
            self.assertIs(feature.get("enabled"), False)
            for tool in feature.get("tools", []):
                self.assertIn(tool.get("server"), config["servers"])


if __name__ == "__main__":
    unittest.main()
